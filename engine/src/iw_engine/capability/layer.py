"""The capability layer - governed, mockable access to the tools. An Adapter is a pure pair:
the tool's `normalize(raw) -> Operation[]` (deterministic, binding-agnostic) plus its DATA
descriptors (`provider`, `intents`, `effect`, `binding`). The live fetch is the ONE
side-effecting seam and lives behind `Source` (see `sources.py`) - swapped for a fixture
loader to mock (DESIGN §2.5 R-K1).

The layer resolves an intent to its adapter, enforces the read/write boundary (writes only in
an approved gate - the human-approval invariant), and records every invocation for the audit
trail. `serve()` is the collapsed, GATE-FIRST path (VALIDATION-VERDICT §C.3 / §D): resolve →
gate → transport.fetch(binding, ...) → normalize, so a blocked write never reaches the
transport (fixing the write-before-gate bug). `invoke()` is the same governance over a
PRE-FETCHED raw payload (the seam unit tests feed directly).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import Binding, Effect, Species
from ..domain.operations import Operation
from .registry import CapabilityRegistry, Policy
from .sources import (
    McpSource,
    MockSource,
    ProviderRoutedSource,
    RestSource,
    RoutedSource,
    ScenarioSource,
    Source,
)

__all__ = [
    "Adapter",
    "CapabilityCall",
    "CapabilityLayer",
    "Invocation",
    "McpSource",
    "MockSource",
    # ProviderRoutedSource is the LIVE router (route by provider, arity 9); RoutedSource is the
    # demoted single-endpoint back-compat router — both re-exported here so this surface AGREES
    # with capability/__init__.py on what the live seam is (M21).
    "ProviderRoutedSource",
    "RestSource",
    "RoutedSource",
    "ScenarioSource",
    "Source",
]


def _summarize_ops(ops: list[Operation]) -> str:
    """A one-line, human-readable summary of what a tool call folded into the graph - the 'out'
    side of the trace (e.g. '2 entities · 12 facts · 1 change'). Adapters emit the AddAssertion
    ATOM natively (P1b), so the fact/event counts are read off AddAssertion BY SPECIES (EVENT →
    events; STATE/DESCRIPTOR/READING/IDENTITY → facts) - the pre-P1b version counted the retired
    AddFact/AddEvent class names, so the dominant assertion-bearing read summarised as 'no new
    data'. Legacy AddFact/AddEvent (the live planner's shim parse target) are still counted for
    back-compat."""
    from collections import Counter
    c = Counter(type(o).__name__ for o in ops)
    n_facts = c.get("AddFact", 0)
    n_events = c.get("AddEvent", 0)
    for o in ops:
        if type(o).__name__ == "AddAssertion":
            if getattr(o, "species", None) == Species.EVENT:
                n_events += 1
            else:
                n_facts += 1
    parts: list[str] = []
    n = c.get("AddNode", 0)
    if n:
        parts.append(f"{n} entit{'y' if n == 1 else 'ies'}")
    if n_facts:
        parts.append(f"{n_facts} fact{'s' if n_facts != 1 else ''}")
    if n_events:
        parts.append(f"{n_events} event{'s' if n_events != 1 else ''}")
    if c.get("AddEdge"):
        parts.append(f"{c['AddEdge']} link{'s' if c['AddEdge'] != 1 else ''}")
    if c.get("ProposeHypothesis"):
        parts.append(f"{c['ProposeHypothesis']} hypothesis")
    if c.get("UpdateHypothesis"):
        parts.append(f"{c['UpdateHypothesis']} hypothesis update{'s' if c['UpdateHypothesis'] != 1 else ''}")
    return " · ".join(parts) or "no new data"


def _served_by(source: Source | None) -> str | None:
    """The concrete transport that SERVED a call (M1): `MockSource`→'mock', `ScenarioSource`→
    'scenario', `McpSource`→'mcp', `RestSource`→'rest', the composing routers→'routed'/
    'providerrouted'. None when no transport is wired (the pre-fetched `invoke()` path). This is
    the one fact that distinguishes a mock read from a live one on the audit record."""
    if source is None:
        return None
    return type(source).__name__.removesuffix("Source").lower() or None


class CapabilityCall(BaseModel):
    """A capability the planner wants invoked (JUDGMENT chooses the intent; the layer
    resolves it to a tool and produces typed data ops)."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    params: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityMeta:
    """The small, self-describing metadata each capability carries. The tool catalogue the reasoner
    sees is rendered FROM this - nothing about a specific tool is hardcoded in the engine or the
    prompt, so adding a capability makes the reasoner aware of it automatically. `queries_by` is the
    load-bearing field: it names the target identifier the tool needs (AppD by `app_id`, git by
    `repo`, the platform by `k8s_workload`, most telemetry by `service_name`), which is how the
    reasoner knows to resolve that id off the incident's CI and pass it - the identity backbone of a
    real cross-tool investigation. The tool CATALOGUE the reasoner sees is data (this metadata), so
    adding a capability needs no engine/prompt edit; onboarding a NEW vendor is still a small,
    CONTAINED code change, not pure config - one adapter with a tool-specific `normalize()` (+ an
    optional `mapping.py` translator) plus entries in the closed `Source` enum and the
    `source_reliability` / `clock_skew_bound_s` maps. A small adapter, not zero code."""

    summary: str          # one line: what this capability is FOR
    queries_by: str       # the target identifier it needs: app_id | repo | service_name | topic_id | fqdn | change_id
    returns: str = ""     # what it contributes to the graph (optional, for the catalogue)


@runtime_checkable
class Adapter(Protocol):
    provider: str
    intents: frozenset[str]
    effect: Effect
    binding: Binding
    meta: CapabilityMeta

    def normalize(self, raw: dict) -> list[Operation]: ...


class Invocation(BaseModel):
    """One audit record of a capability call."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    provider: str
    effect: Effect
    op_count: int
    blocked: bool = False
    reason: str | None = None
    # The boundary outcome - the load-bearing honesty distinction (part4-capability §4):
    #   data        - the tool returned facts that folded into the graph (op_count > 0)
    #   clean-empty - an HONEST no-data read (the provider answered, nothing to fold): this
    #                 CAN feed the nochange/refutation path (R-P2 NoEvidence with a basis)
    #   error       - a transport/normalize failure: NO evidentiary weight, it must NOT be
    #                 read as refuting evidence (this is the fix for fabricated negative evidence)
    #   blocked     - the gate denied the call (unknown intent or ungated write)
    # A downstream reader keys off THIS, never off `op_count == 0` alone (which conflates
    # clean-empty with error - the silent-empty poison this field exists to kill).
    outcome: str = "data"
    # what went IN (the query the reasoner issued) and a one-line summary of what came OUT
    # (what the tool folded into the graph) - so the UI reads like a real agent trace: query + result.
    params: dict = Field(default_factory=dict)
    summary: str = ""
    # agent-trace span (obs 9: "when tool ran, how long, tool vs workflow"). Wall-clock timing,
    # stamped by the engine around serve() - ephemeral (never journaled, never in export_bundle),
    # so goldens stay deterministic. `kind` distinguishes tool | workflow | llm | handoff.
    kind: str = "tool"
    started_at: str | None = None          # ISO wall-clock when the call began
    duration_ms: float | None = None       # how long the fetch+normalize took
    # transport provenance (M1) - the one fact that tells a MOCK read from a LIVE one on the
    # record. `binding` is the adapter's declared Binding (mcp|rest|a2a — DATA, not a code fork);
    # `served_by` is the concrete transport that actually served it (mock|scenario|mcp|rest).
    # Stamped by serve() (which already reads a.binding); the pre-fetched invoke() seam and a
    # blocked-at-the-gate call leave them None (no transport was reached). Journaled + served +
    # streamed → a UI transport chip. Unlike the ephemeral trace span, these are deterministic,
    # so journaling them keeps goldens stable.
    served_by: str | None = None
    binding: Binding | None = None


class CapabilityLayer:
    def __init__(self, adapters: list[Adapter], source: Source | None = None,
                 registry: CapabilityRegistry | None = None) -> None:
        self.adapters = list(adapters)
        self.source = source
        # OPT-IN governance: with no registry the layer behaves exactly as before (the read/write
        # gate only). With a registry, per-intent effect + allow/ask/deny policy are enforced at
        # this boundary (part4-capability §1-2). Engine-side per-call approval-token binding is
        # deferred - see registry.py.
        self.registry = registry
        self._by_intent: dict[str, Adapter] = {}
        for a in adapters:
            for i in a.intents:
                self._by_intent[i] = a

    def resolve(self, intent: str) -> Adapter | None:
        return self._by_intent.get(intent)

    def effect_for(self, a: Adapter, intent: str) -> Effect:
        """The effect of a SPECIFIC intent - PER-INTENT, not per-adapter (part4-capability §1:
        'effect per-intent, kills the OcpRestartAdapter workaround class'). Resolution order:
        the registry's declared effect (governance-as-data), then an adapter's optional
        `effects: dict[str, Effect]` override, then the adapter's default `effect`. Adapters and
        layers without either behave exactly as before (single effect across the intents set)."""
        if self.registry is not None:
            reg_effect = self.registry.effect_for(intent)
            if reg_effect is not None:
                return reg_effect
        effects = getattr(a, "effects", None)
        if isinstance(effects, dict) and intent in effects:
            return effects[intent]
        return a.effect

    # ── gate + audit (shared by both call paths) ──────────────────────────────
    def _gate(self, a: Adapter | None, intent: str, *, allow_write: bool) -> Invocation | None:
        """Return a blocked Invocation if the call must not proceed, else None. Order: resolve →
        policy (allow/ask/deny, if a registry is wired) → read/write gate."""
        if a is None:
            return Invocation(intent=intent, provider="?", effect=Effect.READ, op_count=0,
                              blocked=True, outcome="blocked",
                              reason=f"no capability for intent '{intent}'")
        effect = self.effect_for(a, intent)
        # policy gate (opt-in) - deny refuses outright; ask is the human gate (an approved gate,
        # signalled here by allow_write, releases it); a NEW/unknown intent lands pending_review
        # → deny, so an unregistered tool call is provably refused, never silently executed.
        if self.registry is not None:
            spec = self.registry.spec_for(intent)
            if spec.policy is Policy.DENY:
                why = ("capability pending review - deny" if spec.pending_review
                       else f"policy: deny for intent '{intent}'")
                return Invocation(intent=intent, provider=a.provider, effect=effect, op_count=0,
                                  blocked=True, outcome="blocked", reason=why)
            if spec.policy is Policy.ASK and not allow_write:
                return Invocation(intent=intent, provider=a.provider, effect=effect, op_count=0,
                                  blocked=True, outcome="blocked",
                                  reason=f"policy: ask - human approval required for '{intent}'")
        if effect == Effect.WRITE and not allow_write:
            # the human-approval invariant: a write cannot execute outside an approved gate
            return Invocation(intent=intent, provider=a.provider, effect=effect, op_count=0,
                              blocked=True, outcome="blocked",
                              reason="write blocked - no approved gate")
        return None

    def _fold(self, a: Adapter, intent: str, raw: dict, params: dict | None = None
              ) -> tuple[list[Operation], Invocation]:
        """Normalize a fetched raw into ops + a truthful Invocation. A normalize() that raises
        is caught here and reported as an `error` outcome (no evidentiary weight) - never a
        crash. Ops that fold to nothing are `clean-empty` (an honest no-data read), which is a
        DIFFERENT thing from an error and downstream may treat as NoEvidence."""
        effect = self.effect_for(a, intent)
        try:
            ops = a.normalize(raw)
        except Exception as exc:  # a bad tool payload must degrade, not crash the session
            return [], self._error_invocation(a, intent, effect, exc, params)
        return ops, Invocation(intent=intent, provider=a.provider, effect=effect,
                               op_count=len(ops), params=dict(params or {}),
                               outcome="data" if ops else "empty",
                               summary=_summarize_ops(ops))

    def _error_invocation(self, a: Adapter | None, intent: str, effect: Effect,
                          exc: BaseException, params: dict | None = None) -> Invocation:
        """A recorded, journalable ERROR - the audit trail survives a vendor 4xx/5xx/timeout or a
        malformed payload. `outcome='error'` marks it as carrying NO evidentiary weight: it must
        never be read as a clean-empty (refuting) result."""
        return Invocation(
            intent=intent, provider=(a.provider if a else "?"), effect=effect, op_count=0,
            blocked=False, outcome="error",
            reason=f"{type(exc).__name__}: {exc}", params=dict(params or {}),
            summary="tool error - no evidentiary weight")

    # ── the two entry points ──────────────────────────────────────────────────
    def invoke(self, intent: str, raw: dict, *, allow_write: bool) -> tuple[list[Operation], Invocation]:
        """Gate + normalize a PRE-FETCHED raw payload."""
        a = self._by_intent.get(intent)
        blocked = self._gate(a, intent, allow_write=allow_write)
        if blocked is not None:
            return [], blocked
        return self._fold(a, intent, raw)

    def serve(self, call: CapabilityCall, *, allow_write: bool) -> tuple[list[Operation], Invocation]:
        """The collapsed, GATE-FIRST path: resolve → gate → transport.fetch(binding, ...) →
        normalize. A blocked write returns before any fetch, so no side-effect ever precedes
        the gate. GUARDED (part4-capability §4): a transport OR normalize failure - a vendor
        4xx/5xx, a timeout, an SSE/JSON parse blow-up, a bad shape - becomes a recorded `error`
        Invocation and degrades the read; it NEVER raises through serve() to crash the session."""
        a = self._by_intent.get(call.intent)
        blocked = self._gate(a, call.intent, allow_write=allow_write)
        if blocked is not None:
            return [], blocked
        # transport provenance (M1): the adapter's declared Binding + the concrete transport that
        # serves this call, stamped on WHICHEVER Invocation comes back (data | empty | error) so
        # the record always shows how the raw was fetched.
        stamp = {"served_by": _served_by(self.source), "binding": a.binding}
        try:
            raw = self.source.fetch(a.binding, call.intent, call.params) if self.source else {}
        except Exception as exc:  # a live transport failure degrades the read, never crashes
            effect = self.effect_for(a, call.intent)
            err = self._error_invocation(a, call.intent, effect, exc, call.params)
            return [], err.model_copy(update=stamp)
        ops, inv = self._fold(a, call.intent, raw, params=call.params)
        return ops, inv.model_copy(update=stamp)
