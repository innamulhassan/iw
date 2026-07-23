"""JOURNAL-COMPLETE capture (B1). The owner goal: the journal is the ONE complete record —
per phase, FROM THE START, it captures the goal, the planner's PLAN, the TOOLS AVAILABLE, every
tool call (intent/provider/why/outcome/op_count/ts), the reasoning, the write-gate + decision,
and the run lifecycle (started/resumed/exhausted/closed). Every entry carries its kind; ts on
every entry. These tests prove the CAPTURE side (serving is B2, rendering is B3).
"""
from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

from e2e import scenario_code_regression as cr
from e2e import scenario_deployment as dep
from e2e._helpers import run
from unit.test_session import _layer

import iw_engine
from iw_engine.journal.journal import Journal
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.session import GateDecision, InvestigationSession

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
T0 = datetime(2026, 7, 19, tzinfo=UTC)


def _clock() -> datetime:
    return T0


# ── the PLAN + TOOLS AVAILABLE are captured, per phase, sharing the phase seq ─────────
def test_every_phase_journals_its_plan_and_tools_available():
    """A `plan` entry per phase run: the tools it COULD call (allowed_intents), the intents it
    DECIDED to call, the direct ops it authored, and its narrative — an annotation sharing the
    phase's seq, so replay is untouched."""
    subject, script, fixtures = dep.build()
    res = run(subject, script, fixtures)
    jr = res.journal

    plans = [e for e in jr.entries if e.kind == "plan"]
    assert len(plans) == len(res.phases_run), "one plan record per phase run"
    # every plan carries the ACCESS SURFACE (what it could call) + its narrative
    assert all(p.available for p in plans), "tools-available (allowed_intents) captured per plan"
    assert all(p.reasoning for p in plans), "the plan's narrative is captured"
    # a plan is an ANNOTATION — it shares its phase's seq and is replay-inert (no delta)
    phase_seqs = {e.seq for e in jr.phase_entries()}
    assert all(p.seq in phase_seqs for p in plans), "the plan annotates its phase's seq"
    assert all(p.delta is None for p in plans), "a plan carries no delta (replay ignores it)"
    # FRAME decided to call the topology/change/alert tools — the plan makes that intent explicit
    frame_plan = next(p for p in plans if p.phase_id == "frame")
    assert "get_dependencies" in frame_plan.plan_calls
    assert "active_alerts" in frame_plan.plan_calls
    assert set(frame_plan.available) == {"ingest_alert", "query_change_log", "seed_graph",
                                         "get_dependencies", "assess_impact", "list_dependencies"}


def test_scripted_direct_ops_plan_is_visible_not_fabricated():
    """A scripted plan MAY author its evidence as DIRECT ops (zero CapabilityCalls) — the engine
    journals ZERO invocations, honestly (the owner: 'if the scripted path legitimately makes no
    tool calls, journal that honestly — do not fabricate'). The PLAN entry makes that provenance
    visible: plan_calls is empty, but plan_ops (the direct ops) and available (what it COULD have
    called) are on the record, so an audit is no longer blind to what the phase did. (The flagship
    twin now authors reasoned CALLS, so the direct-ops back-compat is exercised with a minimal
    inline plan, decoupled from any one scenario's evolving content.)"""
    from e2e._helpers import fact, nid, node, phase

    from iw_engine.domain.enums import NodeType
    from iw_engine.domain.subject import SubjectRef

    subject = SubjectRef(domain="app-incident", id="INC-DIRECT", kind="incident")
    anom = nid(NodeType.ANOMALY, anomaly_id="ANOM-1")
    at = datetime(2026, 7, 19, tzinfo=UTC)
    script = [phase("frame", [node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
                              fact(anom, "onset_value", 0.40, at)],
                    "framed from direct ops — no tools called", status="advance"),
              phase("investigate", [], "nothing further to investigate", status="blocked")]
    res = run(subject, script, None)            # no fixtures / no calls — the direct-ops path
    jr = res.journal

    invs = [e for e in jr.entries if e.kind == "invocation"]
    assert invs == [], "no tool calls were made — journalled honestly, never fabricated"

    plans = [e for e in jr.entries if e.kind == "plan"]
    frame_plan = next(p for p in plans if p.phase_id == "frame")
    assert frame_plan.plan_calls == [], "the scripted twin decided to call no tools"
    assert frame_plan.plan_ops, "but the DIRECT ops it authored are on the record (provenance)"
    assert "AddNode" in frame_plan.plan_ops
    assert frame_plan.available, "and the tools it COULD have called are captured too"


# ── F1: the plan is a CHECKLIST of to-dos; each invocation attributes to its to-do ─────
def test_plan_entry_carries_todo_checklist_and_invocations_attribute():
    """Every `plan` entry records its to-do CHECKLIST (F1). On the scripted path each phase reads
    as ONE synthesized to-do (objective + its call intents + op kinds + status), and it flattens
    back to the flat plan_calls/plan_ops — the attribution invariant. Every invocation carries the
    to-do index it served."""
    subject, script, fixtures = dep.build()
    res = run(subject, script, fixtures)
    jr = res.journal

    plans = [e for e in jr.entries if e.kind == "plan"]
    frame_plan = next(p for p in plans if p.phase_id == "frame")
    assert frame_plan.todos, "a phase that did work records a non-empty checklist (F1)"
    td = frame_plan.todos[0]
    assert set(td) >= {"objective", "calls", "ops", "status"}
    assert td["status"] == "pending" and td["objective"]
    # EVERY plan's checklist flattens back to its flat plan_calls/plan_ops — the attribution
    # invariant (a do-nothing phase like `close` is simply an empty checklist: []==[]).
    for p in plans:
        assert [c for t in p.todos for c in t["calls"]] == p.plan_calls
        assert [o for t in p.todos for o in t["ops"]] == p.plan_ops
    # every invocation carries a to-do index pointing at a real to-do of its phase's plan
    todos_len = {p.phase_id: len(p.todos) for p in plans}
    invs = [e for e in jr.entries if e.kind == "invocation"]
    assert invs, "the deployment twin makes tool calls"
    for e in invs:
        assert e.todo is not None, "each invocation attributes to a to-do (F1)"
        assert 0 <= e.todo < todos_len[e.phase_id]
    assert all(e.todo == 0 for e in invs), "the scripted default is a single-item checklist"


def test_engine_attributes_each_invocation_to_its_authored_todo():
    """MULTI-to-do attribution end-to-end: a frame plan authoring TWO to-dos (each with its own
    call) journals each invocation under the RIGHT to-do index — the 1:1 execution loop is
    unchanged; `todo` is the added attribution (call 0 → to-do 0, call 1 → to-do 1)."""
    from e2e._helpers import call, node, verdict

    from iw_engine.capability import CapabilityLayer, MockSource
    from iw_engine.capability.adapters import default_adapters
    from iw_engine.domain.enums import NodeType
    from iw_engine.domain.subject import SubjectRef
    from iw_engine.runtime import Engine, ScriptedPlanner
    from iw_engine.runtime.planner import PlanOutput, Todo

    pb = load_playbook(PLAYBOOK)
    frame = PlanOutput(
        phase=pb.entry_phase, narrative="frame", verdict=verdict("advance"),
        todos=[Todo(objective="pull the recent changes",
                    calls=[call("find_recent_changes", ci="orders-api")]),
               Todo(objective="read the firing alerts + seed the symptom",
                    calls=[call("active_alerts", service="orders-api")],
                    ops=[node(NodeType.ANOMALY, anomaly_id="ANOM-1")])])
    subject = SubjectRef(domain="app-incident", id="INC-TODO", kind="incident")
    engine = Engine(pb, ScriptedPlanner([frame]),
                    clock=_clock, layer=CapabilityLayer(default_adapters(), source=MockSource({})))
    engine.start(subject)
    engine.step()                                   # run FRAME only

    invs = [e for e in engine.journal.entries if e.kind == "invocation"]
    assert [(e.intent, e.todo) for e in invs] == \
           [("find_recent_changes", 0), ("active_alerts", 1)]
    assert engine.invocation_todos == [0, 1]        # the live-stream attribution list, in lockstep
    # the plan entry's checklist records the op attribution too: to-do 1 authored the AddNode
    plan = next(e for e in engine.journal.entries if e.kind == "plan")
    assert plan.todos[0]["ops"] == [] and plan.todos[1]["ops"] == ["AddNode"]


# ── every tool call carries the WHY (owner goal) + its outcome/op_count/ts ─────────────
def test_every_invocation_carries_its_why_and_boundary_fields():
    subject, script, fixtures = dep.build()
    res = run(subject, script, fixtures)
    invs = [e for e in res.journal.entries if e.kind == "invocation"]
    assert len(invs) == 9, "the deployment twin makes 9 read calls across frame + investigate"
    for e in invs:
        assert e.reasoning, "the WHY each tool was called (was reasoning=None before)"
        assert e.ts is not None, "every invocation is timestamped"
        assert e.intent and e.action and "provider" in e.action
        assert "outcome" in e.observation and "op_count" in e.observation


# ── the run LIFECYCLE: started + resumed are now emitted (were documented, never emitted) ──
def _gated_session() -> InvestigationSession:
    from e2e._helpers import call

    subject, script = cr.build()
    script[3] = script[3].model_copy(
        update={"calls": [call("rollback_release", to_version="v4.11.3")]})
    pb = load_playbook(PLAYBOOK)
    return InvestigationSession(subject, pb, ScriptedPlanner(script),
                                layer=_layer(), clock=_clock)


def test_lifecycle_started_is_emitted_at_run_start():
    session = _gated_session()          # construction starts the run
    life = [e for e in session._engine.journal.entries if e.kind == "lifecycle"]
    assert life and life[0].action["event"] == "started"
    assert life[0].phase_id == "frame"          # WHERE it started (the entry phase)
    assert life[0].ts is not None


def test_lifecycle_resumed_is_emitted_on_gate_answer():
    session = _gated_session()
    session.advance()                            # runs to the suspended write-gate
    session.answer_gate(GateDecision.APPROVE, actor="alice@oncall")
    life = [e.action["event"] for e in session._engine.journal.entries if e.kind == "lifecycle"]
    # started at the front, resumed after the human answers the gate, closed at the terminal
    assert life[0] == "started"
    assert "resumed" in life
    assert life[-1] == "closed"
    # the full interactive flow stays GAP-FREE despite the added lifecycle records
    seqs = sorted({e.seq for e in session._engine.journal.entries})
    assert seqs == list(range(1, len(seqs) + 1)), f"burned/gapped seqs: {seqs}"


# ── CLEAN: no on-disk line lacks a kind (the schema header carries kind="header") ─────
def test_no_ondisk_line_is_kindless_and_roundtrips():
    session = _gated_session()
    session.advance()
    session.answer_gate(GateDecision.APPROVE)
    text = session._engine.journal.to_ndjson()
    lines = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    assert lines, "the journal serialized at least the header + entries"
    assert all("kind" in d for d in lines), "EVERY on-disk line carries its kind (CLEAN rule)"
    assert lines[0]["kind"] == "header" and lines[0]["schema_version"] == 2
    # and the header still round-trips (stripped by its schema_version key, entries intact)
    jr = Journal.from_ndjson(text)
    assert [e.seq for e in jr.entries] == [e.seq for e in session._engine.journal.entries]
    assert all(e.kind for e in jr.entries), "every LOADED entry carries its kind"
