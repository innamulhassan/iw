"""Controller — the routing authority (DESIGN §2.3 R-C1). It applies the phase's gate to
the planner's PROPOSED verdict (an 'advance' is only honoured when the declarative gate
passes) and picks the next phase from the verdict + the playbook's transitions. The
LLM's `next_actions` is advisory (it seeds the next plan), never the phase router.
"""
from __future__ import annotations

from ..domain.enums import GateResult, HypothesisStatus, VerdictStatus
from ..domain.phase_result import PhaseResult, PhaseVerdict
from ..domain.playbook import PhaseSpec, Tunables
from ..hypothesis.store import HypothesisStore


def check_gate(spec: PhaseSpec, result: PhaseResult, store: HypothesisStore,
               tunables: Tunables) -> PhaseVerdict:
    """Guard the planner's verdict. Only an ADVANCE is gated; a failed gate downgrades to
    REPEAT so the phase runs again rather than advancing on thin evidence."""
    v = result.verdict
    if v.status != VerdictStatus.ADVANCE:
        return v

    fail: str | None = None
    for field_name in spec.produces_required:
        # NoEvidence facts count — an honest null result satisfies the floor (R-P2)
        if not getattr(result, field_name, None):
            fail = f"produces_required '{field_name}' empty"
            break
    if fail is None and len(result.facts_added) < spec.gate.min_facts:
        fail = f"min_facts {spec.gate.min_facts} not met"
    if fail is None and spec.gate.require_confidence_gate:
        # INV-8: promotion is the ENGINE's decision, never the LLM's. promotion_ok requires
        # the leader to cross the confidence gate, beat the field by delta, and have NO
        # alive unrefuted rival — an LLM-set CONFIRMED status grants no bypass (the old
        # status short-circuit let a rival-contested or below-gate hypothesis clear the
        # gate; 2026-07-22 review, finding 2).
        if not store.promotion_ok(tunables):
            fail = ("confidence gate not met (lead below gate, margin < delta, "
                    "or an unrefuted rival is still alive)")
    if fail is None and spec.gate.require_refutation:
        # genuine differential investigation: a rival was ruled out, or the leader was challenged
        refuted_rival = any(h.status == HypothesisStatus.REFUTED for h in store.hypotheses.values())
        lead = store.leading()
        lead_challenged = lead is not None and bool(lead.refuting_facts)
        if not (refuted_rival or lead_challenged):
            fail = "no refutation attempted (no rival ruled out, leader unchallenged)"

    if fail is not None:
        # carry the exact failing-gate reason so the next plan is told WHY it stalled (GAP 3)
        return v.model_copy(update={"status": VerdictStatus.REPEAT,
                                    "gate_result": GateResult.FAIL,
                                    "gate_reason": fail})
    return v.model_copy(update={"gate_result": GateResult.PASS, "gate_reason": None})


def next_phase(current: PhaseSpec, verdict: PhaseVerdict) -> str | None:
    """Map a (gated) verdict to the next phase id; None ends the run."""
    if verdict.status == VerdictStatus.DONE:
        return None
    nxt = current.on_verdict.get(verdict.status.value)
    if nxt is None and verdict.status == VerdictStatus.REPEAT:
        return current.id            # default: REPEAT re-enters the same phase
    return nxt
