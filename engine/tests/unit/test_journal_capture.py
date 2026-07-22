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
    """INC-4821's twin authors its evidence as DIRECT ops (zero CapabilityCalls), so it journals
    ZERO invocations — honestly (the owner: 'if the scripted path legitimately makes no tool
    calls, journal that honestly — do not fabricate'). The PLAN entry makes that provenance
    visible: plan_calls is empty, but plan_ops (the direct ops) and available (what it COULD have
    called) are on the record, so an audit is no longer blind to what the phase did."""
    subject, script = cr.build()
    res = run(subject, script, None)            # no fixtures / no layer — the direct-ops path
    jr = res.journal

    invs = [e for e in jr.entries if e.kind == "invocation"]
    assert invs == [], "no tool calls were made — journalled honestly, never fabricated"

    plans = [e for e in jr.entries if e.kind == "plan"]
    frame_plan = next(p for p in plans if p.phase_id == "frame")
    assert frame_plan.plan_calls == [], "the scripted twin decided to call no tools"
    assert frame_plan.plan_ops, "but the DIRECT ops it authored are on the record (provenance)"
    assert "AddNode" in frame_plan.plan_ops
    assert frame_plan.available, "and the tools it COULD have called are captured too"


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
