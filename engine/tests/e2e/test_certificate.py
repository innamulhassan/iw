"""End-to-end: drive the REAL engine through the CERTIFICATE-EXPIRY root-cause scenario.
Asserts the OUTCOME, the differential diagnosis (a service-outage rival is ruled out —
pods are Ready, p50 flat — not ignored), the partial/client-dependent failure
discriminator, and the journal-replay-equivalence invariant.
"""
from __future__ import annotations

from iw_engine.domain.enums import CloseOutcome, EdgeType, HypothesisStatus, Phase

from . import scenario_certificate as sc
from ._helpers import assert_replay_equivalent, run


def test_certificate_happy_path():
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)

    assert res.phases_run == [Phase.FRAME, Phase.TRIAGE, Phase.HYPOTHESIZE, Phase.INVESTIGATE,
                              Phase.REMEDIATE, Phase.VERIFY, Phase.CLOSE]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # differential diagnosis: the service-outage hypothesis was ruled out, not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert sc.fid(sc.SVC, "red_latency_p50", sc.T_INV) in res.hypothesis_store.hypotheses["hyp:h2"].refuting_facts

    # the graph carries the full typed causal picture
    for node_id in [sc.SVC, sc.ANOM, sc.CERT, sc.ERRSIG, sc.H1]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"
    caused = res.graph.out_edges(sc.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == sc.CERT

    # the symptom (degraded) fact was superseded on recovery (bi-temporal), not overwritten
    deg_facts = [f for f in res.graph.facts.values()
                 if f.subject_ref == sc.SVC and f.predicate == "degraded"]
    assert len(deg_facts) == 2  # True (superseded) + False (active)
    active = [f for f in deg_facts if f.is_open]
    assert len(active) == 1 and active[0].value is False

    # the journal alone rebuilds the graph exactly (source-of-truth guarantee)
    assert_replay_equivalent(res)   # graph AND hypothesis store (journal v2)


def test_certificate_rules_out_service_outage_via_partial_failure():
    """The discriminator: the failure is PARTIAL (~40% of clients, those validating the
    intermediate), the service is healthy (pods Ready, p50 flat), and the error signature
    is TLS-side (SSLHandshakeException / PKIX) — that pattern rules out the outage rival
    and confirms the expired intermediate cert as root cause."""
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)
    assert res.rejections == []

    # H1 is evidenced by both the handshake-error signature AND the cert's expiry
    supports_h1 = {e.src for e in res.graph.in_edges(sc.H1, EdgeType.SUPPORTS)}
    assert sc.ERRSIG in supports_h1 and sc.CERT in supports_h1

    # H2 (service outage) is REFUTED by the flat p50 — the service is healthy
    refutes_h2 = res.graph.in_edges(sc.H2, EdgeType.REFUTES)
    assert any(e.src == sc.SVC for e in refutes_h2)
    h2 = res.hypothesis_store.hypotheses["hyp:h2"]
    assert h2.status == HypothesisStatus.REFUTED

    # the cert really is expired (days_to_expiry <= 0 — the discriminator's numeric anchor)
    exp = [f for f in res.graph.facts.values()
           if f.subject_ref == sc.CERT and f.predicate == "days_to_expiry"]
    assert exp and any(f.value <= 0 for f in exp)

    # the handshake error really surged (the partial-failure numeric anchor)
    cnt = [f for f in res.graph.facts.values()
           if f.subject_ref == sc.ERRSIG and f.predicate == "count"]
    assert cnt and cnt[0].value > 1000

    # H1 confirmed at high confidence — the root cause IS this hypothesis (R-G2)
    h1 = res.hypothesis_store.hypotheses["hyp:h1"]
    assert h1.status == HypothesisStatus.CONFIRMED
    assert h1.confidence.value >= 0.8
