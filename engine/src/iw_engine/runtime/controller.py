"""Controller — the routing authority (DESIGN §2.3 R-C1). It applies the phase's declared
gate-predicate set to the planner's PROPOSED terminal-intent verdicts — an 'advance' AND a
'done' are only honoured when every declared predicate passes (P7 step 4: DONE is gated,
never a free bypass) — and picks the next phase from the verdict + the playbook's
transitions. The LLM's `next_actions` is advisory (it seeds the next plan), never the
phase router. The engine owns predicate SEMANTICS; playbooks compose which apply.
"""
from __future__ import annotations

from ..domain.enums import FactState, GateResult, HypothesisStatus, VerdictStatus
from ..domain.phase_result import PhaseResult, PhaseVerdict
from ..domain.playbook import PhaseSpec, Tunables
from ..graph.graph import Graph
from ..hypothesis.store import HypothesisStore
from ..journal.journal import Journal


def _symptom_cleared(graph: Graph | None, anomaly_ref: str | None, cleared_event: str) -> bool:
    """The recovery predicate: the symptom node carries an ACTIVE event of the playbook's
    declared cleared type. Keyed on the symptom ROLE BINDING + a playbook-declared event
    name — the engine hardcodes no domain vocabulary. A run without a framed symptom (or
    without a graph) cannot claim recovery."""
    if graph is None or not anomaly_ref:
        return False
    nid = graph.id_remaps.get(anomaly_ref, anomaly_ref)
    return any(e.entity_ref == nid and e.type == cleared_event
               and e.state == FactState.ACTIVE for e in graph.events.values())


def _human_approved(journal: Journal | None, phase_id: str) -> bool:
    """The governance predicate: a human gate_decision (approve — or refine, which approves
    edited params) is on the durable journal FOR THIS PHASE. A denial does not satisfy it,
    and a batch run without a human in the loop cannot fake it."""
    if journal is None:
        return False
    return any(e.kind == "gate_decision" and e.phase_id == phase_id
               and e.decision in ("approve", "refine") for e in journal.entries)


def check_gate(spec: PhaseSpec, result: PhaseResult, store: HypothesisStore,
               tunables: Tunables, *, graph: Graph | None = None,
               journal: Journal | None = None, anomaly_ref: str | None = None,
               symptom_cleared_event: str = "cleared") -> PhaseVerdict:
    """Guard the planner's verdict. Terminal-intent verdicts (ADVANCE and DONE — P7 step 4)
    are gated; a failed predicate downgrades to REPEAT so the phase runs again rather than
    terminating/advancing on thin evidence."""
    v = result.verdict
    if v.status not in (VerdictStatus.ADVANCE, VerdictStatus.DONE):
        return v

    fail: str | None = None
    for field_name in spec.produces_required:
        # NoEvidence facts count — an honest null result satisfies the floor (R-P2)
        if not getattr(result, field_name, None):
            fail = f"produces_required '{field_name}' empty"
            break
    if fail is None and len(result.facts_added) < spec.gate.min_facts:
        fail = f"min_facts {spec.gate.min_facts} not met"
    if fail is None and spec.gate.promotion:
        # INV-8: promotion is the ENGINE's decision, never the LLM's. promotion_ok requires
        # the leader to cross the confidence gate, beat the field by delta, and have NO
        # alive unrefuted rival — an LLM-set CONFIRMED status grants no bypass (the old
        # status short-circuit let a rival-contested or below-gate hypothesis clear the
        # gate; 2026-07-22 review, finding 2).
        if not store.promotion_ok(tunables):
            fail = ("confidence gate not met (lead below gate, margin < delta, "
                    "or an unrefuted rival is still alive)")
    if fail is None and spec.gate.refutation_attempted:
        # genuine differential investigation: a rival was ruled out, or the leader was challenged
        refuted_rival = any(h.status == HypothesisStatus.REFUTED for h in store.hypotheses.values())
        lead = store.leading()
        lead_challenged = lead is not None and bool(lead.refuting_facts)
        if not (refuted_rival or lead_challenged):
            fail = "no refutation attempted (no rival ruled out, leader unchallenged)"
    if fail is None and spec.gate.symptom_cleared:
        if not _symptom_cleared(graph, anomaly_ref, symptom_cleared_event):
            fail = (f"symptom not cleared (no active '{symptom_cleared_event}' event on "
                    f"the symptom node {anomaly_ref or '(never framed)'})")
    if fail is None and spec.gate.human_approved:
        if not _human_approved(journal, result.phase_id):
            fail = "no human approval on the journal for this phase (gate_decision approve/refine)"

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
