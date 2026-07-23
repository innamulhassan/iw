"""REASONED-INVOCATION attribution (JOURNAL story fidelity, 2026-07-23). Targeted, golden-free
proof of the per-call `why` precedence + the reasoned-step attribution the engine stamps on every
invocation record — the machinery the flagship INC-4821 twin exercises end-to-end, asserted here in
isolation so a regression fails loudly on its own terms (not only as a golden byte-drift).

Three properties, each on a minimal reasoned-todo plan driven through the real engine via the
_helpers DSL (`phase(todos=[todo(objective, calls=[call(...)], ops=[...], observation=...)])` + run):

  1. PRECEDENCE — `call.rationale` wins as the invocation's why over the serving to-do's objective;
     an empty rationale falls back to that objective (the `rationale → objective → narrative` chain).
  2. MULTI-CALL ROBUSTNESS — a single to-do carrying TWO calls journals TWO invocations with TWO
     DISTINCT whys (a multi-call to-do never collapses to one generic why).
  3. ATTRIBUTION — a reasoned to-do with N ops + observation "X" stamps op_count == N (overriding
     the empty mock fold), result == "X", and a non-empty per-op `produced` summary describing the ops.

Assertions read the journal's `kind=="invocation"` entries directly (the `_journal_invocation`
surface — matching tests/unit/test_journal_capture.py); the bundle export flattens these same fields
(reasoning→narrative, observation.op_count→op_count, action.result→result, action.produced→produced).
"""
from __future__ import annotations

from datetime import UTC, datetime

from e2e._helpers import call, fact, nid, node, phase, run, todo

from iw_engine.domain.enums import NodeType
from iw_engine.domain.subject import SubjectRef

_AT = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
_ANOM = nid(NodeType.ANOMALY, anomaly_id="ANOM-1")


def _frame_invocations(todos: list) -> list:
    """Drive ONE reasoned FRAME phase (then a blocked INVESTIGATE so the run terminates) and return
    its journaled invocation entries. Hermetic + deterministic: run() wires the mock layer (each
    unfixtured intent folds to ZERO ops, so the authored ops stay the graph delta) and pins a fixed
    clock/ids. The FRAME plan must seed the Anomaly + an onset_value fact so the phase gate passes and
    the run advances into the terminal INVESTIGATE (a coerced repeat would mis-order the script)."""
    subject = SubjectRef(domain="app-incident", id="INC-REASON", kind="incident")
    script = [
        phase("frame", todos=todos, narrative="reasoned frame", status="advance"),
        phase("investigate", [], "nothing further", status="blocked"),
    ]
    res = run(subject, script)
    return [e for e in res.journal.entries if e.kind == "invocation"]


# ── 1. PRECEDENCE: the per-call rationale is the why (objective is only the fallback) ──────
def test_call_rationale_wins_over_todo_objective_as_the_why():
    invs = _frame_invocations([
        # call carries an explicit rationale — it must win over the to-do's objective
        todo("OBJECTIVE-A: quantify the blast radius",
             calls=[call("range_query", "RATIONALE-A: pull error ratio + latency to size the blast radius",
                         service_name="payments-api")],
             ops=[node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
                  fact(_ANOM, "onset_value", 0.40, _AT)]),
        # call carries NO rationale — the engine must fall back to THIS to-do's objective
        todo("OBJECTIVE-B: map what payments-api depends on",
             calls=[call("get_dependencies", service_name="payments-api")]),
    ])
    assert len(invs) == 2
    # rationale present ⇒ it is the why, and it is NOT the objective (precedence, not coincidence)
    assert invs[0].reasoning == "RATIONALE-A: pull error ratio + latency to size the blast radius"
    assert invs[0].reasoning != "OBJECTIVE-A: quantify the blast radius"
    # rationale absent ⇒ the why falls back to the serving to-do's objective (proves the chain)
    assert invs[1].reasoning == "OBJECTIVE-B: map what payments-api depends on"


# ── 2. MULTI-CALL ROBUSTNESS: two calls in one to-do → two DISTINCT whys ──────────────────
def test_two_calls_in_one_todo_journal_two_distinct_whys():
    invs = _frame_invocations([
        todo("OBJECTIVE: read the onset from two angles",
             calls=[call("range_query", "WHY-1: measure the RED signals to size the blast radius",
                         service_name="payments-api"),
                    call("fetch_traces", "WHY-2: capture an onset trace to read the failure shape",
                         service_name="payments-api")],
             ops=[node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
                  fact(_ANOM, "onset_value", 0.40, _AT)]),
    ])
    assert len(invs) == 2, "two calls in one to-do journal two invocations"
    whys = [e.reasoning for e in invs]
    assert whys == ["WHY-1: measure the RED signals to size the blast radius",
                    "WHY-2: capture an onset trace to read the failure shape"]
    assert len(set(whys)) == 2, "the two whys are DISTINCT — a multi-call to-do never shares one generic why"
    assert all(e.todo == 0 for e in invs), "both calls attribute to the SAME single to-do (index 0)"


# ── 3. ATTRIBUTION: op_count == N, result == observation, produced describes the ops ──────
def test_reasoned_todo_attributes_opcount_result_and_produced_to_its_call():
    ops = [node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
           node(NodeType.SERVICE, service_name="payments-api", env="prod"),
           fact(_ANOM, "onset_value", 0.40, _AT)]
    invs = _frame_invocations([
        todo("quantify the blast radius",
             calls=[call("range_query", "size the blast radius", service_name="payments-api")],
             ops=ops,
             observation="40% of ~820 rpm are 5xx; p99 dragged to 4.2s — a code-fault shape"),
    ])
    assert len(invs) == 1
    e = invs[0]
    # op_count is the SERVING to-do's op count (N), attributed over the empty mock fold (0)
    assert e.observation["op_count"] == len(ops) == 3
    # the human RESULT line is the to-do's observation ("what came back")
    assert e.action["result"] == "40% of ~820 rpm are 5xx; p99 dragged to 4.2s — a code-fault shape"
    # a per-op `produced` summary describes what the reasoned step produced
    produced = e.action["produced"]
    assert produced and len(produced) == 3, "one produced-summary entry per authored op"
    assert any("fact " in p for p in produced), "a produced fact is summarized"
    assert any("node " in p for p in produced), "a produced node is summarized"
