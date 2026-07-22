"""Controller gate tests — the INV-8 promotion gate (check_gate x ledger.promotion_ok).

The engine, not the LLM, owns promotion: an ADVANCE through a promotion-gated
phase is honoured only when the leading hypothesis crosses the gate, beats the field by
delta, and has NO alive unrefuted rival. An LLM-set CONFIRMED status grants no bypass
(2026-07-22 review, finding 2).
"""
from __future__ import annotations

from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import GateResult, HypothesisStatus, VerdictStatus
from iw_engine.domain.hypothesis import HypAction, HypDelta, Hypothesis
from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
from iw_engine.domain.playbook import GateSpec, PhaseSpec, Tunables
from iw_engine.hypothesis import HypothesisStore
from iw_engine.runtime.controller import check_gate

TUN = Tunables(confidence_gate=0.8, delta=0.15)


def _spec(require_refutation: bool = False) -> PhaseSpec:
    return PhaseSpec(id="investigate", goal="", allowed_intents=[],
                     gate=GateSpec(promotion=True,
                                   refutation_attempted=require_refutation))


def _advance() -> PhaseResult:
    return PhaseResult(phase_id="investigate", goal_restated="", narrative="n",
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


# ── P7 step 4: the new declarable predicates + gated DONE ─────────────────────
def _verdict(status: VerdictStatus) -> PhaseResult:
    return PhaseResult(phase_id="verify", goal_restated="", narrative="n",
                       verdict=PhaseVerdict(status=status,
                                            confidence=Confidence(value=0.9, basis="x")))


def _graph_with_cleared(cleared: bool):
    from datetime import UTC, datetime

    from iw_engine.domain.enums import NodeType, Source
    from iw_engine.domain.event import Event
    from iw_engine.domain.node import Node
    from iw_engine.graph.graph import Graph

    g = Graph()
    g.upsert_node(Node(id="anomaly:anom-1", type=NodeType.ANOMALY, created_by=1))
    if cleared:
        t = datetime(2026, 7, 19, 15, tzinfo=UTC)
        g.add_event(Event(id="ev:clear", entity_ref="anomaly:anom-1", type="cleared",
                          occurred_at=t, observed_at=t, source=Source.PROMETHEUS,
                          created_by=2))
    return g


def test_symptom_cleared_predicate_gates_advance_and_done():
    spec = PhaseSpec(id="verify", goal="", allowed_intents=[],
                     gate=GateSpec(symptom_cleared=True))
    store = HypothesisStore()

    for status in (VerdictStatus.ADVANCE, VerdictStatus.DONE):   # DONE is gated too
        gated = check_gate(spec, _verdict(status), store, TUN,
                           graph=_graph_with_cleared(False), anomaly_ref="anomaly:anom-1")
        assert gated.status == VerdictStatus.REPEAT, status
        assert "symptom not cleared" in gated.gate_reason

    # an un-framed symptom can never claim recovery
    gated = check_gate(spec, _verdict(VerdictStatus.ADVANCE), store, TUN,
                       graph=_graph_with_cleared(True), anomaly_ref=None)
    assert gated.status == VerdictStatus.REPEAT

    # the cleared event on the symptom node satisfies it
    gated = check_gate(spec, _verdict(VerdictStatus.ADVANCE), store, TUN,
                       graph=_graph_with_cleared(True), anomaly_ref="anomaly:anom-1")
    assert gated.status == VerdictStatus.ADVANCE
    assert gated.gate_result == GateResult.PASS


def test_human_approved_predicate_reads_the_journal():
    from datetime import UTC, datetime

    from iw_engine.journal.journal import Journal

    spec = PhaseSpec(id="act", goal="", allowed_intents=[],
                     gate=GateSpec(human_approved=True))
    store = HypothesisStore()
    result = PhaseResult(phase_id="act", goal_restated="", narrative="n",
                         verdict=PhaseVerdict(status=VerdictStatus.ADVANCE,
                                              confidence=Confidence(value=0.9, basis="x")))

    jr = Journal(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))
    gated = check_gate(spec, result, store, TUN, journal=jr)
    assert gated.status == VerdictStatus.REPEAT           # no human on the record
    assert "no human approval" in gated.gate_reason

    jr.append_gate_decision("act", intent="apply_remediation", reasoning="ok",
                            action={}, observation={}, decision="deny", actor="op")
    assert check_gate(spec, result, store, TUN, journal=jr).status == VerdictStatus.REPEAT

    jr.append_gate_decision("act", intent="apply_remediation", reasoning="ok",
                            action={}, observation={}, decision="approve", actor="op")
    assert check_gate(spec, result, store, TUN, journal=jr).status == VerdictStatus.ADVANCE


def test_done_is_gated_not_a_free_bypass():
    """Pre-P7, DONE bypassed every gate from any phase. Now a DONE that fails the phase's
    predicates downgrades to REPEAT exactly like an ADVANCE."""
    led = _ledger(("hyp:h1", 0.9, HypothesisStatus.CONFIRMED),
                  ("hyp:h2", 0.9, HypothesisStatus.SUPPORTED))   # live rival — promotion fails
    spec = _spec()
    result = PhaseResult(phase_id="investigate", goal_restated="", narrative="n",
                         verdict=PhaseVerdict(status=VerdictStatus.DONE,
                                              confidence=Confidence(value=0.9, basis="x")))
    gated = check_gate(spec, result, led, TUN)
    assert gated.status == VerdictStatus.REPEAT
    assert gated.gate_result == GateResult.FAIL
