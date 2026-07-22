"""The HypothesisStore — ranked, evidence-backed causal explanations (a projection of
the PhaseResult.hypotheses_updated stream). NOTE the name: the append-only JOURNAL is the
real ledger/system-of-record; THIS is a mutable key->state projection beside the graph, so
it is a hypothesis store, not a ledger (owner directive, 2026-07-22: "remove ledger, that is
what journal is for"). Belief moves only via HypDelta carrying a basis; refuted hypotheses
are KEPT (they are evidence). Confirmation is Popperian (DESIGN §2.3 R-C4): a hypothesis is
promotable when it crosses the confidence gate, leads the field by the margin, and has no
unrefuted competitor.
"""
from __future__ import annotations

from ..domain.enums import HypothesisStatus
from ..domain.hypothesis import HypAction, HypDelta, Hypothesis
from ..domain.playbook import Tunables

_STATUS_RANK = {
    HypothesisStatus.CONFIRMED: 5,
    HypothesisStatus.SUPPORTED: 4,
    HypothesisStatus.INVESTIGATING: 3,
    HypothesisStatus.PROPOSED: 2,
    HypothesisStatus.REFUTED: 0,
    HypothesisStatus.SUPERSEDED: -1,
}

# A reached verdict is a terminal state: a re-CREATE of a hid already in one of these is a
# no-op — a REFUTED (or CONFIRMED / SUPERSEDED) hypothesis is INDESTRUCTIBLE evidence and must
# never be silently reset to PROPOSED by a live planner re-proposing the same id (Track-4 #1).
_TERMINAL = frozenset({HypothesisStatus.REFUTED, HypothesisStatus.CONFIRMED,
                       HypothesisStatus.SUPERSEDED})


class HypothesisStore:
    def __init__(self) -> None:
        self.hypotheses: dict[str, Hypothesis] = {}

    def apply(self, deltas: list[HypDelta], seq: int) -> None:
        for d in deltas:
            if d.action == HypAction.CREATE and d.hypothesis is not None:
                h = d.hypothesis
                existing = self.hypotheses.get(h.id)
                if existing is None:
                    # first CREATE of this hid — insert
                    self.hypotheses[h.id] = h.model_copy(
                        update={"created_by": h.created_by or seq})
                elif existing.status not in _TERMINAL:
                    # re-CREATE of a LIVE hid is an UPDATE, never a destructive overwrite
                    # (Track-4 #1): the accumulated status, evidence lists, chain, and created_by
                    # audit trail are PRESERVED; only the descriptive fields refresh and evidence
                    # can only grow. This closes the CREATE-destroys-REFUTED corruption.
                    self.hypotheses[h.id] = existing.model_copy(update={
                        "statement": h.statement,
                        "confidence": h.confidence,
                        "root_candidate": h.root_candidate or existing.root_candidate,
                        "supporting_facts": sorted({*existing.supporting_facts, *h.supporting_facts}),
                        "refuting_facts": sorted({*existing.refuting_facts, *h.refuting_facts}),
                        "causal_chain": [*existing.causal_chain, *h.causal_chain],
                        "updated_by": [*existing.updated_by, seq]})
                # else: existing is TERMINAL (REFUTED/CONFIRMED/SUPERSEDED) — indestructible no-op
                continue
            hid = d.hypothesis_id
            if hid is None or hid not in self.hypotheses:
                continue
            h = self.hypotheses[hid]
            upd: dict = {"updated_by": [*h.updated_by, seq]}
            if d.new_status is not None:
                upd["status"] = d.new_status
            if d.confidence is not None:
                upd["confidence"] = d.confidence
            if d.add_supporting:
                upd["supporting_facts"] = sorted({*h.supporting_facts, *d.add_supporting})
            if d.add_refuting:
                upd["refuting_facts"] = sorted({*h.refuting_facts, *d.add_refuting})
            if d.add_chain:
                upd["causal_chain"] = [*h.causal_chain, *d.add_chain]
            if d.action == HypAction.REFUTE and "status" not in upd:
                upd["status"] = HypothesisStatus.REFUTED
            if d.action == HypAction.CONFIRM and "status" not in upd:
                upd["status"] = HypothesisStatus.CONFIRMED
            self.hypotheses[hid] = h.model_copy(update=upd)

    # ── queries ───────────────────────────────────────────────────────────────
    def _key(self, h: Hypothesis) -> tuple[int, float]:
        return (_STATUS_RANK.get(h.status, 1), h.confidence.value)

    def ranked(self) -> list[Hypothesis]:
        return sorted(self.hypotheses.values(), key=self._key, reverse=True)

    def alive(self) -> list[Hypothesis]:
        return [h for h in self.ranked()
                if h.status not in (HypothesisStatus.REFUTED, HypothesisStatus.SUPERSEDED)]

    def leading(self) -> Hypothesis | None:
        alive = self.alive()
        return alive[0] if alive else None

    def confirmed(self) -> Hypothesis | None:
        for h in self.hypotheses.values():
            if h.status == HypothesisStatus.CONFIRMED:
                return h
        return None

    def promotion_ok(self, tunables: Tunables) -> bool:
        """Leading crosses the gate, beats the runner-up by delta, no unrefuted rival.

        EVERY alive competitor counts as a rival, at ANY band — Popperian confirmation
        (R-C4/INV-8) demands rivals be REFUTED, not merely out-scored, so an unrefuted
        0.75 rival under a 0.8 gate still blocks promotion (2026-07-22 review, finding 2).
        """
        alive = self.alive()
        if not alive:
            return False
        lead = alive[0]
        if lead.confidence.value < tunables.confidence_gate:
            return False
        rivals = alive[1:]   # ALL alive unrefuted rivals block — no band filter
        if rivals:
            return False
        runner = alive[1].confidence.value if len(alive) > 1 else 0.0
        return (lead.confidence.value - runner) >= tunables.delta
