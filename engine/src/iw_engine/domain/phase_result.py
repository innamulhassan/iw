"""PhaseResult — the ONE uniform contract every phase emits (DESIGN §2.2 R-P1). This is
the single seam: the engine has one `fold(PhaseResult)`; each field folds into exactly
one store. Adding/reordering a phase or a whole new playbook needs no new plumbing.
Carries already-materialised Node/Fact/Edge/Event (the reducer turned the planner's ops
into these) + the hypothesis store deltas + the one prose field (narrative) + the verdict.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .common import Confidence
from .edge import Edge
from .enums import GateResult, Phase, VerdictStatus
from .event import Event
from .fact import Fact
from .hypothesis import HypDelta
from .node import Node


class PhaseVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerdictStatus            # advance | repeat | backtrack | blocked | done
    confidence: Confidence           # why this verdict — cited, not asserted
    gate_result: GateResult = GateResult.PASS
    gate_reason: str | None = None   # WHY the gate downgraded ADVANCE->REPEAT — fed back to
    #                                  the next plan so a live planner learns why it stalled (GAP 3)


class PhaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase_id: Phase
    goal_restated: str
    facts_added: list[Fact] = Field(default_factory=list)          # -> GRAPH
    events_added: list[Event] = Field(default_factory=list)        # -> GRAPH
    nodes_touched: list[Node] = Field(default_factory=list)        # -> GRAPH
    edges_added: list[Edge] = Field(default_factory=list)          # -> GRAPH
    hypotheses_updated: list[HypDelta] = Field(default_factory=list)  # -> HYPOTHESIS STORE
    narrative: str                                                 # -> JOURNAL (the ONLY prose field)
    next_actions: list[str] = Field(default_factory=list)          # -> CONTROLLER (advisory)
    verdict: PhaseVerdict                                          # -> CONTROLLER (authoritative)

    def is_empty_delta(self) -> bool:
        return not (self.facts_added or self.events_added or self.nodes_touched
                    or self.edges_added or self.hypotheses_updated)
