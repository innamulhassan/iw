"""Reducer partial-accept tests — one mixed batch of valid + illegal ops must fold the
valid ops and record ONE exact rejection per illegal op (never a crash, never all-or-
nothing). Plus INV-6: SUPPORTS/REFUTES are derived-only — a planner-emitted AddEdge is
rejected while the fold projects the equivalent edge from the hypothesis's canonical
fact-id lists.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import (
    EdgeType,
    FactState,
    HypothesisStatus,
    NodeType,
    Origin,
    Source,
    Species,
    VerdictStatus,
)
from iw_engine.domain.hypothesis import HypAction, HypDelta, Hypothesis
from iw_engine.domain.node import Node
from iw_engine.domain.operations import (
    AddAssertion,
    AddEdge,
    AddNode,
    ProposeHypothesis,
)
from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
from iw_engine.domain.playbook import Tunables
from iw_engine.domain.registry import edge_id, fact_id
from iw_engine.graph import Graph, fold
from iw_engine.graph.reducer import materialize
from iw_engine.hypothesis import HypothesisStore
from iw_engine.journal import Journal

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
SID = "service:payments-api|prod"
SID2 = "service:checkout-api|prod"


# ── (d) reject+repair aggregation: one batch, valid ops fold, exact rejections ─
def test_materialize_partial_accepts_mixed_batch_with_exact_rejections():
    """One batch mixing: 2 valid nodes + 1 valid fact + 1 valid hypothesis with
    an unknown-subject fact, an illegal-predicate fact, and an illegal edge pair.
    Partial-accept means the 3 bad ops are rejected WITH their exact reasons and
    indices while every valid op still materialises."""
    ops = [
        AddNode(type=NodeType.SERVICE,                                        # 0 valid
                props={"service_name": "payments-api", "env": "prod"}),
        AddNode(type=NodeType.ANOMALY, props={"anomaly_id": "ANOM-1"}),       # 1 valid
        AddAssertion(subject=SID, name="red_errors", value=0.4, species=Species.STATE,  # 2 valid
                     valid_from=T0, observed_at=T0,
                     source=Source.PROMETHEUS, source_reliability=0.95),
        AddAssertion(subject="database:ghost|prod", name="pool_util",         # 3 unknown subject
                     value=0.99, species=Species.STATE, valid_from=T0, observed_at=T0,
                     source=Source.PROMETHEUS, source_reliability=0.95),
        AddAssertion(subject="anomaly:anom-1", name="degraded", value=True,   # 4 illegal predicate
                     species=Species.STATE, valid_from=T0, observed_at=T0,
                     source=Source.PROMETHEUS, source_reliability=0.95),
        AddEdge(type=EdgeType.DEPENDS_ON, src=SID, dst="anomaly:anom-1"),     # 5 illegal edge pair
        ProposeHypothesis(hid="h1", statement="bad change",                   # 6 valid
                          root_candidate=SID, confidence_level="med"),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())

    # the valid ops all folded: 2 explicit nodes + the hypothesis's own graph node
    assert sorted(n.id for n in mat.nodes) == ["anomaly:anom-1", "hyp:h1", SID]
    # P2: the reducer canonicalizes red_errors -> error_rate (the 7->1 merge) while the vendor's
    # own spelling survives on source_native_name; the fact id stays keyed on the native name.
    assert [(f.subject_ref, f.predicate, f.value) for f in mat.facts] == \
           [(SID, "error_rate", 0.4)]
    assert mat.facts[0].source_native_name == "red_errors"
    assert mat.edges == []                       # the only edge op was illegal
    assert len(mat.hyp_deltas) == 1
    assert mat.hyp_deltas[0].action == HypAction.CREATE
    assert mat.hyp_deltas[0].hypothesis.id == "hyp:h1"

    # exactly one rejection per illegal op, with the exact reason + index + kind
    assert [(r.op_index, r.op_kind, r.reason) for r in mat.rejections] == [
        (3, "add_assertion", "unknown subject database:ghost|prod"),
        (4, "add_assertion", "predicate 'degraded' not allowed on anomaly"),
        (5, "add_edge", "illegal edge service-depends_on->anomaly"),
    ]


# ── (e) INV-6: SUPPORTS/REFUTES derived-only, projected by the fold ────────────
def _service_node() -> Node:
    return Node(id=SID, type=NodeType.SERVICE,
                props={"service_name": "payments-api", "env": "prod"}, created_by=1)


def test_inv6_planner_emitted_supports_edge_rejected_but_fold_derives_it():
    """INV-6 negative: a planner-authored SUPPORTS/REFUTES AddEdge is rejected as
    derived-only EVEN with valid endpoints and a confidence — while the exact
    equivalent edge DOES appear once the fold projects it from the hypothesis's
    supporting/refuting fact-id lists. The graph view can never disagree with the
    ledger because only the fold may author evidence edges."""
    # 1) the planner tries to hand-author the evidence edges — both rejected
    seed = [
        AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod"}),
        ProposeHypothesis(hid="h1", statement="s", root_candidate=SID, confidence_level="med"),
    ]
    for etype in (EdgeType.SUPPORTS, EdgeType.REFUTES):
        ops = [*seed, AddEdge(type=etype, src=SID, dst="hyp:h1", confidence_level="high")]
        mat = materialize(ops, 1, Graph(), Tunables())
        assert mat.edges == []
        assert len(mat.rejections) == 1
        assert mat.rejections[0].op_kind == "add_edge"
        assert f"{etype.value} is a derived evidence edge" in mat.rejections[0].reason

    # 2) the fold derives the SAME edges from the canonical fact-id lists
    from iw_engine.domain.fact import Fact
    g, led, jr = Graph(), HypothesisStore(), Journal(clock=lambda: T0)
    f_sup = Fact(id="f-sup", subject_ref=SID, predicate="red_errors", value=0.4,
                 valid_from=T0, observed_at=T0, source=Source.PROMETHEUS,
                 source_reliability=0.95, created_by=1)
    f_ref = Fact(id="f-ref", subject_ref=SID, predicate="degraded", value=False,
                 valid_from=T0, observed_at=T0, source=Source.PROMETHEUS,
                 source_reliability=0.95, created_by=1)
    hyp = Hypothesis(id="hyp:h1", statement="bad change", root_candidate=SID,
                     confidence=Confidence(value=0.6, basis="onset match"),
                     supporting_facts=["f-sup"], refuting_facts=["f-ref"],
                     status=HypothesisStatus.PROPOSED, created_by=1)
    delta = PhaseResult(
        phase_id="hypothesize", goal_restated="g",
        nodes_touched=[_service_node()], facts_added=[f_sup, f_ref],
        hypotheses_updated=[HypDelta(action=HypAction.CREATE, hypothesis=hyp)],
        narrative="n",
        verdict=PhaseVerdict(status=VerdictStatus.ADVANCE,
                             confidence=Confidence(value=0.6, basis="test")))
    fold(delta, jr.reserve_seq(), g, led, jr)

    sup_id = edge_id(EdgeType.SUPPORTS, SID, "hyp:h1", Origin.INFERRED)
    ref_id = edge_id(EdgeType.REFUTES, SID, "hyp:h1", Origin.INFERRED)
    assert sup_id in g.edges and ref_id in g.edges
    for eid, etype in ((sup_id, EdgeType.SUPPORTS), (ref_id, EdgeType.REFUTES)):
        e = g.edges[eid]
        assert e.type == etype
        assert e.origin == Origin.INFERRED           # derived, never planner-emitted
        assert e.src == SID and e.dst == "hyp:h1"
        assert e.confidence == hyp.confidence        # projected from the ledger record


# ── the AddAssertion atom materializes natively (F4 retired the AddFact/AddEvent compat shims) ──
def _svc(name: str) -> AddNode:
    return AddNode(type=NodeType.SERVICE, props={"service_name": name, "env": "prod"})


def test_add_assertion_state_materializes_the_expected_fact():
    """A native AddAssertion (species STATE) materializes the expected Fact — the canonicalized name
    over a native-keyed id, INV-9-defaulted reliability, measured channel (no confidence)."""
    mat = materialize(
        [_svc("payments-api"),
         AddAssertion(subject=SID, name="red_errors", value=0.4, unit="ratio", species=Species.STATE,
                      valid_from=T0, observed_at=T0, source=Source.PROMETHEUS)],
        1, Graph(), Tunables())

    assert len(mat.facts) == 1
    f = mat.facts[0]
    # P2: red_errors canonicalizes -> error_rate; the id stays native-keyed (byte-identical to the
    # pre-canonicalization id — no provenance/ref churn) and the native spelling survives.
    assert (f.subject_ref, f.predicate, f.value, f.unit) == (SID, "error_rate", 0.4, "ratio")
    assert f.source_native_name == "red_errors"
    assert f.id == fact_id(SID, "red_errors", T0)
    # INV-9 default reliability applied (measured channel — reliability set, no confidence)
    assert f.source_reliability is not None and f.confidence is None


def test_add_assertion_event_materializes_event():
    """A native AddAssertion (species EVENT) materializes an Event — name→type, value→payload."""
    mat = materialize(
        [_svc("payments-api"),
         AddAssertion(subject=SID, name="deployed", species=Species.EVENT,
                      value={"key": "v"}, occurred_at=T0, observed_at=T0,
                      source=Source.SERVICENOW)], 1, Graph(), Tunables())
    assert len(mat.events) == 1
    e = mat.events[0]
    assert (e.entity_ref, e.type, e.payload) == (SID, "deployed", {"key": "v"})
    assert e.state == FactState.ACTIVE


def test_edge_subject_assertion_is_reachable():
    """known() learns edge subjects (F11): an edge-borne assertion on a same-batch CALLS edge is
    materialized (not rejected as an unknown subject). M30: edge-predicate legality IS now enforced
    (parallel to node `applies_to`) — but `call_error_rate` canonicalizes to `error_rate`, a LEGAL
    CALLS predicate, so it is admitted and lands on the edge id."""
    eid = edge_id(EdgeType.CALLS, SID, SID2, Origin.DISCOVERED)
    ops = [
        _svc("payments-api"),
        _svc("checkout-api"),
        AddEdge(type=EdgeType.CALLS, src=SID, dst=SID2),
        AddAssertion(subject=eid, name="call_error_rate", value=0.2, species=Species.STATE,
                     valid_from=T0, observed_at=T0, source=Source.PROMETHEUS),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    # P2: call_error_rate canonicalizes to error_rate (the CALLS-edge error-rate alias) — a
    # canonical predicate the CALLS EdgeSpec's fact_predicates admits (M30). The native spelling
    # survives on source_native_name.
    assert [(f.subject_ref, f.predicate, f.value) for f in mat.facts] == \
           [(eid, "error_rate", 0.2)]
    assert mat.facts[0].source_native_name == "call_error_rate"


def test_edge_borne_illegal_predicate_is_governed():
    """M30 — the ungoverned lane closed: a KNOWN predicate that is NOT legal on the edge type now
    REJECTS (parallel to a node's applies_to), so the otherwise-closed vocabulary has no free lane
    through edge subjects. `cpu_utilization` is a real Host/Pod reading but is not something a CALLS
    edge carries.

    (The example moved off `degraded` in the SpanFold phase: 2026-07-23 primitives §4.2 Rung 0
    made a `degraded` CALLS a legitimate state-of-the-relationship datum ABOUT the edge, so it is
    now IN CALLS.fact_predicates. The test's guarantee is unchanged — a known predicate illegal on
    the edge still rejects with its exact reason; only the illegal exemplar was re-picked.)"""
    eid = edge_id(EdgeType.CALLS, SID, SID2, Origin.DISCOVERED)
    ops = [
        _svc("payments-api"),
        _svc("checkout-api"),
        AddEdge(type=EdgeType.CALLS, src=SID, dst=SID2),
        AddAssertion(subject=eid, name="cpu_utilization", value=0.9, species=Species.STATE,
                     valid_from=T0, observed_at=T0, source=Source.PROMETHEUS),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.facts == []
    assert [r.reason for r in mat.rejections] == \
        ["predicate 'cpu_utilization' not allowed on edge calls"]


def test_edge_borne_predicate_on_ungoverned_edge_type_rejects():
    """An edge type whose spec declares NO fact_predicates (DEPENDS_ON) carries no edge-borne facts
    at all — any known predicate rejects. Only CALLS opts a discovered RED lane in (§C2)."""
    eid = edge_id(EdgeType.DEPENDS_ON, SID, "database:orders-db", Origin.DECLARED)
    ops = [
        _svc("payments-api"),
        AddNode(type=NodeType.DATABASE, props={"db_id": "orders-db"}),
        AddEdge(type=EdgeType.DEPENDS_ON, src=SID, dst="database:orders-db"),
        AddAssertion(subject=eid, name="error_rate", value=0.2, species=Species.STATE,
                     valid_from=T0, observed_at=T0, source=Source.PROMETHEUS),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.facts == []
    assert [r.reason for r in mat.rejections] == ["predicate 'error_rate' not allowed on edge depends_on"]


def test_edge_subject_assertion_rejected_when_edge_absent():
    """The mirror: an assertion on an edge that never entered the graph is still rejected."""
    eid = edge_id(EdgeType.CALLS, SID, SID2, Origin.DISCOVERED)
    ops = [
        _svc("payments-api"),
        AddAssertion(subject=eid, name="call_error_rate", value=0.2, species=Species.STATE,
                     valid_from=T0, observed_at=T0, source=Source.PROMETHEUS),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.facts == []
    assert [(r.op_index, r.op_kind) for r in mat.rejections] == [(1, "add_assertion")]
    assert mat.rejections[0].reason == f"unknown subject {eid}"
