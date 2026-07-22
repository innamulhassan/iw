"""JOURNAL v2 (P6 step 3, part2 §1): typed entry kinds, ONE seq space assigned at append
(claim-at-first-event — the reserve-at-phase-start burn is gone), every capability call
durable, the gate OPENING durable, v1 journals read-only-loadable, tolerant-additive wire.
"""
from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime

import pytest
from e2e import scenario_code_regression as s1
from e2e._helpers import call

import iw_engine
from iw_engine.journal.journal import SCHEMA_VERSION, Journal, JournalEntry
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.session import GateDecision, InvestigationSession

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
T0 = datetime(2026, 7, 19, tzinfo=UTC)


def _clock() -> datetime:
    return T0


def _gated_session():
    """The code_regression twin with a WRITE injected into REMEDIATE (the test_session rig)."""
    from unit.test_session import _layer

    subject, script = s1.build()
    # a READ alongside the WRITE, so the journal-every-call unification is provable on both
    # effects in one phase (the mock transport serves the read fixture-less → clean-empty)
    script[4] = script[4].model_copy(
        update={"calls": [call("fetch_metrics", service="payments-api"),
                          call("rollback_release", to_version="v4.11.3")]})
    pb = load_playbook(PLAYBOOK)
    return InvestigationSession(subject, pb, ScriptedPlanner(script),
                                layer=_layer(), clock=_clock)


# ── one seq space, append-at-event ────────────────────────────────────────────
def test_gate_flow_burns_no_seq():
    """The v1 TOCTOU: the suspended phase reserved a seq it never used → an unlabeled gap.
    v2 claims at the first journal event, so the full interactive flow is GAP-FREE."""
    session = _gated_session()
    session.advance()
    session.answer_gate(GateDecision.APPROVE, actor="alice@oncall")

    jr = session._engine.journal
    seqs = sorted({e.seq for e in jr.entries})
    assert seqs == list(range(1, len(seqs) + 1)), f"burned/gapped seqs: {seqs}"
    # and the numbered (non-annotation) entries are strictly sequential in append order
    numbered = [e.seq for e in jr.entries if e.kind != "invocation"]
    assert numbered == sorted(numbered)


def test_append_assigns_seq_at_event():
    jr = Journal(clock=_clock)
    e1 = jr.append(JournalEntry(ts=T0, kind="lifecycle", reasoning="started",
                                action={"event": "started"}))
    e2 = jr.append(JournalEntry(ts=T0, kind="message", reasoning="hi", actor="op"))
    assert (e1.seq, e2.seq) == (1, 2)
    assert jr.reserve_seq() == 3            # the claim path shares the same one counter


# ── the typed kinds are journaled ─────────────────────────────────────────────
def test_gate_opened_and_decision_are_durable():
    session = _gated_session()
    session.advance()
    jr = session._engine.journal
    opened = [e for e in jr.entries if e.kind == "gate_opened"]
    assert len(opened) == 1
    assert opened[0].action["actions"][0]["intent"] == "rollback_release"
    assert opened[0].observation["hypothesis"] == "hyp:h1"     # on whose behalf
    assert opened[0].observation["evidence"], "the cited evidence rides the record"

    session.answer_gate(GateDecision.DENY, reason="too risky")
    decisions = [e for e in jr.entries if e.kind == "gate_decision"]
    assert decisions and decisions[0].decision == "deny"
    assert decisions[0].seq > opened[0].seq                    # question, then answer


def test_operator_message_is_a_message_kind():
    session = _gated_session()
    session.advance()
    session.add_message("check the cache first", actor="op")
    msgs = [e for e in session._engine.journal.entries if e.kind == "message"]
    assert msgs and msgs[0].reasoning == "check the cache first"
    assert msgs[0].actor == "op" and msgs[0].intent == "operator_message"


def test_every_capability_call_is_journaled():
    """part2 §1: 'an approved write can leave zero durable trace' — closed. Data-bearing,
    clean-empty AND the approved write itself all land as invocation annotations (sharing
    their phase's seq), and the approved write's entry proves EXECUTION, not just consent."""
    session = _gated_session()
    session.advance()
    session.answer_gate(GateDecision.APPROVE)
    jr = session._engine.journal
    invs = [e for e in jr.entries if e.kind == "invocation"]
    assert any(e.intent == "rollback_release" and e.decision == "data" for e in invs), \
        "the approved write's execution must be durable"
    # reads are journaled too (v2 unification) — this hermetic rig serves them fixture-less,
    # so they land as honest clean-empty records, distinct from error (part4 §4)
    reads = [e for e in invs if e.intent != "rollback_release"]
    assert reads and all(e.decision == "empty" for e in reads)
    phase_seqs = {e.seq for e in jr.phase_entries()}
    assert all(e.seq in phase_seqs for e in invs), "invocations annotate their phase's seq"
    # RECORD kinds stay out of the bundle-journal view (P3's exclusion, kept)
    from iw_engine.api.bundle import export_bundle
    view = export_bundle(session._engine.result())["journal"]
    assert all(j.get("kind") in (None, "step", "gate_decision", "message") or "refs" in j
               for j in view)
    assert not any(j.get("intent") == "find_recent_changes" for j in view)


# ── v1 read-only + tolerant-additive wire ─────────────────────────────────────
def test_v1_journal_loads_read_only():
    """A v1 file (schema_version 1, a 'step' entry, an invocation sharing a phase seq, and a
    reserve-era seq GAP) loads intact — replay of old runs stays possible."""
    phase_line = json.dumps({
        "seq": 1, "ts": T0.isoformat(), "kind": "invocation", "phase_id": "frame",
        "actor": "engine", "intent": "x", "decision": "error",
        "observation": {"outcome": "error", "reason": "boom"}})
    step_line = json.dumps({
        "seq": 3, "ts": T0.isoformat(), "kind": "step", "phase_id": "remediate",
        "actor": "alice", "source": "human", "decision": "approve"})
    text = "\n".join([json.dumps({"schema_version": 1}), phase_line, step_line]) + "\n"
    jr = Journal.from_ndjson(text)
    assert [e.kind for e in jr.entries] == ["invocation", "step"]
    assert jr.step_entries()[0].kind == "step"      # the v1 union still counts as a human step
    assert jr.reserve_seq() == 4                    # watermark = surviving max + 1 (gap kept)


def test_future_schema_version_refuses_loudly():
    text = json.dumps({"schema_version": SCHEMA_VERSION + 1}) + "\n"
    with pytest.raises(ValueError, match="newer than this engine"):
        Journal.from_ndjson(text)


def test_additive_unknown_field_is_tolerated():
    line = json.dumps({"seq": 1, "ts": T0.isoformat(), "kind": "lifecycle",
                       "reasoning": "started", "some_future_field": {"x": 1}})
    jr = Journal.from_ndjson(json.dumps({"schema_version": SCHEMA_VERSION}) + "\n" + line + "\n")
    assert jr.entries[0].kind == "lifecycle"
    assert not hasattr(jr.entries[0], "some_future_field")
