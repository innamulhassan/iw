"""End-to-end: drive the REAL engine through the DATABASE root-cause scenario. Asserts
the OUTCOME, the differential diagnosis (a code-regression rival is ruled out, not
ignored), the JDBC-boundary discriminator, and the journal-replay-equivalence invariant.
"""
from __future__ import annotations

from iw_engine.domain.enums import CloseOutcome, EdgeType, HypothesisStatus, Origin, Phase
from iw_engine.graph import rebuild

from . import scenario_database as s2
from ._helpers import run


def test_database_happy_path():
    subject, script, fixtures = s2.build()
    res = run(subject, script, fixtures)

    assert res.phases_run == [Phase.FRAME, Phase.TRIAGE, Phase.HYPOTHESIZE, Phase.INVESTIGATE,
                              Phase.REMEDIATE, Phase.VERIFY, Phase.CLOSE]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # differential diagnosis: the code-regression hypothesis was ruled out, not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert s2.fid(s2.SVC, "red_latency_p50", s2.T_INV) in res.hypothesis_store.hypotheses["hyp:h2"].refuting_facts

    # the graph carries the full typed causal picture
    for node_id in [s2.SVC, s2.ANOM, s2.DB, s2.CHG, s2.COMMIT, s2.SCHEMA, s2.H1]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"
    caused = res.graph.out_edges(s2.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == s2.CHG

    # the symptom (degraded) fact was superseded on recovery (bi-temporal), not overwritten
    deg_facts = [f for f in res.graph.facts.values()
                 if f.subject_ref == s2.SVC and f.predicate == "degraded"]
    assert len(deg_facts) == 2  # True (superseded) + False (active)
    active = [f for f in deg_facts if f.is_open]
    assert len(active) == 1 and active[0].value is False

    # the journal alone rebuilds the graph exactly (source-of-truth guarantee)
    g2, _ = rebuild(res.journal)
    assert g2.to_dict() == res.graph.to_dict()


def test_database_rules_out_code_hypothesis_via_jdbc_boundary():
    """The discriminator: the exit call is JDBC (DB-bound) and the pool is maxed, while
    the service's own p50 stays flat — that pattern is what rules out the code rival and
    confirms the migration/index as root cause."""
    subject, script, fixtures = s2.build()
    res = run(subject, script, fixtures)
    assert res.rejections == []

    # appd's exit-call discovery: orders-api DEPENDS_ON orders-pg via a discovered JDBC hop,
    # alongside the CMDB-declared spine from TRIAGE — both origins coexist
    depends = res.graph.out_edges(s2.SVC, EdgeType.DEPENDS_ON)
    origins = {e.origin for e in depends if e.dst == s2.DB}
    assert Origin.DECLARED in origins and Origin.DISCOVERED in origins

    # H1 is fully evidenced: the JDBC-driven p99, the maxed pool, and the migration diff
    # all SUPPORT it
    supports_h1 = {e.src for e in res.graph.in_edges(s2.H1, EdgeType.SUPPORTS)}
    assert {s2.SVC, s2.DB, s2.COMMIT} <= supports_h1

    # H2 (code regression) is REFUTED by the flat p50 — the service's own compute is fine
    refutes_h2 = res.graph.in_edges(s2.H2, EdgeType.REFUTES)
    assert any(e.src == s2.SVC for e in refutes_h2)
    h2 = res.hypothesis_store.hypotheses["hyp:h2"]
    assert h2.status == HypothesisStatus.REFUTED

    # the pool really is maxed (the discriminator's numeric anchor)
    conn = [f for f in res.graph.facts.values()
            if f.subject_ref == s2.DB and f.predicate == "active_connections"]
    assert conn and conn[0].value == 200

    # H1 confirmed at high confidence — the root cause IS this hypothesis (R-G2)
    h1 = res.hypothesis_store.hypotheses["hyp:h1"]
    assert h1.status == HypothesisStatus.CONFIRMED
    assert h1.confidence.value >= 0.8
