"""P4 belief arithmetic (DOMAIN-v3 §2.5): the evidence-weight function — reliability x
temporal proximity (skew-tolerant, R-J2) x topological specificity (structural BFS) — and
the weighted for-minus-against blend with the LLM band as the prior. Every knob comes from
Tunables (INV-9): the tests move a knob and assert the arithmetic follows.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.common import Confidence
from iw_engine.domain.edge import Edge
from iw_engine.domain.enums import EdgeType, FactState, NodeType, Origin, Source
from iw_engine.domain.fact import Fact
from iw_engine.domain.hypothesis import Hypothesis
from iw_engine.domain.node import Node
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.hypothesis import belief

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)

ANOM = "anomaly:anom-1"
SVC = "service:orders-api|prod"
DB = "database:orders-pg"
CHG = "change_event:chg-9"
LONER = "database:island-db"          # no structural edge at all


def node(nid: str, t: NodeType, seq=1) -> Node:
    return Node(id=nid, type=t, props={}, created_by=seq)


def fact(fid, subject, predicate, value, ts, *, source=Source.PROMETHEUS,
         reliability=0.9, level=None, state=None, seq=1) -> Fact:
    conf = Confidence(value=level, basis="test") if level is not None else None
    f = Fact(id=fid, subject_ref=subject, predicate=predicate, value=value, valid_from=ts,
             observed_at=ts, source=source,
             source_reliability=None if level is not None else reliability,
             confidence=conf, created_by=seq)
    return f.model_copy(update={"state": state}) if state is not None else f


def edge(eid, etype, src, dst, *, origin=Origin.DISCOVERED, seq=1) -> Edge:
    return Edge(id=eid, type=etype, src=src, dst=dst, origin=origin, created_by=seq)


def graph_fixture() -> Graph:
    """ANOM --AFFECTS--> SVC --DEPENDS_ON--> DB, SVC --CHANGED_BY--> CHG; LONER isolated.
    A CAUSED_BY (inferred) shortcut ANOM->CHG exists and must NOT shorten distances."""
    g = Graph()
    for nid, t in ((ANOM, NodeType.ANOMALY), (SVC, NodeType.SERVICE), (DB, NodeType.DATABASE),
                   (CHG, NodeType.CHANGE_EVENT), (LONER, NodeType.DATABASE)):
        g.upsert_node(node(nid, t))
    g.add_edge(edge("e1", EdgeType.AFFECTS, ANOM, SVC))
    g.add_edge(edge("e2", EdgeType.DEPENDS_ON, SVC, DB, origin=Origin.DECLARED))
    g.add_edge(edge("e3", EdgeType.CHANGED_BY, SVC, CHG))
    g.add_edge(edge("e4", EdgeType.CAUSED_BY, ANOM, CHG, origin=Origin.INFERRED))
    g.add_fact(fact("f-onset", ANOM, "onset_value", 5200, T0, source=Source.PROMETHEUS))
    return g


def hyp(hid="hyp:h1", band=0.9, supporting=(), refuting=()) -> Hypothesis:
    return Hypothesis(id=hid, statement="s", confidence=Confidence(value=band, basis="band"),
                      supporting_facts=sorted(supporting), refuting_facts=sorted(refuting),
                      created_by=1)


# ── structural distances (the Graph helper) ────────────────────────────────────
def test_structural_bfs_is_undirected_and_skips_the_causal_layer():
    g = graph_fixture()
    d = g.structural_distances(ANOM)
    assert d[ANOM] == 0
    assert d[SVC] == 1                     # AFFECTS (discovered signal-attachment)
    assert d[DB] == 2                      # via declared DEPENDS_ON, against edge direction
    assert d[CHG] == 2                     # via CHANGED_BY — NOT 1 via the inferred CAUSED_BY
    assert LONER not in d                  # no structural path at all


def test_structural_bfs_ignores_retracted_edges_and_unknown_anchor():
    g = graph_fixture()
    g.retract_edge("e2", invalidated_by="test")
    assert DB not in g.structural_distances(ANOM)
    assert g.structural_distances("anomaly:nope") == {}


# ── the three factors ─────────────────────────────────────────────────────────
def test_reliability_channel_measured_vs_inferred():
    f_meas = fact("f1", SVC, "red_errors", 0.4, T0, reliability=0.97)
    f_inf = fact("f2", SVC, "degraded", True, T0, source=Source.LLM, level=0.6)
    assert belief.reliability_of(f_meas) == 0.97
    assert belief.reliability_of(f_inf) == 0.6


def test_proximity_is_flat_inside_the_combined_skew_window():
    """R-J2: two sources are only comparable up to the SUM of their skew bounds — inside
    that window proximity NEVER discriminates (asserts nothing tighter than the skew)."""
    tun = Tunables(clock_skew_bound_s={"prometheus": 30.0, "servicenow": 300.0, "default": 0.0})
    onset_src = Source.PROMETHEUS
    combined = 330.0
    at_edge = belief.temporal_proximity(T0 + timedelta(seconds=combined), Source.SERVICENOW,
                                        T0, onset_src, tun)
    inside = belief.temporal_proximity(T0 - timedelta(seconds=200), Source.SERVICENOW,
                                       T0, onset_src, tun)
    beyond = belief.temporal_proximity(T0 + timedelta(seconds=combined + 1), Source.SERVICENOW,
                                       T0, onset_src, tun)
    assert at_edge == 1.0 and inside == 1.0
    assert beyond < 1.0


def test_proximity_halflife_decay_is_exact_and_tunable():
    tun = Tunables(clock_skew_bound_s={"default": 0.0}, proximity_halflife_s=600.0)
    p = belief.temporal_proximity(T0 + timedelta(seconds=600), Source.PROMETHEUS,
                                  T0, Source.PROMETHEUS, tun)
    assert abs(p - 0.5) < 1e-9             # excess == halflife → exactly one halving
    tun2 = Tunables(clock_skew_bound_s={"default": 0.0}, proximity_halflife_s=1200.0)
    p2 = belief.temporal_proximity(T0 + timedelta(seconds=600), Source.PROMETHEUS,
                                   T0, Source.PROMETHEUS, tun2)
    assert p2 > p                          # a slower decay knob weighs the same fact higher


def test_proximity_neutral_without_an_onset():
    assert belief.temporal_proximity(T0, Source.PROMETHEUS, None, None, Tunables()) == 1.0


def test_specificity_decays_per_hop_and_floors_unreachable():
    tun = Tunables(specificity_decay=0.8, specificity_floor=0.25)
    assert belief.topological_specificity(0, tun) == 1.0
    assert belief.topological_specificity(1, tun) == 0.8
    assert abs(belief.topological_specificity(2, tun) - 0.64) < 1e-9
    assert belief.topological_specificity(None, tun) == 0.25   # unreachable → floor
    assert belief.topological_specificity(50, tun) == 0.25     # deep decay never below floor


def test_evidence_weight_is_the_product_of_the_three_factors():
    tun = Tunables(clock_skew_bound_s={"default": 0.0}, proximity_halflife_s=1800.0,
                   specificity_decay=0.8, specificity_floor=0.25)
    f = fact("f1", DB, "conn_pool_util", 1.0, T0 + timedelta(seconds=1800), reliability=0.97)
    w = belief.evidence_weight(f, onset=T0, onset_source=Source.PROMETHEUS,
                               distances={DB: 2}, tunables=tun)
    assert abs(w - 0.97 * 0.5 * 0.64) < 1e-9
    # no anchor at all (distances=None) → specificity neutral, not floored
    w_free = belief.evidence_weight(f, onset=None, onset_source=None,
                                    distances=None, tunables=tun)
    assert w_free == 0.97


# ── the weighted blend ────────────────────────────────────────────────────────
def test_no_evidence_falls_back_to_exactly_the_band():
    g = graph_fixture()
    assert belief.weighted_score(hyp(band=0.6), g, Tunables()) == 0.6


def test_unresolvable_and_retracted_evidence_weighs_nothing():
    g = graph_fixture()
    g.add_fact(fact("f-dead", SVC, "red_errors", 0.4, T0, state=FactState.RETRACTED))
    h = hyp(band=0.6, supporting=["fact:never-materialised", "f-dead"])
    assert belief.weighted_score(h, g, Tunables()) == 0.6


def test_supporting_pulls_up_refuting_pulls_down():
    g = graph_fixture()
    g.add_fact(fact("f-sup", DB, "conn_pool_util", 1.0, T0, reliability=0.97))
    g.add_fact(fact("f-ref", SVC, "red_latency_p50", 46, T0, reliability=0.95))
    up = belief.weighted_score(hyp(band=0.6, supporting=["f-sup"]), g, Tunables())
    down = belief.weighted_score(hyp(band=0.6, refuting=["f-ref"]), g, Tunables())
    assert up > 0.6 > down
    # the exact blend: (prior*band + w_sup) / (prior + w_sup) with hop-2 specificity 0.64
    w_sup = 0.97 * 1.0 * 0.64
    assert abs(up - round((0.6 + w_sup) / (1.0 + w_sup), 4)) < 1e-9


def test_prior_weight_knob_anchors_the_band():
    g = graph_fixture()
    g.add_fact(fact("f-sup", DB, "conn_pool_util", 1.0, T0, reliability=0.97))
    h = hyp(band=0.3, supporting=["f-sup"])
    light = belief.weighted_score(h, g, Tunables(prior_weight=0.5))
    heavy = belief.weighted_score(h, g, Tunables(prior_weight=4.0))
    assert light > heavy                   # a heavier prior holds the score at the band
    assert abs(belief.weighted_score(hyp(band=0.3), g, Tunables(prior_weight=4.0)) - 0.3) < 1e-9


def test_onset_derived_from_the_anomaly_onset_assertion():
    g = graph_fixture()
    ons = belief.onset_of(g, ANOM)
    assert ons is not None and ons[0] == T0 and ons[1] == Source.PROMETHEUS
    assert belief.onset_of(g, None) is None
    assert belief.find_anomaly(g) == ANOM


def test_closer_evidence_outweighs_farther_equal_evidence():
    """Same reliability, same instant — the subject 1 hop from the anomaly beats the one
    2 hops away, and both beat an unreachable subject (the §2.5 specificity claim)."""
    g = graph_fixture()
    g.add_fact(fact("f-svc", SVC, "red_errors", 0.4, T0, reliability=0.9))
    g.add_fact(fact("f-db", DB, "conn_pool_util", 1.0, T0, reliability=0.9))
    g.add_fact(fact("f-loner", LONER, "conn_pool_util", 1.0, T0, reliability=0.9))
    tun = Tunables()
    s_svc = belief.weighted_score(hyp(supporting=["f-svc"]), g, tun)
    s_db = belief.weighted_score(hyp(supporting=["f-db"]), g, tun)
    s_loner = belief.weighted_score(hyp(supporting=["f-loner"]), g, tun)
    assert s_svc > s_db > s_loner > 0.9    # all supporting evidence still lifts the 0.9 band


def test_score_is_deterministic_across_recomputation():
    g = graph_fixture()
    g.add_fact(fact("f-a", SVC, "red_errors", 0.4, T0, reliability=0.97))
    g.add_fact(fact("f-b", DB, "conn_pool_util", 1.0, T0, reliability=0.95))
    h = hyp(supporting=["f-a", "f-b"])
    assert belief.weighted_score(h, g, Tunables()) == belief.weighted_score(h, g, Tunables())


# ── the store ranks on the EARNED score when bound (P4 step 2) ─────────────────
def _store_with(g: Graph, *hyps: Hypothesis):
    from iw_engine.domain.hypothesis import HypAction, HypDelta
    from iw_engine.hypothesis import HypothesisStore
    store = HypothesisStore()
    store.apply([HypDelta(action=HypAction.CREATE, hypothesis=h) for h in hyps], seq=1)
    return store


def test_bound_store_ranks_by_earned_evidence_not_band():
    """Two same-status hypotheses, same band: the one whose evidence weighs more leads.
    Unbound, the tie falls back to band order (insertion-stable) — the pre-P4 behavior."""
    g = graph_fixture()
    g.add_fact(fact("f-strong", SVC, "red_errors", 0.4, T0, reliability=0.99))
    ha = hyp("hyp:ha", band=0.6)
    hb = hyp("hyp:hb", band=0.6, supporting=["f-strong"])
    store = _store_with(g, ha, hb)
    assert store.leading().id == "hyp:ha"          # unbound: equal bands, insertion order
    store.bind_scoring(g, Tunables())
    assert store.leading().id == "hyp:hb"          # bound: earned evidence outranks
    assert store.score(hb) > store.score(ha) == 0.6


def test_unbound_store_scores_at_exactly_the_band():
    g = graph_fixture()
    h = hyp(band=0.9, supporting=["f-onset"])
    store = _store_with(g, h)
    assert store.score(store.hypotheses["hyp:h1"]) == 0.9


# ── promotion consumes the EARNED score (P4 step 3) ────────────────────────────
def test_promotion_blocked_when_refuting_evidence_drags_a_high_band_below_the_gate():
    """The INV-8 gate is cleared by EARNED belief, not the LLM's self-reported band: a
    0.9-band leader whose evidence is heavily refuting scores below the 0.8 gate and
    stays blocked — the same store, unbound, would promote on the naked band."""
    g = graph_fixture()
    for i in range(4):
        g.add_fact(fact(f"f-ref{i}", SVC, "red_errors", 0.4, T0, reliability=0.99, seq=i + 2))
    h = hyp(band=0.9, refuting=[f"f-ref{i}" for i in range(4)])
    tun = Tunables(confidence_gate=0.8, delta=0.15)
    unbound = _store_with(g, h)
    assert unbound.promotion_ok(tun) is True       # pre-P4: the band bluffs its way over
    bound = _store_with(g, h)
    bound.bind_scoring(g, tun)
    assert bound.score(h) < 0.8
    assert bound.promotion_ok(tun) is False        # P4: earned belief holds the gate


def test_promotion_earned_by_evidence_over_a_mid_band():
    """The converse: a med-band (0.6) leader with strong, close, reliable supporting
    evidence EARNS its way over the 0.8 gate the raw band could never clear."""
    g = graph_fixture()
    for i in range(4):
        g.add_fact(fact(f"f-sup{i}", SVC, "red_errors", 0.4, T0, reliability=0.99, seq=i + 2))
    h = hyp(band=0.6, supporting=[f"f-sup{i}" for i in range(4)])
    tun = Tunables(confidence_gate=0.8, delta=0.15)
    unbound = _store_with(g, h)
    assert unbound.promotion_ok(tun) is False      # the band alone is under the gate
    bound = _store_with(g, h)
    bound.bind_scoring(g, tun)
    assert bound.score(h) >= 0.8
    assert bound.promotion_ok(tun) is True         # evidence earned the promotion


def test_promotion_rival_block_still_holds_under_weighted_scoring():
    """The P0 strengthened check survives P4: ANY alive unrefuted rival blocks, however
    weak its earned score — rivals must be refuted, not out-scored."""
    from iw_engine.domain.enums import HypothesisStatus
    from iw_engine.domain.hypothesis import HypAction, HypDelta
    g = graph_fixture()
    g.add_fact(fact("f-sup", SVC, "red_errors", 0.4, T0, reliability=0.99))
    lead = hyp("hyp:h1", band=0.9, supporting=["f-sup"])
    rival = hyp("hyp:h2", band=0.3)
    store = _store_with(g, lead, rival)
    store.bind_scoring(g, Tunables())
    tun = Tunables(confidence_gate=0.8, delta=0.15)
    assert store.promotion_ok(tun) is False        # unrefuted rival blocks
    store.apply([HypDelta(action=HypAction.REFUTE, hypothesis_id="hyp:h2",
                          new_status=HypothesisStatus.REFUTED)], seq=2)
    assert store.promotion_ok(tun) is True         # only refutation clears the field


# ── correlate_timeline — the executable home (P4 step 4, R-J2) ────────────────
def _event(eid, entity, etype, at, *, source=Source.SERVICENOW, state=None):
    from iw_engine.domain.event import Event
    ev = Event(id=eid, entity_ref=entity, type=etype, occurred_at=at, observed_at=at,
               source=source, created_by=1)
    return ev.model_copy(update={"state": state}) if state is not None else ev


def _skew(a: Source, b: Source, tun: Tunables) -> float:
    return belief.combined_skew_s(a, b, tun)


def test_correlate_timeline_finds_the_change_inside_the_window():
    """A change Event before onset, inside [onset - window - skew, onset + skew], is a
    temporally-correlated candidate; one beyond the lookback is not; ordering is only
    asserted OUTSIDE the combined skew bound (R-J2)."""
    g = graph_fixture()
    tun = Tunables()
    skew = _skew(Source.SERVICENOW, Source.PROMETHEUS, tun)   # 300 + 30
    g.add_event(_event("evt-chg", CHG, "implemented", T0 - timedelta(seconds=480)))
    g.add_event(_event("evt-old", CHG, "implemented",
                       T0 - timedelta(seconds=tun.correlation_window_s + skew + 1)))
    out = belief.correlate_timeline(g, tun, anomaly_ref=ANOM)
    assert [c["event"] for c in out] == ["evt-chg"]
    c = out[0]
    assert c["entity"] == CHG and c["lead_s"] == 480.0 and c["skew_bound_s"] == skew
    assert c["ordering_certain"] is True            # 480s lead > 330s combined bound


def test_correlate_timeline_never_asserts_ordering_inside_the_skew_bound():
    """A change stamped 100s AFTER onset still correlates (the clocks cannot exclude it)
    but with ordering_certain=False — R-J2's 'never tighter than the skew window' made
    executable. One stamped beyond the after-onset skew edge does not correlate at all."""
    g = graph_fixture()
    tun = Tunables()
    skew = _skew(Source.SERVICENOW, Source.PROMETHEUS, tun)
    g.add_event(_event("evt-after", CHG, "implemented", T0 + timedelta(seconds=100)))
    g.add_event(_event("evt-way-after", CHG, "implemented", T0 + timedelta(seconds=skew + 1)))
    g.add_event(_event("evt-near", CHG, "implemented", T0 - timedelta(seconds=skew - 1)))
    out = belief.correlate_timeline(g, tun, anomaly_ref=ANOM)
    assert [c["event"] for c in out] == ["evt-near", "evt-after"]   # (occurred_at, id) order
    assert all(c["ordering_certain"] is False for c in out)


def test_correlate_timeline_only_change_tier_entities_and_needs_an_onset():
    """Only Events on registry L5 (change & supply-chain) entities are candidates — a
    service's degraded_started at the same instant is a symptom, not a change. Without a
    framed onset the correlation returns [] (a hint is never a guess), and retracted
    change events stop correlating."""
    g = graph_fixture()
    tun = Tunables()
    g.add_event(_event("evt-sym", SVC, "degraded_started", T0 - timedelta(seconds=60),
                       source=Source.PROMETHEUS))
    g.add_event(_event("evt-chg", CHG, "implemented", T0 - timedelta(seconds=480)))
    out = belief.correlate_timeline(g, tun, anomaly_ref=ANOM)
    assert [c["event"] for c in out] == ["evt-chg"]
    # retracted → gone
    g.retract_event("evt-chg", invalidated_by="test")
    assert belief.correlate_timeline(g, tun, anomaly_ref=ANOM) == []
    # no onset framed (fresh graph without the anomaly onset fact) → []
    g2 = Graph()
    g2.upsert_node(node(CHG, NodeType.CHANGE_EVENT))
    g2.add_event(_event("evt-chg", CHG, "implemented", T0))
    assert belief.correlate_timeline(g2, tun) == []


def test_correlation_window_is_a_tunable():
    g = graph_fixture()
    g.add_event(_event("evt-chg", CHG, "implemented", T0 - timedelta(seconds=480)))
    wide = belief.correlate_timeline(g, Tunables(), anomaly_ref=ANOM)
    narrow = belief.correlate_timeline(
        g, Tunables(correlation_window_s=0.0,
                    clock_skew_bound_s={"default": 0.0}), anomaly_ref=ANOM)
    assert [c["event"] for c in wide] == ["evt-chg"]
    assert narrow == []                            # window shrunk → the candidate drops
