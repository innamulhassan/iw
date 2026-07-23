"""F1 — the TO-DO LAYER (planner side). A plan reads as a CHECKLIST of to-dos: each a short
objective grouping the capability CALLS + direct OPS that serve it. The layer is ADDITIVE and an
ATTRIBUTION view only — {calls, ops} always flatten back to the engine's UNCHANGED 1:1 execution
loop, and each call/op attributes to its to-do by position. These pin that contract:

  - a plan that authors NO to-dos still reads as a single-item checklist (the scripted default);
  - a plan that authors to-dos makes them AUTHORITATIVE — the flat lists become their exact
    concatenation, so execution is byte-identical and attribution is exact;
  - the derivation survives a post-construction `model_copy` of calls (the session write-gate path).
"""
from __future__ import annotations

from iw_engine.capability.layer import CapabilityCall
from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import ConfidenceLevel, NodeType, VerdictStatus
from iw_engine.domain.operations import AddNode, ProposeHypothesis
from iw_engine.domain.phase_result import PhaseVerdict
from iw_engine.runtime.planner import PlanOutput, Todo, TodoStatus


def _verdict() -> PhaseVerdict:
    return PhaseVerdict(status=VerdictStatus.ADVANCE, confidence=Confidence(value=0.9, basis="t"))


def _call(intent: str) -> CapabilityCall:
    return CapabilityCall(intent=intent, params={})


def _node(sn: str) -> AddNode:
    return AddNode(type=NodeType.SERVICE, props={"service_name": sn, "env": "prod"})


def _propose(h: str) -> ProposeHypothesis:
    return ProposeHypothesis(hid=h, statement=f"theory {h}", confidence_level=ConfidenceLevel.MED)


# ── the scripted default: no authored to-dos → a single-item checklist ─────────────────
def test_flat_plan_synthesizes_one_todo_wrapping_its_calls_and_ops():
    plan = PlanOutput(phase="frame", calls=[_call("get_dependencies"), _call("active_alerts")],
                      ops=[_node("pay"), _propose("h1")], narrative="frame the symptom",
                      verdict=_verdict())
    # the flat lists are untouched (the engine reads THESE, 1:1 execution unchanged)
    assert [c.intent for c in plan.calls] == ["get_dependencies", "active_alerts"]
    assert [type(o).__name__ for o in plan.ops] == ["AddNode", "ProposeHypothesis"]
    # the plan reads as ONE to-do: objective = the narrative, all calls + ops under it, PENDING
    todos = plan.effective_todos
    assert len(todos) == 1
    assert todos[0].objective == "frame the symptom"
    assert todos[0].status is TodoStatus.PENDING
    assert [c.intent for c in todos[0].calls] == ["get_dependencies", "active_alerts"]
    assert len(todos[0].ops) == 2
    # every call + op attributes to to-do 0
    assert plan.call_todo_indices() == [0, 0]
    assert plan.op_todo_indices() == [0, 0]


def test_empty_plan_has_no_todos():
    """A phase that authors nothing (e.g. close: no calls, no ops) is an empty checklist —
    synthesis fires only when there is something to do."""
    plan = PlanOutput(phase="close", narrative="postmortem", verdict=_verdict())
    assert plan.effective_todos == []
    assert plan.call_todo_indices() == [] and plan.op_todo_indices() == []


# ── authored to-dos are authoritative: the flat lists become their exact flattening ────
def test_authored_todos_pin_the_flat_lists_and_attribution():
    td0 = Todo(objective="map the topology", calls=[_call("get_dependencies")], ops=[_node("pay")])
    td1 = Todo(objective="frame the two rivals", calls=[_call("active_alerts")],
               ops=[_propose("h1"), _propose("h2")])
    plan = PlanOutput(phase="investigate", todos=[td0, td1], narrative="two-step frame",
                      verdict=_verdict())
    # calls/ops are pinned to the exact concatenation (so the engine executes the same flat ops)
    assert [c.intent for c in plan.calls] == ["get_dependencies", "active_alerts"]
    assert [type(o).__name__ for o in plan.ops] == ["AddNode", "ProposeHypothesis", "ProposeHypothesis"]
    # effective_todos are the authored ones, unchanged
    assert plan.effective_todos is plan.todos
    # attribution: call 0 → to-do 0, call 1 → to-do 1; op 0 → 0, ops 1,2 → 1
    assert plan.call_todo_indices() == [0, 1]
    assert plan.op_todo_indices() == [0, 1, 1]


def test_flatten_invariant_holds_for_both_paths():
    """flatten(effective_todos) == the flat calls/ops — the invariant the engine's execution
    attribution rests on, on BOTH the authored and the synthesized path."""
    for plan in (
        PlanOutput(phase="frame", calls=[_call("a"), _call("b")], ops=[_node("s")],
                   narrative="n", verdict=_verdict()),
        PlanOutput(phase="frame", todos=[Todo(objective="x", calls=[_call("a")], ops=[_node("s")]),
                                         Todo(objective="y", calls=[_call("b")])],
                   narrative="n", verdict=_verdict()),
    ):
        flat_calls = [c for td in plan.effective_todos for c in td.calls]
        flat_ops = [o for td in plan.effective_todos for o in td.ops]
        assert flat_calls == plan.calls
        assert flat_ops == plan.ops
        assert len(plan.call_todo_indices()) == len(plan.calls)
        assert len(plan.op_todo_indices()) == len(plan.ops)


# ── the session write-gate path: model_copy of calls re-derives the checklist ──────────
def test_model_copy_of_calls_redrives_the_synthesized_todo():
    """The session injects a write call via `model_copy(update={"calls": [...]})` (bypassing the
    validator). Because effective_todos is DERIVED (not stored) for the no-authored-to-dos path, the
    added call is attributed to the single to-do — never orphaned or index-mismatched."""
    plan = PlanOutput(phase="act", calls=[], ops=[_propose("h1")], narrative="remediate",
                      verdict=_verdict())
    assert plan.call_todo_indices() == []          # no calls yet
    gated = plan.model_copy(update={"calls": [_call("apply_remediation")]})
    # the injected write is under the (still single) to-do, attribution matches the new call count
    assert len(gated.effective_todos) == 1
    assert [c.intent for c in gated.effective_todos[0].calls] == ["apply_remediation"]
    assert gated.call_todo_indices() == [0]
    assert len(gated.call_todo_indices()) == len(gated.calls)


# ── the F1 seams are declared + default-inert (op-ceiling-per-todo; delegatable to-do) ─
def test_todo_seams_default_inert_and_settable():
    default = Todo(objective="x")
    assert default.op_budget is None and default.delegate is False
    seamed = Todo(objective="delegatable", op_budget=8, delegate=True)
    assert seamed.op_budget == 8 and seamed.delegate is True
