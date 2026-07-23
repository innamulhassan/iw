"""Phase-review gate (owner 2026-07-23): after a phase completes its goal, the interactive
session SUMMARIZES what it accomplished and gets the human's DIRECTION approval before advancing
to the NEXT phase. Reuses the write-gate's suspend/approve machinery as a SESSION-DRIVER pause
BETWEEN steps (never an engine/controller gate), so the batch Engine.run()/gen_golden/run_live
path never encounters it and every golden stays byte-identical.

Covered here:
  * interactive suspend at each declared transition (frame->investigate, investigate->act, …)
  * APPROVE advances; REFINE re-runs the phase with the steer; DENY halts (terminal)
  * auto_review=True (scripted / CI / batch) drives straight through — never suspends, never
    journals a review (the hermetic journals stay byte-identical — this is the no-hang guarantee)
  * the Act WRITE-gate SUBSUMES act's review (one pause at act, not two)
  * the whole interactive flow stays journal-authoritative + gap-free
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_code_regression as s1
from e2e._helpers import fact, node, phase

import iw_engine
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.graph import rebuild
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.session import (
    GateDecision,
    InvestigationSession,
    ReviewDecision,
    SessionState,
)

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def _clock() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


def _review_session(*, auto_review: bool = False) -> InvestigationSession:
    """The code_regression twin driven interactively (no layer — the direct-ops path), with the
    phase-review pause ON (auto_review=False) by default."""
    subject, script = s1.build()
    pb = load_playbook(PLAYBOOK)
    return InvestigationSession(subject, pb, ScriptedPlanner(script),
                               clock=_clock, auto_review=auto_review)


# ── interactive suspend at the first transition ────────────────────────────────
def test_review_suspends_at_frame_to_investigate():
    session = _review_session()
    evs = session.advance()               # runs FRAME, then pauses BEFORE investigate

    assert session.state == SessionState.AWAITING_REVIEW
    # the engine points at the NEXT phase but has NOT run it yet
    assert session._engine.current_phase == "investigate"
    assert "investigate" not in session._engine._phases_run

    opened = [e for e in evs if e["type"] == "phase_review_opened"]
    assert len(opened) == 1
    r = opened[0]
    assert r["phase"] == "frame" and r["to_phase"] == "investigate"
    assert r["verdict"] == "advance"
    assert r["goal"] and r["narrative"], "the summary carries the goal + the phase narrative"
    assert r["discovered"]["nodes"] >= 1 and "facts" in r["discovered"]
    assert "proposing to advance to 'investigate'" in r["summary"]

    # the review OPENING is durable (gate_opened-style)
    pr = [e for e in session._engine.journal.entries if e.kind == "phase_review"]
    assert pr and pr[0].action["to_phase"] == "investigate" and pr[0].phase_id == "frame"

    # the last state event marks the review pause (a distinct state, not the write-gate's)
    st = [e for e in evs if e["type"] == "session_state"]
    assert st[-1]["state"] == "awaiting_review"
    assert session.pending_review is not None and session.pending_gate is None


# ── APPROVE walks the whole run to a resolved close ────────────────────────────
def test_review_approve_advances_through_to_close():
    session = _review_session()
    session.advance()

    to_phases, guard = [], 0
    while session.state == SessionState.AWAITING_REVIEW:
        to_phases.append(session.pending_review["to_phase"])
        session.answer_review(ReviewDecision.APPROVE, actor="alice@oncall")
        guard += 1
        assert guard < 12, "approve loop must terminate"

    assert session.state == SessionState.CLOSED
    assert session.outcome == "resolved"
    # a review fired at EACH advancing transition to a new phase (the DONE terminal has none)
    assert to_phases == ["investigate", "act", "verify", "close"]

    decs = [e for e in session._engine.journal.entries if e.kind == "review_decision"]
    assert len(decs) == 4 and all(d.decision == "approve" for d in decs)
    assert all(d.actor == "alice@oncall" and d.source.value == "human" for d in decs)

    # journal-authoritative: replay reproduces the graph + hypothesis store exactly
    g2, store2 = rebuild(session._engine.journal)
    assert g2.to_dict() == session._engine.graph.to_dict()
    assert {h: v.status for h, v in store2.hypotheses.items()} == \
           {h: v.status for h, v in session._engine.hypothesis_store.hypotheses.items()}


# ── REFINE re-runs the just-completed phase with the operator's steer ──────────
def test_review_refine_reenters_phase_with_steer():
    subject, _ = s1.build()
    onset = fact(s1.ANOM, "onset_value", 0.40, s1.T_ONSET, source=S.PROMETHEUS)
    frame1 = phase("frame", [node(NT.ANOMALY, anomaly_id="ANOM-1"), onset],
                   "initial framing", status="advance")
    frame2 = phase("frame", [node(NT.ANOMALY, anomaly_id="ANOM-1"), onset],
                   "reframed after operator steer", status="advance")
    pb = load_playbook(PLAYBOOK)
    session = InvestigationSession(subject, pb, ScriptedPlanner([frame1, frame2]),
                                   clock=_clock, auto_review=False)

    session.advance()
    assert session.state == SessionState.AWAITING_REVIEW
    assert session.pending_review["narrative"] == "initial framing"

    # REFINE with a steer: re-runs FRAME (consuming the 2nd script step) with the steer buffered
    session.answer_review(ReviewDecision.REFINE, text="also check the cache tier")

    # the phase re-ran and re-proposed the advance → we are back at a frame->investigate review
    assert session.state == SessionState.AWAITING_REVIEW
    assert session._engine._phases_run.count("frame") == 2
    assert session.pending_review["narrative"] == "reframed after operator steer"

    # the steer is a durable operator message (the LIVE planner would see it on the re-plan)
    msgs = [e for e in session._engine.journal.entries if e.kind == "message"]
    assert msgs and msgs[-1].reasoning == "also check the cache tier"
    # a refine decision is journaled + a resumed lifecycle recorded the re-entry
    rdecs = [e for e in session._engine.journal.entries if e.kind == "review_decision"]
    assert rdecs and rdecs[0].decision == "refine"
    life = [e.action["event"] for e in session._engine.journal.entries if e.kind == "lifecycle"]
    assert "resumed" in life


# ── DENY halts the investigation (terminal) ────────────────────────────────────
def test_review_deny_halts_terminal():
    session = _review_session()
    session.advance()
    assert session.state == SessionState.AWAITING_REVIEW

    session.answer_review(ReviewDecision.DENY, text="framing is not trustworthy")
    assert session.state == SessionState.CLOSED
    assert session.outcome == "open"                  # close was never reached
    assert "investigate" not in session._engine._phases_run   # halted before the next phase ran

    rdecs = [e for e in session._engine.journal.entries if e.kind == "review_decision"]
    assert rdecs and rdecs[0].decision == "deny"
    life = [e for e in session._engine.journal.entries if e.kind == "lifecycle"]
    assert life[-1].action["event"] == "closed"
    assert life[-1].action["reason"] == "phase_review_denied"
    # the closing session_state event fires with a terminal outcome
    st = [e for e in session.events() if e["type"] == "session_state"]
    assert st[-1]["state"] == "closed"
    # the halted run still replays exactly from its own journal
    g2, _ = rebuild(session._engine.journal)
    assert g2.to_dict() == session._engine.graph.to_dict()


# ── auto_review=True (scripted / CI / batch) drives straight through, no hang ──
def test_auto_review_drives_straight_through_and_journals_nothing():
    session = _review_session(auto_review=True)
    session.advance()                                 # ONE call runs the whole investigation

    assert session.state == SessionState.CLOSED
    assert session.outcome == "resolved"
    assert session._engine.current_phase is None
    # the non-interactive mode NEVER suspends and NEVER journals a review — so a scripted/CI
    # session (and every existing hermetic journal) is byte-identical to before this feature.
    assert not [e for e in session._engine.journal.entries
                if e.kind in ("phase_review", "review_decision")]
    assert not [e for e in session.events() if e["type"] in
                ("phase_review_opened", "phase_review_decision")]


# ── the Act WRITE-gate SUBSUMES act's review (one pause at act, not two) ────────
def test_write_gate_subsumes_act_review():
    from unit.test_session import _approve_script, _layer

    subject, script = _approve_script()               # a WRITE injected into ACT
    pb = load_playbook(PLAYBOOK)
    session = InvestigationSession(subject, pb, ScriptedPlanner(script),
                                   layer=_layer(), clock=_clock, auto_review=False)

    session.advance()
    reviewed_to = []
    while session.state == SessionState.AWAITING_REVIEW:
        reviewed_to.append(session.pending_review["to_phase"])
        session.answer_review(ReviewDecision.APPROVE)

    # reviews fired ONLY at frame->investigate and investigate->act; then the ACT write-gate
    assert reviewed_to == ["investigate", "act"]
    assert session.state == SessionState.SUSPENDED     # the write-gate, not a review
    assert session._engine.current_phase == "act"
    assert session.pending_gate is not None and session.pending_review is None

    # approve the write → act advances to verify. act's OWN review (act->verify) is SUPPRESSED
    # (the write-gate was act's single human pause) — the next pause is the verify->close review.
    session.answer_gate(GateDecision.APPROVE)
    assert session.state == SessionState.AWAITING_REVIEW
    assert session.pending_review["phase"] == "verify"      # act->verify was NOT reviewed
    assert session.pending_review["to_phase"] == "close"

    session.answer_review(ReviewDecision.APPROVE)
    assert session.state == SessionState.CLOSED
    assert session.outcome == "resolved"
    # exactly THREE reviews total (frame->inv, inv->act, verify->close) — never an act->verify one
    revs = [e for e in session._engine.journal.entries if e.kind == "phase_review"]
    assert [e.phase_id for e in revs] == ["frame", "investigate", "verify"]


# ── the interactive review flow stays gap-free (seq discipline) ────────────────
def test_review_flow_is_gap_free():
    session = _review_session()
    session.advance()
    while session.state == SessionState.AWAITING_REVIEW:
        session.answer_review(ReviewDecision.APPROVE)
    seqs = sorted({e.seq for e in session._engine.journal.entries})
    assert seqs == list(range(1, len(seqs) + 1)), f"burned/gapped seqs: {seqs}"


# ── repeats never fire a review (only advancing transitions to a NEW phase) ────
def test_repeat_transition_does_not_review():
    session = _review_session()
    session.advance()                                 # frame->investigate review
    session.answer_review(ReviewDecision.APPROVE)
    # the investigate loop REPEATS once (investigate_open votes repeat) before advancing to act;
    # that same-phase repeat must NOT open a review — the next review is the investigate->act one.
    assert session.state == SessionState.AWAITING_REVIEW
    assert session.pending_review["phase"] == "investigate"
    assert session.pending_review["to_phase"] == "act"
    assert session._engine._phases_run.count("investigate") == 2   # it did loop
