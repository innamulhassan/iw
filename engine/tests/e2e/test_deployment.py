"""End-to-end: drive the REAL engine through a bad-DEPLOYMENT incident, with the ocp/git/
servicenow/prometheus capability adapters exercised via mocked fixtures (not direct ops).
Asserts the OUTCOME, the differential diagnosis (checkout-db ruled out), the discriminator
(the pod never reaches Ready), and the causal chain the git blame adapter pins itself.
"""
from __future__ import annotations

from iw_engine.domain.enums import CloseOutcome, EdgeType, HypothesisStatus

from . import scenario_deployment as s2
from ._helpers import run


def test_deployment_happy_path_resolved():
    subject, script, fixtures = s2.build()
    res = run(subject, script, fixtures)

    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.phases_run == ["frame", "investigate", "investigate", "act",
                              "verify", "close"]
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # differential diagnosis: checkout-db was ruled out, not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert s2.fid(s2.DB, "conn_pool_util", s2.T_INV) in res.hypothesis_store.hypotheses["hyp:h2"].refuting_facts

    # the discriminator: the pod's phase fact stays CrashLoopBackOff through the whole
    # investigation and only flips to Running post-rollback (bi-temporal supersession)
    pod_phase_facts = sorted(
        (f for f in res.graph.facts.values() if f.subject_ref == s2.POD and f.predicate == "phase"),
        key=lambda f: f.valid_from,
    )
    assert [f.value for f in pod_phase_facts] == ["CrashLoopBackOff", "Running"]
    assert not pod_phase_facts[0].is_open and pod_phase_facts[1].is_open

    # the graph carries the full typed causal chain: blame's own CAUSED_BY edge pins h1
    for node_id in [s2.SVC, s2.ANOM, s2.CHG, s2.DEP, s2.POD, s2.COMMIT, s2.PR, s2.ERRSIG,
                    s2.DB, s2.H1]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"
    caused = res.graph.out_edges(s2.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == s2.COMMIT

    # the git adapter's blame join also pins the ErrorSignature to the same commit
    errsig_caused = res.graph.out_edges(s2.ERRSIG, EdgeType.CAUSED_BY)
    assert errsig_caused and errsig_caused[0].dst == s2.COMMIT


def test_deployment_mitigated_variant_without_confirmation():
    """Same investigation and successful rollback, but VERIFY never promotes h1 to
    CONFIRMED — the engine must close MITIGATED (impact stopped, root cause unconfirmed),
    not RESOLVED, exercising the other half of CloseOutcome."""
    subject, script, fixtures = s2.build(mitigated=True)
    res = run(subject, script, fixtures)

    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.phases_run == ["frame", "investigate", "investigate", "act",
                              "verify", "close"]
    assert res.close_outcome == CloseOutcome.MITIGATED
    assert res.confirmed is None

    # the leading hypothesis is still well-evidenced (supported, high confidence) — just
    # never independently confirmed — and the rival is still ruled out either way
    h1 = res.hypothesis_store.hypotheses["hyp:h1"]
    assert h1.status == HypothesisStatus.SUPPORTED
    assert h1.confidence.value >= 0.8
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED

    # impact still cleared even without confirmation (rollback worked)
    degraded_facts = [f for f in res.graph.facts.values()
                      if f.subject_ref == s2.SVC and f.predicate == "degraded"]
    assert any(f.value is False and f.is_open for f in degraded_facts)
