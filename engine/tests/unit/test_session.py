"""Interactive session backend (DEPTH-BUILD-PLAN §C). Drives the REAL engine through the
`code_regression` scenario as a resumable, human-gated session — reusing
`scenario_code_regression.build()` as the deterministic planner + fixture source. Two runs:

  * approve → the proposed write (a rollback) is applied under the gate → RESOLVED.
  * deny   → the write is dropped, the denial is recorded as a synthetic ledger result fed
             back, and the run takes a DIVERGENT journal (→ MITIGATED).

Asserts the gate suspension, the derived event-stream shape, and that a node's `graph_delta`
carries its `created_by` creation-order seq.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_code_regression as s1
from e2e._helpers import call, fact, phase, update

import iw_engine
from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters import default_adapters
from iw_engine.domain.enums import Binding, Effect, Source
from iw_engine.domain.operations import AddEvent, Operation
from iw_engine.graph import rebuild
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.session import GateDecision, InvestigationSession, SessionState

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
ROLLBACK_INTENT = "rollback_release"


def _clock() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


class _RollbackAdapter:
    """A WRITE-effect capability: rolling payments-api back. Folds the approved action into a
    Service `deployed` event so the write leaves an auditable graph delta (the mock echoes the
    call params, so a `refine` of the target version is observable)."""

    provider = "ocp"
    intents = frozenset({ROLLBACK_INTENT})
    effect = Effect.WRITE
    binding = Binding.A2A

    def normalize(self, raw: dict) -> list[Operation]:
        return [AddEvent(entity=s1.SVC, type="deployed", occurred_at=s1.T_FIX,
                         observed_at=s1.T_FIX, source=Source.OCP,
                         payload={"action": "rollback", "to_version": raw.get("to_version", "?")})]


class _WriteAwareMock:
    """Hermetic transport: read intents get their fixture; the write intent echoes its params
    (so an approved/refined rollback carries the operator's chosen version into the fold)."""

    def __init__(self, fixtures: dict | None = None) -> None:
        self._fx = fixtures or {}

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        if intent == ROLLBACK_INTENT:
            return dict(params)
        raw = self._fx.get(intent, {})
        return raw if isinstance(raw, dict) else {"records": raw}


def _layer() -> CapabilityLayer:
    return CapabilityLayer([*default_adapters(), _RollbackAdapter()], source=_WriteAwareMock())


def _session(script: list, subject) -> InvestigationSession:
    pb = load_playbook(PLAYBOOK)
    return InvestigationSession(subject, pb, ScriptedPlanner(script),
                               layer=_layer(), clock=_clock)


def _approve_script():
    """The happy path with a WRITE-effect rollback injected into REMEDIATE."""
    subject, script = s1.build()
    script[4] = script[4].model_copy(update={"calls": [call(ROLLBACK_INTENT, to_version="v4.11.3")]})
    return subject, script


def _deny_script():
    """Diverges after the gate: the rollback is refused, the symptom persists, h1 is never
    confirmed → the investigation closes MITIGATED rather than RESOLVED."""
    subject, base = s1.build()
    remediate = base[4].model_copy(update={"calls": [call(ROLLBACK_INTENT, to_version="v4.11.3")]})
    verify = phase("verify", [
        fact(s1.SVC, "red_errors", 0.35, s1.T_FIX, source=Source.PROMETHEUS, reliability=0.98),
        update("h1", status="supported", level="high",
               basis="rollback declined; 5xx still ~35% — cause known, impact not cleared"),
    ], "Rollback declined; symptom persists. Cause identified but incident not resolved.")
    close = phase("close", [], "Closed MITIGATED: root cause identified (abc123 NPE) but the "
                  "proposed remediation was declined.", status="done")
    return subject, [base[0], base[1], base[2], base[3], remediate, verify, close]


# ── the gate suspension + event stream ─────────────────────────────────────────
def test_session_suspends_at_write_gate():
    subject, script = _approve_script()
    session = _session(script, subject)

    events = session.advance()   # runs FRAME..INVESTIGATE, then pauses BEFORE the write
    assert session.state == SessionState.SUSPENDED

    # the run got as far as REMEDIATE and no further (the write was NOT applied)
    assert session._engine.current_phase.value == "remediate"
    assert not any(e.type == "deployed" for e in session._engine.graph.events.values())

    # exactly one gate is open, carrying the proposed action + the serving hypothesis + evidence
    gates = [e for e in events if e["type"] == "gate_opened"]
    assert len(gates) == 1
    gate = gates[0]
    assert gate["actions"][0]["intent"] == ROLLBACK_INTENT
    assert gate["actions"][0]["effect"] == "write"
    assert gate["hypothesis"]["id"] == "hyp:h1"            # the leader the write serves
    assert gate["evidence"], "gate must cite the serving hypothesis's supporting facts"

    # the event stream is the derived-delta shape, and a graph_delta node carries created_by
    types = [e["type"] for e in events]
    for expected in ("phase_started", "reasoning", "graph_delta", "ledger_delta",
                     "session_state", "gate_opened"):
        assert expected in types, f"missing {expected} event"
    node_deltas = [n for e in events if e["type"] == "graph_delta" for n in e["nodes"]]
    assert node_deltas, "expected nodes in graph_delta"
    assert all(isinstance(n["created_by"], int) and n["created_by"] >= 1 for n in node_deltas)

    # the last state event marks the suspension
    state_events = [e for e in events if e["type"] == "session_state"]
    assert state_events[-1]["state"] == "suspended"


# ── approve → apply + continue → RESOLVED ──────────────────────────────────────
def test_session_approve_resolves():
    subject, script = _approve_script()
    session = _session(script, subject)
    session.advance()
    assert session.state == SessionState.SUSPENDED

    resume = session.answer_gate(GateDecision.APPROVE)
    assert session.state == SessionState.CLOSED
    assert session.outcome == "resolved"

    # the approved write executed (gate-first, under the approved gate) and folded a rollback event
    caps = [e for e in resume if e["type"] == "capability_call" and e["intent"] == ROLLBACK_INTENT]
    assert caps and caps[0]["effect"] == "write" and not caps[0]["blocked"] and caps[0]["op_count"] == 1
    rollbacks = [e for e in session._engine.graph.events.values()
                 if e.type == "deployed" and e.payload.get("action") == "rollback"]
    assert len(rollbacks) == 1 and rollbacks[0].payload["to_version"] == "v4.11.3"

    # the hypothesis was confirmed and the incident resolved
    assert session._engine.ledger.confirmed().id == "hyp:h1"

    # journal-as-truth: the session state replays exactly from its journal (reconstructable)
    g2, led2 = rebuild(session._engine.journal)
    assert g2.to_dict() == session._engine.graph.to_dict()
    assert {h: v.status for h, v in led2.hypotheses.items()} == \
           {h: v.status for h, v in session._engine.ledger.hypotheses.items()}

    # snapshot is export_bundle-shaped for cold-load
    snap = session.snapshot()
    assert snap["outcome"] == "resolved"
    assert snap["session_id"] == subject.key
    assert snap["state"] == "closed"
    assert "graph" in snap and "ledger" in snap and "journal" in snap


# ── deny → drop the write, feed the denial back → DIVERGENT journal → MITIGATED ─
def test_session_deny_diverges():
    a_subject, a_script = _approve_script()
    approved = _session(a_script, a_subject)
    approved.advance()
    approved.answer_gate(GateDecision.APPROVE)

    d_subject, d_script = _deny_script()
    denied = _session(d_script, d_subject)
    denied.advance()
    assert denied.state == SessionState.SUSPENDED

    denied.answer_gate(GateDecision.DENY, reason="too risky during peak traffic")
    assert denied.state == SessionState.CLOSED

    # the write was NEVER applied on the deny branch
    assert not any(e.type == "deployed" for e in denied._engine.graph.events.values())

    # the denial was recorded as a synthetic ledger result fed back (visible in the journal)
    remediate = [e for e in denied._engine.journal.phase_entries() if e.phase_id.value == "remediate"]
    assert remediate and "DENIED" in remediate[0].reasoning

    # the outcome + the journals genuinely DIVERGE from the approve branch
    assert denied.outcome == "mitigated" and approved.outcome == "resolved"
    a_journal = [(e.phase_id.value, e.reasoning) for e in approved._engine.journal.phase_entries()]
    d_journal = [(e.phase_id.value, e.reasoning) for e in denied._engine.journal.phase_entries()]
    assert a_journal != d_journal
    assert denied._engine.ledger.confirmed() is None    # nothing confirmed without the fix

    # the deny run still replays exactly from its own journal
    g2, _ = rebuild(denied._engine.journal)
    assert g2.to_dict() == denied._engine.graph.to_dict()


# ── refine → edit params then apply ────────────────────────────────────────────
def test_session_refine_edits_params():
    subject, script = _approve_script()
    session = _session(script, subject)
    session.advance()

    session.answer_gate(GateDecision.REFINE, params={"to_version": "v4.11.2"})
    assert session.state == SessionState.CLOSED
    rollbacks = [e for e in session._engine.graph.events.values()
                 if e.type == "deployed" and e.payload.get("action") == "rollback"]
    assert rollbacks and rollbacks[0].payload["to_version"] == "v4.11.2"   # the refined target
