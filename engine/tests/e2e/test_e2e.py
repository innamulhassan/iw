"""End-to-end: drive the REAL engine (thin loop + reducer + fold + gate + controller) with
a ScriptedPlanner through a full incident. Asserts the OUTCOME, the reasoning (a rival is
refuted; the write-gate holds), and the journal-replay-equivalence invariant.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import iw_engine
from iw_engine.domain.enums import CloseOutcome, EdgeType, HypothesisStatus, Phase
from iw_engine.graph import rebuild
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

from . import scenario_code_regression as s1

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def _run(build_fn, **kw):
    subject, script = build_fn(**kw)
    pb = load_playbook(PLAYBOOK)
    clock = lambda: datetime(2026, 7, 19, tzinfo=UTC)  # noqa: E731 deterministic
    return Engine(pb, ScriptedPlanner(script), clock=clock).run(subject)


def test_code_regression_happy_path():
    res = _run(s1.build)

    assert res.phases_run == [Phase.FRAME, Phase.TRIAGE, Phase.HYPOTHESIZE, Phase.INVESTIGATE,
                              Phase.REMEDIATE, Phase.VERIFY, Phase.CLOSE]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"
    # differential diagnosis: the DB hypothesis was ruled out, not ignored
    assert res.ledger.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert s1.fid(s1.DB, "conn_pool_util", s1.T_INV) in res.ledger.hypotheses["hyp:h2"].refuting_facts

    # the graph carries the full typed causal picture
    for node_id in [s1.SVC, s1.ANOM, s1.CHG, s1.COMMIT, s1.ERRSIG, s1.DB, s1.H1]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"
    caused = res.graph.out_edges(s1.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == s1.COMMIT

    # the symptom fact was superseded on recovery (bi-temporal), not overwritten. P2: the
    # canonical predicate is error_rate (red_errors is the vendor spelling); supersession is
    # unchanged (the native-keyed fact ids are stable, the scan matches on the canonical name).
    err_facts = [f for f in res.graph.facts.values()
                 if f.subject_ref == s1.SVC and f.predicate == "error_rate"]
    assert len(err_facts) == 2  # 0.40 (superseded) + 0.01 (active)
    active = [f for f in err_facts if f.is_open]
    assert len(active) == 1 and active[0].value == 0.01

    # the journal alone rebuilds the graph exactly (source-of-truth guarantee)
    g2, _ = rebuild(res.journal)
    assert g2.to_dict() == res.graph.to_dict()


def test_refuted_variant_backtracks():
    res = _run(s1.build, refuted_variant=True)
    # the engine returned from INVESTIGATE to HYPOTHESIZE when the leading hypothesis was refuted
    assert res.phases_run.count(Phase.HYPOTHESIZE) >= 2
    assert Phase.INVESTIGATE in res.phases_run
    assert res.ledger.hypotheses["hyp:h1"].status == HypothesisStatus.REFUTED


def test_write_gate_holds_below_confidence():
    """If INVESTIGATE tries to ADVANCE without meeting the confidence gate, the controller
    downgrades it to REPEAT — the engine never advances to remediation on thin evidence."""
    from iw_engine.domain.common import Confidence
    from iw_engine.domain.enums import GateResult, VerdictStatus
    from iw_engine.domain.enums import Phase as P
    from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
    from iw_engine.domain.playbook import GateSpec, PhaseSpec, Tunables
    from iw_engine.ledger import Ledger
    from iw_engine.runtime.controller import check_gate

    spec = PhaseSpec(id=P.INVESTIGATE, goal="", allowed_intents=[],
                     gate=GateSpec(require_confidence_gate=True, require_refutation=True))
    result = PhaseResult(phase_id=P.INVESTIGATE, goal_restated="",
                         narrative="thin", verdict=PhaseVerdict(
                             status=VerdictStatus.ADVANCE,
                             confidence=Confidence(value=0.9, basis="x")))
    gated = check_gate(spec, result, Ledger(), Tunables())  # empty ledger -> no confident leader
    assert gated.status == VerdictStatus.REPEAT
    assert gated.gate_result == GateResult.FAIL
