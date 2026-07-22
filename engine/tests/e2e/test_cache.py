"""End-to-end: drive the REAL engine through the CACHE-STAMPEDE root-cause scenario. Asserts
the OUTCOME, the differential diagnosis (a code-regression rival is ruled out by flat p50,
not ignored), the cache-saturation discriminator (collapsed hit-rate + pinned memory), and
the journal-replay-equivalence invariant.
"""
from __future__ import annotations

from iw_engine.domain.enums import CloseOutcome, EdgeType, HypothesisStatus, Origin

from . import scenario_cache as sc
from ._helpers import assert_replay_equivalent, run


def test_cache_happy_path():
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)

    assert res.phases_run == ["frame", "investigate", "investigate", "act",
                              "verify", "close"]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # differential diagnosis: the code-regression hypothesis was ruled out, not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert sc.fid(sc.SVC, "red_latency_p50", sc.T_INV) in res.hypothesis_store.hypotheses["hyp:h2"].refuting_facts

    # the graph carries the full typed causal picture
    for node_id in [sc.SVC, sc.ANOM, sc.CACHE, sc.CHG, sc.COMMIT, sc.H1]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"
    caused = res.graph.out_edges(sc.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == sc.COMMIT

    # the symptom (degraded) fact was superseded on recovery (bi-temporal), not overwritten
    deg_facts = [f for f in res.graph.facts.values()
                 if f.subject_ref == sc.SVC and f.predicate == "degraded"]
    assert len(deg_facts) == 2  # True (superseded) + False (active)
    active = [f for f in deg_facts if f.is_open]
    assert len(active) == 1 and active[0].value is False

    # the journal alone rebuilds the graph exactly (source-of-truth guarantee)
    assert_replay_equivalent(res)   # graph AND hypothesis store (journal v2)


def test_cache_rules_out_code_hypothesis_via_flat_p50():
    """The discriminator: hit-rate collapsed + memory pinned (a cache-tier stampede), while
    the service's own p50 stays flat — that pattern rules out the code rival and confirms
    the cache-client config change (singleflight disabled) as root cause."""
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)
    assert res.rejections == []

    # appd's exit-call discovery: product-api READS_FROM product-redis via a discovered hop
    reads = res.graph.out_edges(sc.SVC, EdgeType.READS_FROM)
    origins = {e.origin for e in reads if e.dst == sc.CACHE}
    assert Origin.DECLARED in origins and Origin.DISCOVERED in origins

    # H1 is fully evidenced: the collapsed hit-rate, the eviction surge, and the pinned
    # memory all SUPPORT it
    supports_h1 = {e.src for e in res.graph.in_edges(sc.H1, EdgeType.SUPPORTS)}
    assert {sc.CACHE} <= supports_h1

    # H2 (code regression) is REFUTED by the flat p50 — the service's own compute is fine
    refutes_h2 = res.graph.in_edges(sc.H2, EdgeType.REFUTES)
    assert any(e.src == sc.SVC for e in refutes_h2)
    h2 = res.hypothesis_store.hypotheses["hyp:h2"]
    assert h2.status == HypothesisStatus.REFUTED

    # the cache really did collapse (the discriminator's numeric anchor)
    hit = [f for f in res.graph.facts.values()
           if f.subject_ref == sc.CACHE and f.predicate == "hit_rate"]
    assert hit and hit[0].value <= 0.5

    # H1 confirmed at high confidence — the root cause IS this hypothesis (R-G2)
    h1 = res.hypothesis_store.hypotheses["hyp:h1"]
    assert h1.status == HypothesisStatus.CONFIRMED
    assert h1.confidence.value >= 0.8
