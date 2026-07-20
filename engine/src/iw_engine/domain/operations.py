"""Operations — the planner's ONLY output channel (principle 7: prose is a field, not the
medium). Every graph mutation the LLM/planner wants is a typed op in this closed tagged
union; the reducer validates + materialises them into Node/Fact/Edge/Event/HypDelta.
`subject`/`src`/`dst` are NodeIds (use registry.node_id to compute them deterministically);
confidence is a coarse rubric enum (R-C4), mapped to a numeric band by the reducer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import EvidenceRef
from .enums import ConfidenceLevel, EdgeType, NodeType, OpKind, Origin, Source
from .fact import FactValue
from .hypothesis import ChainLink


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddNode(_Op):
    op: Literal[OpKind.ADD_NODE] = OpKind.ADD_NODE
    type: NodeType
    props: dict = Field(default_factory=dict)


class AddFact(_Op):
    op: Literal[OpKind.ADD_FACT] = OpKind.ADD_FACT
    subject: str                       # NodeId (or EdgeId)
    predicate: str
    value: FactValue = None
    unit: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    observed_at: datetime
    source: Source
    confidence_level: ConfidenceLevel | None = None      # for inferred facts
    source_reliability: float | None = None              # for measured facts
    evidence: list[EvidenceRef] = Field(default_factory=list)


class AddEvent(_Op):
    op: Literal[OpKind.ADD_EVENT] = OpKind.ADD_EVENT
    entity: str                        # NodeId
    type: str
    occurred_at: datetime
    observed_at: datetime
    payload: dict = Field(default_factory=dict)
    source: Source


class AddEdge(_Op):
    op: Literal[OpKind.ADD_EDGE] = OpKind.ADD_EDGE
    type: EdgeType
    src: str
    dst: str
    origin: Origin | None = None       # default taken from the edge spec
    props: dict = Field(default_factory=dict)
    confidence_level: ConfidenceLevel | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ProposeHypothesis(_Op):
    op: Literal[OpKind.PROPOSE_HYPOTHESIS] = OpKind.PROPOSE_HYPOTHESIS
    hid: str                           # a stable local id for cross-reference within the run
    statement: str
    root_candidate: str | None = None
    causal_chain: list[ChainLink] = Field(default_factory=list)
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
    supporting: list[str] = Field(default_factory=list)   # FactIds
    refuting: list[str] = Field(default_factory=list)
    predictions: list[str] = Field(default_factory=list)


class UpdateHypothesis(_Op):
    op: Literal[OpKind.UPDATE_HYPOTHESIS] = OpKind.UPDATE_HYPOTHESIS
    hid: str
    new_status: str | None = None
    confidence_level: ConfidenceLevel | None = None
    add_supporting: list[str] = Field(default_factory=list)
    add_refuting: list[str] = Field(default_factory=list)
    add_chain: list[ChainLink] = Field(default_factory=list)
    basis: str = ""


class NoEvidence(_Op):
    """Honest null result (R-P2). Satisfies produces_required AND refutes hypotheses:
    'we looked at X and it was clean' is evidence."""

    op: Literal[OpKind.NO_EVIDENCE] = OpKind.NO_EVIDENCE
    intent: str
    scope: str              # a NodeId we looked at (falls back to the anomaly node)
    basis: str
    at: datetime            # when we looked (the null-result is itself timestamped evidence)


Operation = Annotated[
    AddNode | AddFact | AddEvent | AddEdge | ProposeHypothesis | UpdateHypothesis | NoEvidence,
    Field(discriminator="op"),
]
