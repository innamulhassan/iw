"""End-to-end: drive the REAL engine through the FEATURE-FLAG root-cause scenario. Asserts
the OUTCOME, the differential diagnosis (a deploy-regression rival is ruled out — the last
build is 3 days old with no errors — not ignored), the flag-onset discriminator (error
signature first_seen at flip time), and the journal-replay-equivalence invariant.
"""
from __future__ import annotations

from iw_engine.domain.enums import CloseOutcome, EdgeType, HypothesisStatus

from . import scenario_featureflag as sf
from ._helpers import assert_replay_equivalent, run


def test_featureflag_happy_path():
    subject, script, fixtures = sf.build()
    res = run(subject, script, fixtures)

    assert res.phases_run == ["frame", "triage", "hypothesize", "investigate",
                              "remediate", "verify", "close"]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # differential diagnosis: the deploy-regression hypothesis was ruled out, not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert sf.fid(sf.SVC, "red_latency_p50", sf.T_INV) in res.hypothesis_store.hypotheses["hyp:h2"].refuting_facts

    # the graph carries the full typed causal picture
    for node_id in [sf.SVC, sf.ANOM, sf.FLAG, sf.CHG, sf.ERRSIG, sf.H1]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"
    caused = res.graph.out_edges(sf.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == sf.FLAG

    # the symptom (degraded) fact was superseded on recovery (bi-temporal), not overwritten
    deg_facts = [f for f in res.graph.facts.values()
                 if f.subject_ref == sf.SVC and f.predicate == "degraded"]
    assert len(deg_facts) == 2  # True (superseded) + False (active)
    active = [f for f in deg_facts if f.is_open]
    assert len(active) == 1 and active[0].value is False

    # the journal alone rebuilds the graph exactly (source-of-truth guarantee)
    assert_replay_equivalent(res)   # graph AND hypothesis store (journal v2)


def test_featureflag_rules_out_deploy_hypothesis():
    """The discriminator: onset is 30s after the FLAG FLIP, no build/deploy event in the
    window (the last deploy is 3 days old), and the error signature is NEW (first_seen at
    flip time) — that pattern rules out the deploy rival and confirms the flag as root cause."""
    subject, script, fixtures = sf.build()
    res = run(subject, script, fixtures)
    assert res.rejections == []

    # H1 is evidenced by the error signature (TaxEngineException, first_seen at the flip)
    supports_h1 = {e.src for e in res.graph.in_edges(sf.H1, EdgeType.SUPPORTS)}
    assert sf.ERRSIG in supports_h1

    # H2 (deploy regression) is REFUTED by the flat p50 — the handler compute is unchanged
    refutes_h2 = res.graph.in_edges(sf.H2, EdgeType.REFUTES)
    assert any(e.src == sf.SVC for e in refutes_h2)
    h2 = res.hypothesis_store.hypotheses["hyp:h2"]
    assert h2.status == HypothesisStatus.REFUTED

    # the error signature is real and surged (the discriminator's numeric anchor)
    cnt = [f for f in res.graph.facts.values()
           if f.subject_ref == sf.ERRSIG and f.predicate == "count"]
    assert cnt and cnt[0].value > 100

    # the flag was rolled to 100% at onset (the change that caused it)
    rollout = [f for f in res.graph.facts.values()
               if f.subject_ref == sf.FLAG and f.predicate == "rollout_percentage"]
    assert rollout and any(f.value == 100 for f in rollout)

    # H1 confirmed at high confidence — the root cause IS this hypothesis (R-G2)
    h1 = res.hypothesis_store.hypotheses["hyp:h1"]
    assert h1.status == HypothesisStatus.CONFIRMED
    assert h1.confidence.value >= 0.8
