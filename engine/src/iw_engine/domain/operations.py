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

from .assertion import AssertionValue, Window
from .common import EvidenceRef
from .enums import Channel, ConfidenceLevel, EdgeType, NodeType, OpKind, Origin, Source, Species, Stat
from .fact import FactValue
from .hypothesis import ChainLink


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddNode(_Op):
    op: Literal[OpKind.ADD_NODE] = OpKind.ADD_NODE
    type: NodeType
    props: dict = Field(default_factory=dict)
    # P6 makes node props sourced assertions; the source is known at mint time, so the field is
    # threaded here (default None = no behavior change, existing callers stay valid). The reducer
    # does not read it yet — pure groundwork.
    source: Source | None = None


class AddAssertion(_Op):
    """The P1a atom op (build-spec §2.2) — a superset of AddFact carrying the species + reading
    qualifiers. AddFact/AddEvent are deprecated compat shims that map onto this (domain.shim).
    `channel` is optional: the reducer defaults it from the source. Belief stays UNRESOLVED here
    (a coarse `confidence_level` rubric / raw `source_reliability`); the reducer resolves the
    rubric to a numeric band and applies the INV-9 per-source reliability default, exactly as it
    does for AddFact."""

    op: Literal[OpKind.ADD_ASSERTION] = OpKind.ADD_ASSERTION
    subject: str                       # NodeId or EdgeId
    name: str                          # dictionary-canonical name (P2 validates; P1a accepts any)
    value: AssertionValue = None
    unit: str | None = None
    species: Species
    channel: Channel | None = None     # default derived from source by the reducer
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    observed_at: datetime | None = None
    occurred_at: datetime | None = None            # EVENT only
    stat: Stat | None = None                       # READING only
    window: Window | None = None                   # READING only
    source: Source
    source_native_name: str | None = None
    confidence_level: ConfidenceLevel | None = None      # for inferred assertions
    source_reliability: float | None = None              # for measured assertions
    evidence: list[EvidenceRef] = Field(default_factory=list)


class AddFact(_Op):
    """DEPRECATED compat op — a state/descriptor/reading assertion in the pre-atom shape. Retained
    post-P1b (adapters + scenarios now emit AddAssertion) solely as the LivePlanner's model-JSON
    parse target (`add_fact`) + the reducer compat tests; routed onto AddAssertion via domain.shim.
    Deleted once the planner emits AddAssertion natively (a later phase)."""

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
    """DEPRECATED compat op — an occurrence in the pre-atom shape. Retained post-P1b (adapters +
    scenarios now emit AddAssertion with species=event) solely as the LivePlanner's model-JSON
    parse target (`add_event`) + the reducer compat tests; routed onto AddAssertion via domain.shim.
    Deleted once the planner emits AddAssertion natively (a later phase)."""

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
    AddNode | AddAssertion | AddFact | AddEvent | AddEdge | ProposeHypothesis | UpdateHypothesis
    | NoEvidence,
    Field(discriminator="op"),
]
