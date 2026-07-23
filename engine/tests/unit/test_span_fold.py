"""SPAN residence + the fold (NODE-EDGE-PRIMITIVES 2026-07-23 §2.6/§4 — the SpanFold phase).

The sixth species already existed in the atom + the graph views (Species.SPAN, SpanPhase,
`spans_of`); what was missing was the FOLD PATH — the reducer's `emit_assertion` only branched
EVENT-vs-Fact, so a `species=SPAN` op silently flowed through the Fact path and lost its
span_phase/correlation_id. These tests lock in the residence LADDER now that the path exists:

  * Rung 0 — a degenerate single-subject interval is a STATE datum on ONE subject, and that
    subject may be an EDGE ("this CALLS was degraded t1->t2" — a state-of-the-relationship,
    NOT the edge's own lifecycle). It stays a Fact, never a span.
  * Rung 1 — an atomic 2-party hop is a SPAN datum whose subject_ref is the discovered CALLS
    EDGE, carrying a correlation_id; a node-borne happening (a trace on a BT) is a SPAN whose
    subject is the node. The engine DERIVES span_phase (OPEN in-flight -> CLOSED once ended);
    the OPEN datum and its later CLOSED datum share one started_at -> one span_id, so the close
    overwrites the open IN PLACE (two-phase-then-frozen).

Every path is exercised through the REAL fold + journal, and each test asserts journal-replay
equivalence — a span round-trips through to_dict/from_dict and rebuild() bit-for-bit.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import (
    EdgeType,
    FactState,
    NodeType,
    Origin,
    SpanPhase,
    Species,
    VerdictStatus,
)
from iw_engine.domain.operations import AddAssertion, AddEdge, AddNode
from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
from iw_engine.domain.playbook import Tunables
from iw_engine.domain.registry import edge_id, span_id
from iw_engine.graph import Graph, rebuild
from iw_engine.graph.fold import fold as apply_fold
from iw_engine.graph.reducer import materialize
from iw_engine.hypothesis import HypothesisStore
from iw_engine.journal import Journal

T0 = datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(milliseconds=800)
PAY = {"service_name": "payments-api", "env": "prod"}
CHK = {"service_name": "checkout-api", "env": "prod"}
PAY_ID = "service:payments-api|prod"
CHK_ID = "service:checkout-api|prod"
BT = {"service_name": "checkout-api", "bt_name": "Checkout"}
BT_ID = "business_transaction:checkout-api|checkout"


def _span_op(subject: str, name: str, started_at, *, ended_at=None, value=None,
             correlation_id=None):
    return AddAssertion(subject=subject, name=name, value=value, species=Species.SPAN,
                        valid_from=started_at, valid_to=ended_at,
                        observed_at=ended_at or started_at, correlation_id=correlation_id,
                        source="appd", source_reliability=0.95, source_native_name=name)


class _World:
    """A tiny live projection driven through the REAL fold + journal, so every test also proves
    journal-replay equivalence (rebuild() must reproduce the graph bit-for-bit)."""

    def __init__(self) -> None:
        self.graph = Graph()
        self.store = HypothesisStore()
        self.journal = Journal()
        self.seq = 0

    def fold(self, ops):
        self.seq += 1
        mat = materialize(ops, self.seq, self.graph, Tunables())
        pr = PhaseResult(
            phase_id="p", goal_restated="", narrative="",
            verdict=PhaseVerdict(status=VerdictStatus.ADVANCE,
                                 confidence=Confidence(value=0.9, basis="t")),
            facts_added=mat.facts, events_added=mat.events, spans_added=mat.spans,
            nodes_touched=mat.nodes, edges_added=mat.edges,
            hypotheses_updated=mat.hyp_deltas, retractions=mat.retractions,
            remaps=mat.remaps, rejections=mat.rejections)
        apply_fold(pr, self.seq, self.graph, self.store, self.journal)
        return mat

    def assert_replays(self) -> None:
        g2, _ = rebuild(self.journal)
        assert g2.to_dict() == self.graph.to_dict(), "span did not replay bit-for-bit"


def _calls_world() -> tuple[_World, str]:
    """Two services + a discovered CALLS edge — the subject a Rung-0 STATE / Rung-1 hop addresses."""
    w = _World()
    w.fold([
        AddNode(type=NodeType.SERVICE, props=PAY),
        AddNode(type=NodeType.SERVICE, props=CHK),
        AddEdge(type=EdgeType.CALLS, src=PAY_ID, dst=CHK_ID, origin=Origin.DISCOVERED),
    ])
    return w, edge_id(EdgeType.CALLS, PAY_ID, CHK_ID, Origin.DISCOVERED)


# ── Rung 0 — a STATE-of-the-relationship datum ABOUT the edge (not a span) ─────────────────
def test_rung0_state_on_a_calls_edge_is_a_fact_never_a_span():
    w, calls = _calls_world()
    mat = w.fold([AddAssertion(subject=calls, name="degraded", value=True, species=Species.STATE,
                               valid_from=T0, valid_to=T1, observed_at=T0,
                               source="appd", source_reliability=0.95,
                               source_native_name="degraded")])
    # it materialized as a FACT on the edge — the degenerate single-subject interval (Rung 0),
    # NOT a span and NOT the edge's own lifecycle.
    assert len(mat.facts) == 1 and not mat.spans
    f = mat.facts[0]
    assert f.subject_ref == calls and f.predicate == "degraded" and f.value is True
    assert w.graph.facts_of(calls) and not w.graph.spans_of(calls)   # a fact, no span
    w.assert_replays()


# ── Rung 1 — an atomic hop SPAN whose subject is the discovered CALLS edge ─────────────────
def test_rung1_hop_span_addresses_the_edge_and_the_engine_derives_open():
    w, calls = _calls_world()
    mat = w.fold([_span_op(calls, "hop", T0, correlation_id="trace-abc")])
    # routed to spans, NOT facts/events
    assert len(mat.spans) == 1 and not mat.facts and not mat.events
    s = mat.spans[0]
    assert s.species is Species.SPAN
    assert s.subject_ref == calls                       # subject is the EDGE (§4.1)
    assert s.span_phase is SpanPhase.OPEN               # engine-derived: no ended_at -> OPEN
    assert s.valid_to is None and s.duration is None    # in-flight
    assert s.correlation_id == "trace-abc"              # §4.4 join key preserved
    assert s.id == span_id(calls, "hop", T0)
    # the query surface exposes the raw atom (phase visible) and finds it under the edge subject
    got = w.graph.spans_of(calls)
    assert len(got) == 1 and got[0].span_phase is SpanPhase.OPEN
    assert not w.graph.facts_of(calls)                  # a span is not a fact
    w.assert_replays()


def test_rung1_late_close_overwrites_the_open_in_place_two_phase_then_frozen():
    w, calls = _calls_world()
    w.fold([_span_op(calls, "hop", T0, correlation_id="trace-abc")])            # OPEN
    open_id = w.graph.spans_of(calls)[0].id
    # the CLOSE shares the same started_at -> same span_id -> overwrites in place
    w.fold([_span_op(calls, "hop", T0, ended_at=T1, value={"status": "ok"},
                     correlation_id="trace-abc")])
    closed = w.graph.spans_of(calls)
    assert len(closed) == 1                             # overwrote, never appended a twin
    c = closed[0]
    assert c.id == open_id                              # same stable id
    assert c.span_phase is SpanPhase.CLOSED             # engine-derived from ended_at
    assert c.valid_to == T1 and c.duration == T1 - T0   # frozen extent + duration
    assert c.value == {"status": "ok"} and c.correlation_id == "trace-abc"
    w.assert_replays()


# ── Rung 1 — a node-borne happening (a trace on a BusinessTransaction) ─────────────────────
def test_rung1_trace_span_on_a_bt_node_is_findable_and_phase_exposed():
    w = _World()
    w.fold([AddNode(type=NodeType.BUSINESS_TRANSACTION, props=BT)])
    w.fold([_span_op(BT_ID, "trace", T0, ended_at=T1, value={"error": False},
                     correlation_id="trace-xyz")])
    got = w.graph.spans_of(BT_ID)
    assert len(got) == 1
    s = got[0]
    assert s.species is Species.SPAN and s.subject_ref == BT_ID
    assert s.span_phase is SpanPhase.CLOSED             # CLOSED — arrived with an ended_at
    assert s.correlation_id == "trace-xyz"
    assert s.duration == T1 - T0
    w.assert_replays()


# ── §4.6 universality invariant — every returned span exposes a phase ─────────────────────
def test_every_returned_span_exposes_span_phase():
    w, calls = _calls_world()
    w.fold([_span_op(calls, "hop", T0, correlation_id="c1")])                    # OPEN, edge-borne
    w.fold([AddNode(type=NodeType.BUSINESS_TRANSACTION, props=BT)])
    w.fold([_span_op(BT_ID, "trace", T0, ended_at=T1, correlation_id="c2")])     # CLOSED, node-borne
    every = w.graph.spans_of(calls) + w.graph.spans_of(BT_ID)
    assert len(every) == 2
    assert all(s.span_phase in (SpanPhase.OPEN, SpanPhase.CLOSED, SpanPhase.ABANDONED)
               for s in every)                          # never None — an ABANDONED span never
    assert all(s.state is FactState.ACTIVE for s in every)   # reads as 'ongoing' by omission
    w.assert_replays()
