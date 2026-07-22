"""Controller gate tests — the INV-8 promotion gate (check_gate x ledger.promotion_ok).

The engine, not the LLM, owns promotion: an ADVANCE through a require_confidence_gate
phase is honoured only when the leading hypothesis crosses the gate, beats the field by
delta, and has NO alive unrefuted rival. An LLM-set CONFIRMED status grants no bypass
(2026-07-22 review, finding 2).
"""
from __future__ import annotations

from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import GateResult, HypothesisStatus, Phase, VerdictStatus
from iw_engine.domain.hypothesis import HypAction, HypDelta, Hypothesis
from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
from iw_engine.domain.playbook import GateSpec, PhaseSpec, Tunables
from iw_engine.hypothesis import HypothesisStore
from iw_engine.runtime.controller import check_gate

TUN = Tunables(confidence_gate=0.8, delta=0.15)


def _spec(require_refutation: bool = False) -> PhaseSpec:
    return PhaseSpec(id=Phase.INVESTIGATE, goal="", allowed_intents=[],
                     gate=GateSpec(require_confidence_gate=True,
                                   require_refutation=require_refutation))


def _advance() -> PhaseResult:
    return PhaseResult(phase_id=Phase.INVESTIGATE, goal_restated="", narrative="n",
                       verdict=PhaseVerdict(status=VerdictStatus.ADVANCE,
                                            confidence=Confidence(value=0.9, basis="x")))


def _ledger(*hyps: tuple[str, float, HypothesisStatus]) -> HypothesisStore:
    led = HypothesisStore()
    for i, (hid, conf, status) in enumerate(hyps, start=1):
        h = Hypothesis(id=hid, statement=f"cause {hid}",
                       confidence=Confidence(value=conf, basis="b"),
                       created_by=i, status=status)
        led.apply([HypDelta(action=HypAction.CREATE, hypothesis=h)], seq=i)
    return led


def test_gate_blocks_confirmed_lead_with_live_rival():
    """The exact review scenario: h1 CONFIRMED@0.9 with an unrefuted h2@0.9 rival.
    Under the old gate the CONFIRMED status short-circuited everything; now the
    live rival (and the zero margin) must block the ADVANCE."""
    led = _ledger(("hyp:h1", 0.9, HypothesisStatus.CONFIRMED),
                  ("hyp:h2", 0.9, HypothesisStatus.SUPPORTED))
    gated = check_gate(_spec(), _advance(), led, TUN)
    assert gated.status == VerdictStatus.REPEAT
    assert gated.gate_result == GateResult.FAIL


def test_gate_blocks_below_gate_lead_despite_confirmed_status():
    """A sole CONFIRMED hypothesis at MED (0.6 < gate 0.8) may not clear the gate —
    the LLM-set status is not a bypass."""
    led = _ledger(("hyp:h1", 0.6, HypothesisStatus.CONFIRMED))
    gated = check_gate(_spec(), _advance(), led, TUN)
    assert gated.status == VerdictStatus.REPEAT
    assert gated.gate_result == GateResult.FAIL


def test_gate_blocks_unrefuted_sub_gate_rival():
    """An unrefuted 0.75 rival under a 0.8 gate must block (review finding 2)."""
    led = _ledger(("hyp:h1", 0.9, HypothesisStatus.SUPPORTED),
                  ("hyp:h2", 0.75, HypothesisStatus.PROPOSED))
    gated = check_gate(_spec(), _advance(), led, TUN)
    assert gated.status == VerdictStatus.REPEAT
    assert gated.gate_result == GateResult.FAIL


def test_gate_passes_sole_confident_lead_with_refuted_rival():
    """The legitimate promotion: lead@0.9 over the gate, only rival REFUTED."""
    led = _ledger(("hyp:h1", 0.9, HypothesisStatus.SUPPORTED),
                  ("hyp:h2", 0.75, HypothesisStatus.REFUTED))
    gated = check_gate(_spec(require_refutation=True), _advance(), led, TUN)
    assert gated.status == VerdictStatus.ADVANCE
    assert gated.gate_result == GateResult.PASS
    assert gated.gate_reason is None
