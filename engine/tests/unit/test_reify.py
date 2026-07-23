"""Rung-2 REIFY + the ABANDONED reaper (NODE-EDGE-PRIMITIVES 2026-07-23 §4.6/§4.7).

The engine mechanism (never LLM-authored): a SPAN datum that earns identity PROMOTES to a reified
occurrence NODE carrying its own SPAN self-assertion, with participants attached via PARTICIPATED_IN
edges whose windows ARE the participants' involvement sub-intervals. The reaper marks OPEN spans
whose close was lost — a DETERMINISTIC decision off a passed replay clock, never a wall-clock read.
Reification is exercised through the REAL fold, with journal-replay equivalence asserted.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from iw_engine.domain.assertion import Assertion
from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import (
    Channel,
    EdgeType,
    FactState,
    NodeType,
    Origin,
    SpanPhase,
    Species,
    VerdictStatus,
)
from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
from iw_engine.graph import Graph, rebuild
from iw_engine.graph.fold import fold as apply_fold
from iw_engine.graph.reify import (
    Participant,
    reap_abandoned_spans,
    reify_span,
    should_reify,
)
from iw_engine.hypothesis import HypothesisStore
from iw_engine.journal import Journal

T0 = datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)
PAY_ID = "service:payments-api|prod"
CHK_ID = "service:checkout-api|prod"
CALLS_EDGE = "edge:calls:service:payments-api|prod->service:checkout-api|prod:discovered"
BT_ID = "business_transaction:checkout-api|checkout"


def _trace(correlation_id="trace-abc", *, subject=CALLS_EDGE, ended=True) -> Assertion:
    return Assertion(
        id="span:seed", subject_ref=subject, name="trace", value={"error": False},
        species=Species.SPAN, channel=Channel.MEASURED, valid_from=T0,
        valid_to=(T0 + timedelta(milliseconds=800)) if ended else None,
        observed_at=T0, span_phase=SpanPhase.CLOSED if ended else SpanPhase.OPEN,
        correlation_id=correlation_id, source="appd", source_reliability=0.95, created_by=1)


# ── should_reify — the Rung-2 test triple + the §4.3 stable-id precondition ────────────────
def test_should_reify_requires_the_stable_id_precondition():
    idless = _trace(correlation_id=None)   # a span with no correlation_id can never reify (§4.3)
    assert should_reify(idless, arity=9, referenced=True, has_own_children=True) is False


def test_should_reify_fires_on_any_of_the_three_tests_when_id_present():
    s = _trace()
    assert should_reify(s, arity=3) is True                    # (1) arity > 2
    assert should_reify(s, has_own_children=True) is True       # (2) own children/metrics
    assert should_reify(s, referenced=True) is True             # (3) structural referability
    assert should_reify(s, arity=2) is False                    # none hold -> stays a datum
    assert should_reify(_trace().model_copy(update={"species": Species.STATE})) is False


# ── reify_span — mint node + self-span + PARTICIPATED_IN with involvement windows ──────────
def _fold(g, store, jr, seq, *, nodes=(), spans=(), edges=()):
    pr = PhaseResult(
        phase_id="p", goal_restated="", narrative="",
        verdict=PhaseVerdict(status=VerdictStatus.ADVANCE,
                             confidence=Confidence(value=0.9, basis="t")),
        nodes_touched=list(nodes), spans_added=list(spans), edges_added=list(edges))
    apply_fold(pr, seq, g, store, jr)


def test_reify_span_mints_node_self_span_and_participation_windows():
    g, store, jr = Graph(), HypothesisStore(), Journal()
    span = _trace()
    involved = timedelta(milliseconds=300)
    result = reify_span(
        span, NodeType.BUSINESS_TRANSACTION,
        {"service_name": "checkout-api", "bt_name": "Checkout"},
        participants=[
            Participant(PAY_ID, NodeType.SERVICE, T0, T0 + involved),
            Participant(CHK_ID, NodeType.SERVICE, T0 + timedelta(milliseconds=100),
                        span.valid_to),
        ],
        seq=1, edge_reliability=0.9)

    # the reified occurrence NODE, keyed by the BT identity
    assert result.node.id == BT_ID and result.node.type is NodeType.BUSINESS_TRANSACTION
    # its SPAN self-assertion — the occurrence's OWN extent, on the NODE (never an edge, §5.2 F)
    ss = result.self_span
    assert ss.species is Species.SPAN and ss.subject_ref == BT_ID
    assert ss.span_phase is SpanPhase.CLOSED and ss.correlation_id == "trace-abc"
    assert ss.valid_from == T0 and ss.valid_to == span.valid_to    # extent preserved
    # two PARTICIPATED_IN edges, each carrying its participant's INVOLVEMENT sub-interval
    parts = {e.src: e for e in result.participations}
    assert set(parts) == {PAY_ID, CHK_ID}
    assert all(e.type is EdgeType.PARTICIPATED_IN and e.dst == BT_ID
               and e.origin is Origin.DISCOVERED for e in parts.values())
    assert parts[PAY_ID].valid_from == T0 and parts[PAY_ID].valid_to == T0 + involved
    assert parts[CHK_ID].valid_to == span.valid_to                 # distinct per-participant window

    # fold it and prove the reification round-trips through the journal bit-for-bit
    _fold(g, store, jr, 1, nodes=[result.node], spans=[result.self_span],
          edges=result.participations)
    assert g.node(BT_ID) is not None
    got = g.spans_of(BT_ID)
    assert len(got) == 1 and got[0].span_phase is SpanPhase.CLOSED
    assert len(g.in_edges(BT_ID, EdgeType.PARTICIPATED_IN)) == 2
    g2, _ = rebuild(jr)
    assert g2.to_dict() == g.to_dict()


def test_reify_span_refuses_an_idless_span_and_illegal_participants():
    with pytest.raises(ValueError, match="correlation_id"):
        reify_span(_trace(correlation_id=None), NodeType.BUSINESS_TRANSACTION,
                   {"service_name": "s", "bt_name": "b"}, seq=1)
    with pytest.raises(ValueError, match="illegal PARTICIPATED_IN"):
        reify_span(_trace(), NodeType.BUSINESS_TRANSACTION,
                   {"service_name": "s", "bt_name": "b"},
                   participants=[Participant("host:h1", NodeType.HOST, T0, None)], seq=1)


# ── reap_abandoned_spans — deterministic, off a passed replay clock (§4.6) ─────────────────
def _open(name="trace", started=T0, sid="span:o") -> Assertion:
    return Assertion(id=sid, subject_ref=CALLS_EDGE, name=name, species=Species.SPAN,
                     channel=Channel.MEASURED, valid_from=started, valid_to=None,
                     observed_at=started, span_phase=SpanPhase.OPEN, correlation_id="c",
                     source="appd", source_reliability=0.95, created_by=1)


def test_reaper_marks_only_open_spans_past_their_ttl():
    ttls = {"trace": timedelta(minutes=5)}
    fresh = _open(started=T0, sid="span:fresh")
    stale = _open(started=T0 - timedelta(minutes=10), sid="span:stale")
    closed = _open(sid="span:closed").model_copy(
        update={"valid_to": T0, "span_phase": SpanPhase.CLOSED})
    reaped = reap_abandoned_spans([fresh, stale, closed], now=T0, ttls=ttls)
    # only the stale OPEN span is reaped; it keeps valid_to=None (close LOST, not ended)
    assert [r.id for r in reaped] == ["span:stale"]
    assert reaped[0].span_phase is SpanPhase.ABANDONED and reaped[0].valid_to is None
    # deterministic: identical inputs -> identical decision (a passed clock, never wall-clock)
    assert reap_abandoned_spans([fresh, stale, closed], now=T0, ttls=ttls) == reaped
    # a span whose name has no TTL (and no default) is left OPEN
    assert reap_abandoned_spans([stale], now=T0, ttls={}) == []


def test_reaper_abandon_is_overwritten_by_a_late_close_in_place():
    g, store, jr = Graph(), HypothesisStore(), Journal()
    stale = _open(started=T0 - timedelta(minutes=10), sid="span:x")
    _fold(g, store, jr, 1, spans=[stale])                          # OPEN
    reaped = reap_abandoned_spans(list(g.spans.values()), now=T0,
                                  ttls={"trace": timedelta(minutes=5)})
    _fold(g, store, jr, 2, spans=reaped)                           # reaper -> ABANDONED (same id)
    assert g.spans_of(CALLS_EDGE)[0].span_phase is SpanPhase.ABANDONED
    # a real close arrives late: same started_at -> same span_id -> overwrites ABANDONED -> CLOSED
    late_close = stale.model_copy(update={"valid_to": T0, "span_phase": SpanPhase.CLOSED,
                                          "created_by": 3})
    _fold(g, store, jr, 3, spans=[late_close])
    final = g.spans_of(CALLS_EDGE)
    assert len(final) == 1 and final[0].span_phase is SpanPhase.CLOSED
    assert final[0].state is FactState.ACTIVE
    g2, _ = rebuild(jr)
    assert g2.to_dict() == g.to_dict()
