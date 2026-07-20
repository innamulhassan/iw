"""Edge — a typed, directional graph edge. Structural edges (origin=declared/discovered)
are the durable spine; causal/inferred edges (CAUSED_BY, CORRELATED_WITH, SUPPORTS…)
carry mandatory Confidence + evidence and never mutate the spine (DESIGN §2.1 R-G8).
The graph is a MultiDiGraph, so a structural and an inferred edge between the same pair
coexist (distinct edge ids).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
    confidence: Confidence | None = None            # required for inferred/causal edges
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # provenance envelope — WHO established this relation and WHEN (obs 7: "clearly say what
    # relation and when established"). All optional/defaulted so goldens stay green; the fold
    # surfaces "relation-type · established {time}" on edge hover. Same envelope as Fact.
    source: Source | None = None                    # WHO/which capability discovered the relation
    valid_from: datetime | None = None              # WHEN the relation was established
    observed_at: datetime | None = None             # WHEN we learned of it (transaction time)
    # lifecycle — symmetric with Fact (VALIDATION-VERDICT §B P0 #2). A refuted CAUSED_BY or a
    # superseded structural edge is tombstoned, never mutated: retract sets state + invalidated_by
    # and closes valid_to. The premise is that inferred relationships can be wrong.
    state: FactState = FactState.ACTIVE
    valid_to: datetime | None = None                # None = still holds (open interval)
    invalidated_by: str | None = None               # id of the fact/hypothesis/edge that retracted it
    created_by: int
