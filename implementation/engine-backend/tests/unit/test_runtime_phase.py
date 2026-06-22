"""P4b · the phase loop (B3) + capability-in-loop (B4) — unit tests.

A ScriptedPlanner drives the Assess phase over mock capabilities; the loop resolves each need to a
governed capability, invokes it, folds the result into the graph, logs Steps, and produces a valid
AssessResult. The guards: a read-only phase can't invoke a write (Denied), a write capability pauses
at the gate (NeedsApproval), an invalid output is rejected, and an operator question pauses.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.capability import (
    AdapterRegistry,
    CapabilityLayer,
    CapabilityRegistry,
    Denied,
    MockAdapter,
    NeedsApproval,
)
from engine.domain import (
    Access,
    AssessResult,
    CapabilityPolicy,
    DeclaredCapability,
    Effect,
    PolicyStatus,
    Provider,
    ProviderKind,
    SubjectRef,
)
from engine.graph_runtime import IncidentGraph, TopologyFold
from engine.runtime import ScriptedPlanner, WaitingInput, run_phase
from fixtures import inc4821 as fx


def _assess_phase(playbook):
    return next(p for p in playbook.phases if p.id == "assess")


def _remediation_phase(playbook):
    return next(p for p in playbook.phases if p.id == "remediation")


def build_layer() -> CapabilityLayer:
    r = CapabilityRegistry()
    r.add_provider(Provider(id="cmdb", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="obs", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="bladelogic", kind=ProviderKind.a2a_agent, trusted=True))
    # read caps for assess needs (synced → DEFAULT allow for trusted read)
    r.sync_capability(DeclaredCapability(id="cmdb__topology", provider="cmdb", effect_hint=Effect.read,
                                         intents=["topology", "incident-source", "similar-incidents"]))
    r.sync_capability(DeclaredCapability(id="obs__telemetry", provider="obs", effect_hint=Effect.read,
                                         intents=["telemetry", "change-history"]))
    # a write cap (gated) for remediation
    r.register_capability(
        DeclaredCapability(id="bladelogic__failover", provider="bladelogic", effect_hint=Effect.write,
                           intents=["remediation-action"]),
        policy=CapabilityPolicy(capability_id="bladelogic__failover", effect=Effect.write,
                                access=Access.ask, status=PolicyStatus.active),
    )
    a = AdapterRegistry()
    a.bind("cmdb", MockAdapter(ProviderKind.mcp_remote, {"cmdb__topology": {
        "nodes": [{"id": "app:payments-api", "kind": "system", "type": "app", "layer": "app"},
                  {"id": "db:payments-ora", "kind": "system", "type": "database", "layer": "database"}],
        "edges": [{"type": "depends_on", "from": "app:payments-api", "to": "db:payments-ora"}],
        "evidence": ["cmdb://payments-api"]}}))
    a.bind("obs", MockAdapter(ProviderKind.mcp_remote, {"obs__telemetry": {
        "nodes": [{"id": "chg:deploy-rev47", "kind": "change", "type": "change"}],
        "evidence": ["deploy://rev47"]}}))
    a.bind("bladelogic", MockAdapter(ProviderKind.a2a_agent, {"bladelogic__failover": {"result": "ok"}}))
    return CapabilityLayer(r, a)


def _fold_registry():
    from engine.graph_runtime import FoldRegistry
    reg = FoldRegistry()
    reg.register("cmdb", TopologyFold())
    reg.register("obs", TopologyFold())
    return reg


def _state(graph=None):
    return {"subject": SubjectRef(domain="app-incident", id="INC-4821", kind="incident"),
            "graph": graph if graph is not None else IncidentGraph(), "phase_records": []}


def test_phase_loop_produces_valid_output_and_steps(playbook):
    layer = build_layer()
    graph = IncidentGraph()
    state = _state(graph)
    planner = ScriptedPlanner(
        "walk topology, pull telemetry",
        actions=[("topology", {"subject": "INC-4821"}), ("telemetry", {"window": "1h"})],
        output=fx.ASSESS_RESULT,
    )
    rec = run_phase(state, _assess_phase(playbook), playbook, planner, layer, _fold_registry())

    assert rec.state.value == "done"
    assert len(rec.steps) == 2
    assert rec.steps[0].capability == "cmdb__topology"
    assert rec.steps[0].touched == ["app:payments-api", "db:payments-ora"]
    assert rec.steps[0].evidence == ["cmdb://payments-api"]
    AssessResult.model_validate(rec.output)                 # the contract holds
    assert {"app:payments-api", "db:payments-ora", "chg:deploy-rev47"} <= set(graph.node_ids())
    assert rec.id == "INC-4821:assess:1"


def test_read_only_phase_cannot_invoke_write(playbook):
    layer = build_layer()
    planner = ScriptedPlanner("try a write", actions=[("remediation-action", {})], output=fx.ASSESS_RESULT)
    with pytest.raises(Denied):
        run_phase(_state(), _assess_phase(playbook), playbook, planner, layer, _fold_registry())


def test_write_capability_pauses_at_the_gate(playbook):
    layer = build_layer()
    planner = ScriptedPlanner("apply fix", actions=[("remediation-action", {})], output=fx.REMEDIATION_RESULT)
    with pytest.raises(NeedsApproval):
        run_phase(_state(), _remediation_phase(playbook), playbook, planner, layer, _fold_registry())


def test_invalid_output_is_rejected(playbook):
    layer = build_layer()
    planner = ScriptedPlanner("incomplete", actions=[("topology", {})], output={"symptom": "only this"})
    with pytest.raises(ValidationError):
        run_phase(_state(), _assess_phase(playbook), playbook, planner, layer, _fold_registry())


def test_planner_can_pause_for_operator(playbook):
    layer = build_layer()
    planner = ScriptedPlanner("ask first", actions=[("topology", {})], output=fx.ASSESS_RESULT,
                              ask_operator_after=0)
    with pytest.raises(WaitingInput):
        run_phase(_state(), _assess_phase(playbook), playbook, planner, layer, _fold_registry())
