"""P4c · compile + run the engine (B1/B5/B6) + error handling (E4) — unit tests.

The headline test compiles the playbook into a LangGraph run and drives the full INC-4821 path:
assess → root-cause → (gate pauses before the write phase) → resume → remediation → verify-close →
END, with the shared graph growing across phases. Plus: transient errors retry; a permanent error
under `on_failure: run-remaining` blocks the phase without crashing the run.
"""
from __future__ import annotations


from engine.capability import (
    AdapterRegistry,
    CapabilityLayer,
    CapabilityRegistry,
    MockAdapter,
)
from engine.domain import (
    Access,
    CapabilityPolicy,
    DeclaredCapability,
    Effect,
    PolicyStatus,
    Provider,
    ProviderKind,
    SubjectRef,
)
from engine.graph_runtime import FoldRegistry, IncidentGraph, TopologyFold
from engine.runtime import (
    MultiPhasePlanner,
    PermanentError,
    ScriptedPlanner,
    TransientError,
    compile_run,
    run_phase,
)
from fixtures import inc4821 as fx

_TOPO = {
    "nodes": [{"id": "app:payments-api", "kind": "system", "type": "app", "layer": "app"},
              {"id": "db:payments-ora", "kind": "system", "type": "database", "layer": "database"}],
    "edges": [{"type": "depends_on", "from": "app:payments-api", "to": "db:payments-ora"}],
    "evidence": ["cmdb://payments-api"],
}


def _assess(pb):
    return next(p for p in pb.phases if p.id == "assess")


def build_layer() -> CapabilityLayer:
    r = CapabilityRegistry()
    r.add_provider(Provider(id="cmdb", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="obs", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="bl", kind=ProviderKind.a2a_agent, trusted=True))
    r.sync_capability(DeclaredCapability(id="cmdb__topo", provider="cmdb", effect_hint=Effect.read,
                                         intents=["topology", "incident-source", "change-history",
                                                  "similar-incidents"]))
    r.sync_capability(DeclaredCapability(id="obs__tel", provider="obs", effect_hint=Effect.read,
                                         intents=["telemetry", "metrics", "logs", "traces",
                                                  "layer-deep-dive", "synthetic-replay"]))
    r.register_capability(
        DeclaredCapability(id="bl__failover", provider="bl", effect_hint=Effect.write,
                           intents=["remediation-action", "escalation"]),
        policy=CapabilityPolicy(capability_id="bl__failover", effect=Effect.write,
                                access=Access.ask, status=PolicyStatus.active),
    )
    a = AdapterRegistry()
    a.bind("cmdb", MockAdapter(ProviderKind.mcp_remote, {"cmdb__topo": _TOPO}))
    a.bind("obs", MockAdapter(ProviderKind.mcp_remote, {"obs__tel": {"nodes": [], "evidence": ["obs://y"]}}))
    a.bind("bl", MockAdapter(ProviderKind.a2a_agent, {"bl__failover": {"result": "applied; I/O 28ms→4ms"}}))
    return CapabilityLayer(r, a)


def build_planner() -> MultiPhasePlanner:
    return MultiPhasePlanner({
        "assess": ScriptedPlanner("assess", [("topology", {})], fx.ASSESS_RESULT),
        "root-cause": ScriptedPlanner("root-cause", [("traces", {})], fx.ROOT_CAUSE_RESULT),
        "remediation": ScriptedPlanner("remediation", [("remediation-action", {})], fx.REMEDIATION_RESULT),
        "verify-close": ScriptedPlanner("verify", [("synthetic-replay", {})], fx.VERIFY_RESULT),
    })


def _fold() -> FoldRegistry:
    reg = FoldRegistry()
    reg.register("cmdb", TopologyFold())
    reg.register("obs", TopologyFold())
    return reg


def _init_state() -> dict:
    return {"subject": {"domain": "app-incident", "id": "INC-4821", "kind": "incident"},
            "phase_records": [], "messages": [], "current_phase": "", "status": "running"}


# ── the full run: compile → gate → resume → END (B1/B5/B6) ──────────────
def test_full_run_pauses_at_gate_then_completes(playbook):
    graph = IncidentGraph()
    app = compile_run(playbook, build_planner(), build_layer(), graph, _fold())
    cfg = {"configurable": {"thread_id": "INC-4821"}}

    app.invoke(_init_state(), cfg)                       # runs assess + root-cause, then PAUSES
    paused = app.get_state(cfg)
    assert paused.next == ("remediation",)               # the gate — before the write phase (B5)

    final = app.invoke(None, cfg)                        # operator approves → resume
    phases = [r["phase"] for r in final["phase_records"]]
    assert phases == ["assess", "root-cause", "remediation", "verify-close"]
    assert all(r["state"] == "done" for r in final["phase_records"])
    # the shared graph grew across phases (B2) and the remediation actually invoked the write
    assert "db:payments-ora" in graph.node_ids()
    rem = next(r for r in final["phase_records"] if r["phase"] == "remediation")
    assert rem["steps"][0]["capability"] == "bl__failover"


def test_root_cause_id_is_first_attempt(playbook):
    graph = IncidentGraph()
    app = compile_run(playbook, build_planner(), build_layer(), graph, _fold())
    cfg = {"configurable": {"thread_id": "INC-4821-b"}}
    app.invoke(_init_state(), cfg)
    final = app.invoke(None, cfg)
    rc = next(r for r in final["phase_records"] if r["phase"] == "root-cause")
    assert rc["id"] == "INC-4821:root-cause:1"


# ── error handling (E4) ─────────────────────────────────────────────────
class _FlakyAdapter:
    kind = ProviderKind.mcp_remote

    def __init__(self, fail_times: int, result: dict) -> None:
        self._left = fail_times
        self._result = result

    def invoke(self, capability_id: str, input: dict) -> dict:
        if self._left > 0:
            self._left -= 1
            raise TransientError("temporary glitch")
        return self._result


class _PermAdapter:
    kind = ProviderKind.mcp_remote

    def invoke(self, capability_id: str, input: dict) -> dict:
        raise PermanentError("hard failure")


def _read_layer(provider_id: str, cap_id: str, intents: list[str], adapter) -> CapabilityLayer:
    r = CapabilityRegistry()
    r.add_provider(Provider(id=provider_id, kind=ProviderKind.mcp_remote, trusted=True))
    r.sync_capability(DeclaredCapability(id=cap_id, provider=provider_id, effect_hint=Effect.read,
                                         intents=intents))
    a = AdapterRegistry()
    a.bind(provider_id, adapter)
    return CapabilityLayer(r, a)


def _state():
    return {"subject": SubjectRef(domain="app-incident", id="INC-4821", kind="incident"),
            "graph": IncidentGraph(), "phase_records": []}


def test_transient_error_retries_then_succeeds(playbook):
    layer = _read_layer("obs", "obs__tel", ["telemetry"], _FlakyAdapter(2, {"ok": True}))  # fail 2, succeed on 3rd
    planner = ScriptedPlanner("p", [("telemetry", {})], fx.ASSESS_RESULT)
    rec = run_phase(_state(), _assess(playbook), playbook, planner, layer)
    assert rec.state.value == "done"
    assert rec.steps[0].capability == "obs__tel"


def test_permanent_error_under_run_remaining_blocks(playbook):
    r = CapabilityRegistry()
    r.add_provider(Provider(id="bad", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="obs", kind=ProviderKind.mcp_remote, trusted=True))
    r.sync_capability(DeclaredCapability(id="bad__x", provider="bad", effect_hint=Effect.read,
                                         intents=["telemetry"]))
    r.sync_capability(DeclaredCapability(id="obs__y", provider="obs", effect_hint=Effect.read,
                                         intents=["topology"]))
    a = AdapterRegistry()
    a.bind("bad", _PermAdapter())
    a.bind("obs", MockAdapter(ProviderKind.mcp_remote, {"obs__y": {"ok": True}}))
    layer = CapabilityLayer(r, a)
    planner = ScriptedPlanner("p", [("telemetry", {}), ("topology", {})], fx.ASSESS_RESULT)

    rec = run_phase(_state(), _assess(playbook), playbook, planner, layer)
    assert rec.state.value == "blocked"             # on_failure: run-remaining → partial, not a crash
    assert len(rec.steps) == 2                       # the failed step + the remaining one both recorded
    assert "error" in rec.steps[0].result


def test_permanent_error_recorded_as_failed_step(playbook):
    layer = _read_layer("bad", "bad__x", ["telemetry"], _PermAdapter())
    planner = ScriptedPlanner("p", [("telemetry", {})], fx.ASSESS_RESULT)
    rec = run_phase(_state(), _assess(playbook), playbook, planner, layer)
    # the one action permanently failed → run-remaining leaves the phase blocked with a failed step
    assert rec.state.value == "blocked"
    assert rec.steps[0].note and "permanent failure" in rec.steps[0].note
