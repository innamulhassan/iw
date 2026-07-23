"""Edge — a typed, directional graph edge. Structural edges (origin=declared/discovered)
are the durable spine; causal/inferred edges (CAUSED_BY, CORRELATED_WITH, SUPPORTS…)
carry mandatory Confidence + evidence and never mutate the spine (DESIGN §2.1 R-G8).
The graph is a MultiDiGraph, so a structural and an inferred edge between the same pair
coexist (distinct edge ids).

An edge is the relationship-assertion primitive (NODE-EDGE-PRIMITIVES 2026-07-23 §5): a
directed, typed, time-scoped, provenance-and-belief-bearing REFUTABLE assertion that a
relationship holds between exactly two nodes. Its belief/provenance/lifecycle envelope is
SYMMETRIC WITH THE ATOM (§5.4): an INFERRED edge carries a `confidence`; a
DECLARED/DISCOVERED (observed) edge carries a `source_reliability` — the vendor's own name
for the relation survives on `source_native_name`, and a superseded edge points back at its
prior version via `supersedes` (the same trio the assertion atom carries).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Confidence, EvidenceRef
from .enums import EdgeType, FactState, Origin, Source


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: EdgeType
    src: str                       # NodeId (dependent / effect)
    dst: str                       # NodeId (provider / cause)
    origin: Origin
    props: dict = Field(default_factory=dict)
    confidence: Confidence | None = None            # INFERRED channel — required for inferred/causal edges
    # source_reliability — the OBSERVED-channel belief, symmetric with the atom (2026-07-23 §5.4).
    # A DECLARED edge (CMDB/IaC spine) is trusted ~1.0; a DISCOVERED edge (telemetry-inferred
    # topology) is graded < 1 and, below a per-playbook floor, lands `provisional` (the reducer
    # fills it, §5.2 class 1). Belief is keyed on origin: an edge carries confidence XOR
    # source_reliability, never both (validated below — the atom's `enforce_belief_exclusivity`
    # rule, the never-both half, so a legacy declared edge carrying neither still round-trips).
    source_reliability: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # provenance envelope — WHO established this relation and WHEN (obs 7: "clearly say what
    # relation and when established"). All optional/defaulted so goldens stay green; the fold
    # surfaces "relation-type · established {time}" on edge hover. Same envelope as Fact.
    source: Source | None = None                    # WHO/which capability discovered the relation
    source_native_name: str | None = None           # the vendor's own name for this relation (atom-symmetric)
    valid_from: datetime | None = None              # WHEN the relation was established
    observed_at: datetime | None = None             # WHEN we learned of it (transaction time)
    # lifecycle — symmetric with Fact (VALIDATION-VERDICT §B P0 #2). A refuted CAUSED_BY or a
    # superseded structural edge is tombstoned, never mutated: retract sets state + invalidated_by
    # and closes valid_to; a supersede closes valid_to and the newer edge names the prior via
    # `supersedes`. The premise is that inferred relationships can be wrong (§5.3 invariant 5).
    state: FactState = FactState.ACTIVE
    valid_to: datetime | None = None                # None = still holds (open interval)
    invalidated_by: str | None = None               # id of the fact/hypothesis/edge that retracted it
    supersedes: str | None = None                   # prior-edge pointer (supersede-with-trail, atom-symmetric)
    # P3 type airlock (DOMAIN-v3 §2.4 row 2): True for an edge the airlock admitted — a
    # generic_ci substituted into a structural pair, or a CAUSED_BY blaming a generic_ci.
    # Rendered dimly; carries a reduced confidence; a human RETYPE promotes it out.
    provisional: bool = False
    created_by: int

    @model_validator(mode="after")
    def _belief_never_both(self) -> Edge:
        """Belief-carriage, symmetric with the atom (2026-07-23 §5.3 invariant 3): an edge carries
        AT MOST ONE belief field — an INFERRED edge a `confidence`, an OBSERVED (declared/discovered)
        edge a `source_reliability` — NEVER both. (A legacy declared spine edge may still carry
        neither: the reducer fills reliability for newly-minted observed edges, but a hand-built or
        pre-envelope edge carrying no belief stays valid — the additive never-both half of the atom's
        `enforce_belief_exclusivity`, so the envelope lands without a migration of every spine edge.)"""
        if self.confidence is not None and self.source_reliability is not None:
            raise ValueError(
                f"edge {self.id}: carries at most one belief field — confidence XOR "
                "source_reliability, keyed on origin, never both")
        return self
