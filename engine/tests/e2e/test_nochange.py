"""End-to-end: the NO-CHANGE incident class through the REAL engine (mocked capability
outputs). Asserts the MITIGATED close path (a supported-but-never-confirmed leading
hypothesis) and the empty-change-list HYPOTHESIZE fallback (root seeded from the
saturation signal, never a ChangeEvent).
"""
from __future__ import annotations

from iw_engine.domain.enums import CloseOutcome, EdgeType, HypothesisStatus, NodeType, Phase

from . import scenario_nochange as s2
from ._helpers import run


def _run():
    subject, script, fixtures = s2.build()
    return run(subject, script, fixtures)


def test_nochange_mitigated_close():
    res = _run()

    assert res.phases_run == [Phase.FRAME, Phase.TRIAGE, Phase.HYPOTHESIZE, Phase.INVESTIGATE,
                              Phase.REMEDIATE, Phase.VERIFY, Phase.CLOSE]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"

    # closed MITIGATED: the symptom cleared but no hypothesis was ever confirmed
    assert res.close_outcome == CloseOutcome.MITIGATED
    assert res.confirmed is None

    # the leading hypothesis is evidence-backed and high-confidence, but stops at
    # 'supported' — an organic no-change incident has no revert experiment to run
    h1 = res.ledger.hypotheses["hyp:h1"]
    assert h1.status == HypothesisStatus.SUPPORTED
    assert h1.confidence.value >= 0.8
    assert s2.fid(s2.DB, "conn_pool_util", s2.T_INV) in h1.supporting_facts

    # the rival "an invisible change did it" hypothesis was ruled out, not ignored
    h2 = res.ledger.hypotheses["hyp:h2"]
    assert h2.status == HypothesisStatus.REFUTED
    no_change_fact = s2.fid(s2.SVC, "no_evidence:find_recent_changes", s2.T_INV)
    assert no_change_fact in h2.refuting_facts

    # the causal edge points at the database, not at a change/commit
    caused = res.graph.out_edges(s2.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == s2.DB

    # the symptom fact was superseded on recovery (bi-temporal), not overwritten
    pool_facts = [f for f in res.graph.facts.values()
                  if f.subject_ref == s2.DB and f.predicate == "conn_pool_util"]
    assert len(pool_facts) == 3  # 0.86 (triage) + 0.97 (investigate, superseded) + 0.52 (active)
    active = [f for f in pool_facts if f.is_open]
    assert len(active) == 1 and active[0].value == 0.52


def test_nochange_empty_change_list_fallback():
    res = _run()
    assert res.rejections == []

    # find_recent_changes returned an empty list -> the adapter folded zero ChangeEvent
    # ops; the graph must carry none, proving HYPOTHESIZE fell back to the saturation
    # signal rather than a phantom change
    assert res.graph.nodes_of_type(NodeType.CHANGE_EVENT) == []

    # the leading hypothesis's root candidate is the database, never a change record
    h1 = res.ledger.hypotheses["hyp:h1"]
    assert h1.root_candidate == s2.DB

    # the rival change-hypothesis had no change to point at and was refuted by the
    # honest null result (R-P2), not by asserting a fact about a change that never
    # existed
    h2 = res.ledger.hypotheses["hyp:h2"]
    assert h2.root_candidate is None
    assert h2.status == HypothesisStatus.REFUTED
