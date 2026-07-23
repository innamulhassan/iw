"""P6 step 5 (part2 §3): transition-event derivation + the zombie-session fixes.

Derivation: a boolean STATE flip derives `<name>_started`/`<name>_cleared` in the reducer —
tunable-gated (default OFF: several shipped scenarios deliberately model stacks without
transition events, and their goldens must stay byte-identical), dictionary-known names only,
authored twins win by event-id dedup, and derived events RIDE THE DELTA so replay is exact.

Zombies: max-steps exhaustion and terminal closes write durable `lifecycle` entries and CLOSE
the session (ending the SSE stream) instead of leaving it RUNNING forever.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

from e2e import scenario_code_regression as s1
from e2e._helpers import fact, node, phase

import iw_engine
from iw_engine.domain.enums import NodeType, Source
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph, fold, rebuild
from iw_engine.graph.reducer import materialize
from iw_engine.hypothesis import HypothesisStore
from iw_engine.journal import Journal
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.session import InvestigationSession, SessionState

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=40)
SVC = "service:payments-api|prod"

DERIVE = Tunables(derive_transitions=True)


def _seeded() -> Graph:
    g = Graph()
    mat = materialize([node(NodeType.SERVICE, service_name="payments-api", env="prod")],
                      1, g, DERIVE)
    for n in mat.nodes:
        g.upsert_node(n)
    return g


def test_flip_to_true_derives_started():
    g = _seeded()
    mat = materialize([fact(SVC, "degraded", True, T0)], 2, g, DERIVE)
    derived = [e for e in mat.events if e.type == "degraded_started"]
    assert len(derived) == 1
    ev = derived[0]
    assert ev.entity_ref == SVC and ev.occurred_at == T0 and ev.payload == {}
    assert ev.source == Source.PROMETHEUS          # mirrors the fact's provenance
    assert ev.created_by == 2


def test_flip_to_false_derives_cleared_only_after_true():
    g = _seeded()
    for n_seq, (val, at) in enumerate(((True, T0), (False, T1)), start=2):
        mat = materialize([fact(SVC, "degraded", val, at)], n_seq, g, DERIVE)
        for f in mat.facts:
            g.add_fact(f)
        for e in mat.events:
            g.add_event(e)
    assert any(e.type == "degraded_cleared" and e.occurred_at == T1
               for e in g.events.values())


def test_no_flip_no_event():
    g = _seeded()
    m1 = materialize([fact(SVC, "degraded", True, T0)], 2, g, DERIVE)
    for f in m1.facts:
        g.add_fact(f)
    for e in m1.events:
        g.add_event(e)
    # re-assert True → no second started; a never-started False → no cleared
    m2 = materialize([fact(SVC, "degraded", True, T1)], 3, g, DERIVE)
    assert not m2.events
    g2 = _seeded()
    m3 = materialize([fact(SVC, "degraded", False, T0)], 2, g2, DERIVE)
    assert not m3.events


def test_authored_twin_wins_no_duplicate():
    """The dual-authoring era stays byte-safe: an authored twin in the same batch suppresses
    the derived one (same event id — entity, name, instant)."""
    from e2e._helpers import event as authored_event

    g = _seeded()
    ops = [fact(SVC, "degraded", True, T0),
           authored_event(SVC, "degraded_started", T0, source=Source.PROMETHEUS)]
    mat = materialize(ops, 2, g, DERIVE)
    started = [e for e in mat.events if e.type == "degraded_started"]
    assert len(started) == 1                        # ONE record, the authored one
    assert started[0].source == Source.PROMETHEUS


def test_unknown_transition_name_is_never_fabricated():
    """`enabled` flips have no dictionary-known transition events — the engine must not mint
    a name the closed vocabulary lacks (no self-fabricated quarantine spellings)."""
    g = Graph()
    mat0 = materialize([node(NodeType.FEATURE_FLAG, flag_key="new-pricing", env="prod")],
                       1, g, DERIVE)
    for n in mat0.nodes:
        g.upsert_node(n)
    flag = mat0.nodes[0].id
    mat = materialize([fact(flag, "enabled", True, T0, source=Source.OCP)], 2, g, DERIVE)
    assert mat.events == []


def test_default_off_derives_nothing():
    """The golden guard: with the knob at its default, boolean flips derive nothing — the
    shipped scenarios' event sets are untouched (deployment/infra/network model stacks
    WITHOUT transition events; their goldens hold)."""
    g = _seeded()
    mat = materialize([fact(SVC, "degraded", True, T0)], 2, g, Tunables())
    assert mat.events == []


def test_derived_events_ride_the_delta_and_replay():
    """Derivation is replay-exact BY CONSTRUCTION: the derived event is materialized into the
    PhaseResult delta (journaled), never re-derived at fold time — so a rebuild with ANY
    tunables reproduces it bit-for-bit."""
    from iw_engine.domain.common import Confidence
    from iw_engine.domain.enums import GateResult, VerdictStatus
    from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict

    g, store, jr = Graph(), HypothesisStore(), Journal(clock=lambda: T0)
    # ONE batch: the node + the flipping fact — the derived event lands in the SAME
    # Materialized delta (the standard adapter batch shape).
    mat = materialize([node(NodeType.SERVICE, service_name="payments-api", env="prod"),
                       fact(SVC, "degraded", True, T0)], 1, g, DERIVE)
    assert any(e.type == "degraded_started" for e in mat.events)
    delta = PhaseResult(
        phase_id="frame", goal_restated="", nodes_touched=mat.nodes,
        facts_added=mat.facts, events_added=mat.events, narrative="flip",
        verdict=PhaseVerdict(status=VerdictStatus.ADVANCE,
                             confidence=Confidence(value=0.9, basis="x"),
                             gate_result=GateResult.PASS))
    fold(delta, jr.reserve_seq(), g, store, jr)
    assert any(e.type == "degraded_started" for e in g.events.values())
    g2, _ = rebuild(jr)                             # NO tunables — the record still replays
    assert g2.to_dict() == g.to_dict()


# ── zombie fixes ───────────────────────────────────────────────────────────────
def _clock() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


def _terminal_closed(session):
    """The run's terminal lifecycle records (M17: every terminal path emits ONE `closed`)."""
    return [e for e in session._engine.journal.entries
            if e.kind == "lifecycle" and e.action.get("event") == "closed"]


def test_max_steps_exhaustion_closes_with_lifecycle():
    """The RUNNING-forever zombie dies: exhausting max_steps CLOSES the session (ending the SSE
    stream — the server loop exits on CLOSED) and journals WHY via the ONE terminal record
    (event=closed, cause=exhausted). M17 folded the old distinct `max_steps_exhausted` event and
    the ad-hoc session_state `reason` into the unified closed-record + `cause`."""
    subject, script = s1.build()
    pb = load_playbook(PLAYBOOK)
    session = InvestigationSession(subject, pb, ScriptedPlanner(script),
                                   clock=_clock, max_steps=2)
    session.advance()
    assert session.state == SessionState.CLOSED, "exhaustion must close, never zombie"
    closed = _terminal_closed(session)
    assert len(closed) == 1 and closed[0].action["cause"] == "exhausted"
    assert closed[0].phase_id is not None           # WHERE it starved is on the record
    states = [e for e in session.events() if e["type"] == "session_state"]
    assert states[-1]["state"] == "closed" and states[-1]["cause"] == "exhausted"


def test_normal_close_writes_terminal_lifecycle():
    subject, script = s1.build()
    pb = load_playbook(PLAYBOOK)
    session = InvestigationSession(subject, pb, ScriptedPlanner(script), clock=_clock)
    session.advance()
    assert session.state == SessionState.CLOSED
    closed = _terminal_closed(session)
    assert len(closed) == 1                          # M17: exactly one terminal record
    assert closed[0].action["cause"] == "finished"   # reached the terminal cleanly
    assert closed[0].decision == "resolved"          # the terminal outcome rides the record


def test_blocked_verdict_closes_with_lifecycle():
    """An unrouted BLOCKED verdict drains the phase route — the session must CLOSE with the ONE
    terminal lifecycle record (event=closed, outcome open), never hang. The engine journals the
    `unrouted_verdict` separately; the terminal record's cause is `finished` (the route ran out).
    (Routing BLOCKED somewhere better is P7's phase work; the diagnosable-close half lands here.)"""
    subject, base = s1.build()
    script = [base[0], phase("investigate", [], "cannot proceed: access denied", status="blocked")]
    pb = load_playbook(PLAYBOOK)
    session = InvestigationSession(subject, pb, ScriptedPlanner(script), clock=_clock)
    session.advance()
    assert session.state == SessionState.CLOSED
    assert session.outcome == "open"
    closed = _terminal_closed(session)
    assert len(closed) == 1
    assert closed[0].action["cause"] == "finished" and closed[0].decision == "open"


def test_errored_drive_closes_with_error_cause_and_outcome():
    """M17 + M18: a live drive that crashes mid-run funnels through the SAME terminal path — state
    CLOSED and ONE `closed` record with cause=error — and sets outcome='error' (NOT the default
    'open') on both `_outcome` and list_view(), so a crashed run never masquerades as still-open."""
    subject, _ = s1.build()
    pb = load_playbook(PLAYBOOK)

    class _BoomPlanner:
        def plan(self, ctx):
            raise RuntimeError("live transport died mid-drive")

    session = InvestigationSession(subject, pb, _BoomPlanner(), clock=_clock)
    session._drive_and_clear()                       # the background error path, run synchronously
    assert session.state == SessionState.CLOSED
    assert session.outcome == "error" and session.list_view()["outcome"] == "error"
    closed = _terminal_closed(session)
    assert len(closed) == 1 and closed[0].action["cause"] == "error"
    # the crash was announced on the stream, then the unified close (cause=error)
    types = [e["type"] for e in session.events()]
    assert "session_error" in types
    states = [e for e in session.events() if e["type"] == "session_state"]
    assert states[-1]["state"] == "closed" and states[-1]["cause"] == "error"
