"""Scenario registry — makes ALL SIX use cases runnable end-to-end through the interactive
session backend (UI-SPEC §1 "ALL use cases runnable"). Maps an incident id to the scripted
planner + fixture transport for its scenario, so `POST /sessions {subject:{domain,id}}` starts
the matching interactive investigation and `GET /catalog` lists every runnable incident.

The six scenarios (their deterministic `build() -> (subject, script[, fixtures])`) live in
`tests/e2e/scenario_*.py` — the same twins the golden suite drives in batch. Here we reuse
them for the *interactive* run: a `ScriptedPlanner(script)` behind the session's write-gate, a
`MockSource(fixtures)` behind the capability layer, plus a WRITE-effect `RemediationAdapter` so
the playbook's `writes_allowed` phase actually opens a human-in-the-loop gate (the golden scripts
only *propose* a fix as a hypothesis update — the registry injects the matching `apply_remediation`
write call so the operator gets an Approve / Refine / Deny card, exactly the UI-SPEC §2 approval loop).

The scenario twins are authored under `tests/`, so on an editable/source checkout we add that
directory to `sys.path` (mirroring pyproject's `pythonpath = ["src", "tests"]`) and import the
`e2e.*` package. If the tests tree is absent (a bare wheel), the registry is simply empty and
the server still starts.
"""
from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable
from datetime import datetime

from ..capability import (
    CapabilityCall,
    CapabilityLayer,
    MappingSource,
    MockSource,
    ProviderRoutedSource,
    ScenarioSource,
    build_provider_transports,
)
from ..capability.adapters import default_adapters
from ..capability.adapters.remediation import RemediationAdapter
from ..domain import registry
from ..domain.catalog import render_catalog, render_tools, tool_intents
from ..domain.playbook import Playbook
from ..domain.subject import SubjectRef
from .live_fixtures import LIVE_SCENARIOS
from .live_planner import LivePlanner
from .llm_client import make_llm_client
from .loader import load_playbook
from .planner import PlanOutput, ScriptedPlanner
from .session import SessionManager
from .store import InvestigationStore

# ── scenario catalog: one runnable incident per layer (UI-SPEC §1) ─────────────────
# Each entry is the metadata the start selector needs (id + title + layer) plus the human
# remediation the write-gate proposes. `id` is the session identity the UI opens; where a
# scenario twin natively reuses an id (network + nochange are both authored as INC-9001) we give
# it a distinct catalog id so every incident lists + opens independently.
_CATALOG: list[dict] = [
    {"key": "code_regression", "id": "INC-4821", "domain": "app-incident",
     "title": "payments-api 5xx after v4.12.0 deploy", "layer": "Application code",
     "remediation": "Roll payments-api back to v4.11.3 (revert commit abc123)"},
    {"key": "deployment", "id": "INC-7731", "domain": "app-incident",
     "title": "checkout-api CrashLoopBackOff after rev43", "layer": "Deployment",
     "remediation": "Roll the Deployment back from rev43 to rev42 (revert PR #482)"},
    {"key": "network", "id": "INC-9001", "domain": "app-incident",
     "title": "checkout-svc → pricing-svc timeouts after MTU change", "layer": "Network",
     "remediation": "Revert the MTU/uplink change on SEG-EDGE-12 (CHG-77)"},
    {"key": "database", "id": "INC-7734", "domain": "app-incident",
     "title": "orders-api latency after index drop (CHG-9)", "layer": "Database",
     "remediation": "Re-create the dropped index on orders.order_items (roll back CHG-9)"},
    {"key": "firewall", "id": "INC-7702", "domain": "app-incident",
     "title": "fraud-scoring egress blocked by ACL (CHG-3311)", "layer": "Firewall / Security",
     "remediation": "Revert CHG-3311 on FW-EGR-118 (restore the prior egress ACL)"},
    {"key": "nochange", "id": "INC-9100", "domain": "app-incident",
     "title": "checkout-api pool saturation (no change)", "layer": "No-change / Saturation",
     "remediation": "Scale checkout-db's connection pool + add a read replica"},
    {"key": "messaging", "id": "INC-8801", "domain": "app-incident",
     "title": "order-processor consumer lag after CHG-55 deploy", "layer": "Messaging",
     "remediation": "Roll the consumer deploy CHG-55 back to the prior build"},
    {"key": "infra", "id": "INC-8900", "domain": "app-incident",
     "title": "checkout-svc pod evicted — noisy-neighbor batch job", "layer": "Infra",
     "remediation": "Reschedule etl-nightly off the tier-1 node + cap its memory"},
    {"key": "cache", "id": "INC-5500", "domain": "app-incident",
     "title": "product-api latency after cache-client deploy (stampede)", "layer": "Caching",
     "remediation": "Roll product-api back to v3.3.2 (re-enable singleflight)"},
    {"key": "featureflag", "id": "INC-5600", "domain": "app-incident",
     "title": "cart-api 5xx after feature-flag flip (CHG-77)", "layer": "Configuration / Flag",
     "remediation": "Recycle the new-tax-engine flag to 0% rollout"},
    {"key": "certificate", "id": "INC-5700", "domain": "app-incident",
     "title": "auth-svc intermittent 503 — expiring intermediate cert", "layer": "TLS / Certificate",
     "remediation": "Renew the Corp Intermediate CA cert + re-push the auth-svc TLS secret"},
    {"key": "correlated", "id": "INC-6001", "domain": "app-incident",
     "title": "payments platform latency storm — AIOps-correlated DB migration", "layer": "AIOps / Correlation",
     "remediation": "Re-create the dropped index on the payments datastore (roll back CHG-DB-500)"},
]

_BY_ID = {e["id"]: e for e in _CATALOG}


def catalog() -> list[dict]:
    """The runnable incidents for the start selector — id, title, layer, domain (UI-SPEC §1)."""
    return [{"id": e["id"], "title": e["title"], "layer": e["layer"], "domain": e["domain"],
             "kind": "incident"} for e in _CATALOG]


# ── locate + import the scenario twins (authored under tests/) ─────────────────────
def _tests_dir() -> pathlib.Path:
    # .../engine/src/iw_engine/runtime/scenarios.py -> parents[3] == .../engine
    return pathlib.Path(__file__).resolve().parents[3] / "tests"


def _load_builders() -> dict[str, Callable]:
    tests = _tests_dir()
    if tests.is_dir() and str(tests) not in sys.path:
        sys.path.insert(0, str(tests))
    try:
        import importlib
        builders: dict[str, Callable] = {}
        for entry in _CATALOG:
            mod = importlib.import_module(f"e2e.scenario_{entry['key']}")
            builders[entry["id"]] = mod.build
        return builders
    except ModuleNotFoundError:
        return {}   # bare wheel without the tests tree — registry stays empty


# ── the write-gate injection (turns a proposal into an approvable action) ──────────
def _with_gate(script: list[PlanOutput], remediation: str,
               write_phases: set[str]) -> list[PlanOutput]:
    """Return a copy of `script` whose `writes_allowed` phase(s) carry an `apply_remediation`
    WRITE call, so the interactive session suspends there and offers Approve / Refine / Deny.
    Keyed on the playbook's `writes_allowed` role binding (P7 phase-as-data), never a phase
    name. The golden batch path (which calls `build()` directly) never sees this — only the
    session does."""
    out: list[PlanOutput] = []
    for step in script:
        if step.phase in write_phases:
            call = CapabilityCall(intent="apply_remediation",
                                  params={"action": remediation, "reversible": True})
            step = step.model_copy(update={"calls": [*step.calls, call]})
        out.append(step)
    return out


def _layer(fixtures: dict | None) -> CapabilityLayer:
    # default read adapters + the write-effect remediation adapter; the mock transport answers
    # every read from the scenario's fixtures (an unfixtured intent folds to zero ops).
    return CapabilityLayer([*default_adapters(), RemediationAdapter()],
                           source=MockSource(fixtures or {}))


def build_live_layer(env: dict[str, str] | None = None, *,
                     rest_routes: dict[str, dict[str, str]] | None = None,
                     http_mcp=None, http_rest=None) -> CapabilityLayer:
    """The LIVE composition factory — the one-line prod seam symmetric to the mock `_layer(fixtures)`.
    Where `_layer` answers every read from a `MockSource(fixtures)`, this composes the REAL transports
    from the pieces that already exist + are unit-tested, so the "two prod swaps are one-seam" bar is
    met by written glue, not a promise (M20):

      build_provider_transports(bindings, env)   → a `McpSource`/`RestSource` per provider whose
                                                    `IW_CAP_<PROVIDER>_URL`/`_TOKEN` is set in `env`
      → ProviderRoutedSource(intent_provider)    → routes each intent to ITS provider's transport
                                                    (arity-9 by provider, not arity-3 by binding)
      → MappingSource(inner, intent_provider)    → translates each vendor JSON into the adapter shape

    The seam behaves EXACTLY like the mock: an intent whose provider has no `IW_CAP_*` URL routes to
    `{}` (clean-empty — the adapter folds it to zero ops), so this factory COMPOSES AND RUNS
    end-to-end with NO real vendor configured (the mock-equivalent). A real connection is a couple
    of env vars, never new factory code. NOT wired to any real vendor here — this is the composable
    prod seam, exercised on mocks. A2A-bound providers (remediation) have no live transport yet, so
    they route clean-empty — the reserved seam (F2). HTTP clients are injectable for hermetic tests;
    a real run uses the stdlib urllib defaults."""
    adapters = [*default_adapters(), RemediationAdapter()]
    bindings = {a.provider: a.binding for a in adapters}
    intent_provider = {i: a.provider for a in adapters for i in a.intents}
    transports = build_provider_transports(bindings, rest_routes=rest_routes, env=env,
                                           http_mcp=http_mcp, http_rest=http_rest)
    source = MappingSource(ProviderRoutedSource(intent_provider, transports), intent_provider)
    return CapabilityLayer(adapters, source=source)


# ── the SessionManager the server drives ───────────────────────────────────────────
def _default_playbook() -> pathlib.Path:
    import iw_engine
    return pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def build_manager(*, playbook: Playbook | None = None,
                  clock: Callable[[], datetime] | None = None,
                  store: InvestigationStore | None = None) -> SessionManager:
    """A SessionManager wired to the scenario registry — the default backend for the workbench.
    `planner_factory(subject)` replays the incident's scripted plan (with the write-phase
    call injected); `layer_factory(subject)` gives it the fixture-backed capability layer."""
    pb = playbook or load_playbook(_default_playbook())
    builders = _load_builders()
    write_phases = {p.id for p in pb.phases if p.writes_allowed}   # role binding, not a name

    def _built(subject: SubjectRef):
        entry = _BY_ID.get(subject.id)
        build = builders.get(subject.id) if entry else None
        if entry is None or build is None:
            raise KeyError(f"no runnable scenario for incident {subject.id!r} "
                           f"(known: {sorted(_BY_ID)})")
        got = build()
        script = got[1]
        fixtures = got[2] if len(got) > 2 else None
        return _with_gate(script, entry["remediation"], write_phases), fixtures

    def planner_factory(subject: SubjectRef) -> ScriptedPlanner:
        return ScriptedPlanner(_built(subject)[0])

    def layer_factory(subject: SubjectRef) -> CapabilityLayer:
        return _layer(_built(subject)[1])

    return SessionManager(pb, planner_factory, layer_factory=layer_factory, clock=clock,
                          store=store)


# ── the LIVE backend (obs 10: the LLM is the product; mock above is the CI net) ─────
def make_live_client(model: str | None = None):
    """Resolve an LLM client for the live backend. Thin wrapper over `llm_client.make_llm_client`
    (the consolidated factory) — kept so existing callers (`from scenarios import make_live_client`)
    keep working. Selection is xAI-first (XAI_API_KEY), then Gemini, then None; IW_LIVE_PROVIDER
    overrides. See `iw_engine.runtime.llm_client` for the full precedence + how to plug in any LLM."""
    return make_llm_client(model)


def _available_intents(fixtures: dict, adapters) -> set[str]:
    """The concrete READ intents actually wired (fixtured) for this incident — told to the model
    as its 'connected integrations' so it doesn't waste calls on empty tools (GAP 4)."""
    fixtured = set(fixtures)
    return {i for a in adapters if a.provider in fixtured and a.effect.value != "write"
            for i in a.intents}


def live_wired_ids() -> set[str]:
    """Catalog incident ids that have LIVE fixtures wired (can run LLM-driven today)."""
    return {e["id"] for e in _CATALOG if e["key"] in LIVE_SCENARIOS}


def live_build_manager(*, playbook: Playbook | None = None,
                       clock: Callable[[], datetime] | None = None,
                       model: str | None = None, client=None,
                       store: InvestigationStore | None = None) -> SessionManager:
    """A SessionManager whose planner is the REAL LLM (LivePlanner), reusing run_live's wiring:
    a `ScenarioSource` (intent→provider routing) over the shared live fixtures, the same catalog
    + tools prompt, and the write-effect RemediationAdapter so the LLM can open the write
    gate itself. This is the product experience — the engine/reducer/hypothesis store/journal are identical
    to the mock path; only the JUDGMENT author changes. Raises if no LLM key is available."""
    pb = playbook or load_playbook(_default_playbook())
    client = client or make_live_client(model)
    if client is None:
        raise RuntimeError(
            "live backend needs an LLM key: set XAI_API_KEY or ~/.secrets/stock/gemini-api-key.txt")

    catalog_text = render_catalog(registry, pb)
    # show the LLM the write-effect remediation tool too, so it can PROPOSE the fix as an
    # apply_remediation WRITE call in the writes_allowed phase — which opens the human approval
    # gate (the human-in-the-loop invariant). Read-only tools alone would let it silently self-remediate.
    tool_adapters = (*default_adapters(), RemediationAdapter())
    tools_text = render_tools(tool_adapters, include_writes=True)
    key_by_id = {e["id"]: e["key"] for e in _CATALOG}
    # route EVERY intent (incl. the write) to its provider so ScenarioSource can resolve them
    intent_provider = {i: a.provider for a in tool_adapters for i in a.intents}

    def _fixtures_for(subject: SubjectRef) -> dict:
        key = key_by_id.get(subject.id)
        builder = LIVE_SCENARIOS.get(key) if key else None
        if builder is None:
            raise KeyError(f"no LIVE fixtures for incident {subject.id!r} "
                           f"(live-wired: {sorted(live_wired_ids())})")
        return builder()[1]

    def planner_factory(subject: SubjectRef) -> LivePlanner:
        return LivePlanner(client, catalog_text, tools_text,
                           tool_intents(tool_adapters, include_writes=True),
                           available_sources=_available_intents(_fixtures_for(subject),
                                                                default_adapters()),
                           verbose=False)

    def layer_factory(subject: SubjectRef) -> CapabilityLayer:
        source = ScenarioSource(intent_provider, _fixtures_for(subject))
        return CapabilityLayer([*default_adapters(), RemediationAdapter()], source=source)

    # background_drive: a live phase is one or more LLM round-trips (seconds) — drive off the HTTP
    # thread so POST /sessions,/advance,/gate return immediately and the SSE stream shows progress.
    # auto_review=False: this is the INTERACTIVE workbench backend — every phase transition SUSPENDS
    # for the real human's direction approval (owner 2026-07-23). The scripted mock backend
    # (build_manager) keeps the default auto_review=True (a deterministic CI net that never hangs).
    return SessionManager(pb, planner_factory, layer_factory=layer_factory, clock=clock,
                          background_drive=True, auto_review=False, store=store)
