"""Fact — a reified, bi-temporal, sourced observation about a node or edge.

The single most important modelling ruling (DESIGN §2.1 R-G5): a Fact is what was
TRUE OF a thing OVER A WINDOW, not a mutable property stamped on it. Facts are never
mutated — a newer value SUPERSEDES (closing valid_to); a wrong observation is RETRACTED.
This is what makes "reconstruct the graph as of incident-start" answerable.

Bi-temporal: `valid_from/valid_to` = real-world truth window; `observed_at` = when we
learned it (transaction time). `valid_to=None` means "still true" (open interval).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Confidence, EvidenceRef, enforce_belief_exclusivity
from .enums import FactState, Source

# a fact value is one of a small typed set; `unit` qualifies numbers
FactValue = bool | int | float | str | dict | None


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    subject_ref: str                       # NodeId or EdgeId this fact is about (WHAT/WHERE-entity)
    predicate: str                         # registry-controlled per node type (reducer-validated)
    value: FactValue = None
    unit: str | None = None

    # bi-temporal
    valid_from: datetime
    valid_to: datetime | None = None       # None = still true
    observed_at: datetime

    source: Source
    source_native_name: str | None = None  # the vendor's own spelling before dictionary canonicalization
                                           # (P2 §2.3) — provenance survives the rename; None = LLM-authored
    # exactly one belief channel is meaningful per fact (R-C4):
    confidence: Confidence | None = None          # for INFERRED facts
    source_reliability: float | None = Field(default=None, ge=0.0, le=1.0)  # for MEASURED facts

    evidence: list[EvidenceRef] = Field(default_factory=list)
    supersedes: str | None = None          # FactId this replaces (never mutate — supersede)
    state: FactState = FactState.ACTIVE
    # P3 airlock (DOMAIN-v3 §2.4): True for knowledge the airlock admitted rather than the
    # closed vocabulary — a name-quarantined fact (`x.<source>.<native>`) or a known name with
    # an off-shape reading. Rendered dimly, counted toward promotion, never silently erased.
    provisional: bool = False
    created_by: int                        # journal seq — lineage

    @model_validator(mode="after")
    def _window_ok(self) -> Fact:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError(f"fact {self.id}: valid_to < valid_from")
        return self

    @model_validator(mode="after")
    def _belief_channel(self) -> Fact:
        """Enforce R-C4 (VALIDATION-VERDICT §B P0 #3): exactly one belief channel is meaningful,
        and WHICH one is fixed by provenance — an INFERRED fact (source=llm, the model reasoned
        it into being) carries a `confidence`; a directly-MEASURED fact (any tool/engine/human
        observation) carries `source_reliability`. The rule itself is the ONE shared enforcer
        (`common.enforce_belief_exclusivity`): a Fact is a view over an Assertion, so both records
        route through the SAME function instead of hand-writing the invariant twice (M16 — before,
        this keyed on source==llm while `Assertion._belief_channel` keyed on channel, two
        implementations of one rule that could drift)."""
        enforce_belief_exclusivity(
            f"fact {self.id}:", inferred=(self.source == Source.LLM),
            inferred_desc="inferred (source=llm) fact",
            measured_desc=f"measured (source={self.source.value}) fact",
            confidence=self.confidence, source_reliability=self.source_reliability)
        return self

    @property
    def is_open(self) -> bool:
        return self.valid_to is None and self.state == FactState.ACTIVE
