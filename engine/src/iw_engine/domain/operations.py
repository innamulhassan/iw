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
    """The ONE atom op (build-spec §2.2) — the state/descriptor/reading/event assertion carrying its
    species + reading qualifiers. Adapters, scenario twins AND the live planner all emit it natively
    (F4 retired the AddFact/AddEvent compat shims that used to map onto it). `channel` is optional:
    the reducer defaults it from the source. Belief stays UNRESOLVED here (a coarse `confidence_level`
    rubric / raw `source_reliability`); the reducer resolves the rubric to a numeric band and applies
    the INV-9 per-source reliability default."""

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


class Retract(_Op):
    """Tombstone a WRONG observation (P3 airlock step 6 — R-J3 finally reachable through the op
    grammar). Targets a materialized fact/event/edge id; the fold sets state=RETRACTED (the
    record survives as evidence of what was once believed — append-only, never deleted). Replay-
    safe: the retraction rides the PhaseResult delta, so a journal replay tombstones the same id
    at the same seq. Hypothesis records are NOT retracted here — refutation via UpdateHypothesis
    is their lifecycle."""

    op: Literal[OpKind.RETRACT] = OpKind.RETRACT
    target: str                        # FactId | EventId | EdgeId to tombstone
    invalidated_by: str | None = None  # id of the fact/hypothesis that proved it wrong
    reason: str = ""                   # the narrative WHY (journaled with the delta)


class Merge(_Op):
    """Fold a PROVISIONAL entity into its canonical identity (P5 step 5 — R-J5 / DOMAIN-v3
    §9.2 late alias binding). An observation keyed only by a tool credential mints a
    provisional entity; when its canonical identity becomes known, Merge graduates it — every
    reference re-homes via the remap subsystem, the provisional id stays resolvable in the
    old→new table, aliases follow. provisional→canonical ONLY: canonical entities never merge
    (the original 'never merge' survives where it matters). The reducer also auto-materializes
    this fold when a canonical arrival's credential is already bound to a provisional twin —
    this op is the explicit lane for the planner/engine."""

    op: Literal[OpKind.MERGE] = OpKind.MERGE
    provisional_id: str                # the provisional entity being folded in
    canonical_id: str                  # the canonical entity absorbing it (alias forms resolve)
    reason: str = ""                   # journaled WHY (which binding proved they are one)


class Retype(_Op):
    """Graduate the escape hatch (P5 step 6 — DOMAIN-v3 §2.4 row 2 / §9.2, closing audit 4
    S2.4 "re-typing later: no path"): re-key a `generic_ci` as the real NodeType its
    `class_hint` promised. Mints the canonical entity (identity props for the new type ride on
    `props`, merged over the old node's — `ci_id`/`class_hint` survive as provenance), remaps
    every reference through the remap subsystem, and the old id becomes an alias via the
    graph-level old→new table — write-once identity is never violated (a retype is an alias
    graduation, not an identity edit). History survives: facts/events/edges re-home, nothing
    orphans. generic_ci only: canonical typed entities never re-key."""

    op: Literal[OpKind.RETYPE] = OpKind.RETYPE
    target: str                        # the generic_ci node id (alias forms resolve)
    new_type: NodeType                 # the real type it turned out to be
    props: dict = Field(default_factory=dict)   # identity (+any extra) props for the new type
    reason: str = ""                   # journaled WHY (e.g. "class_hint corroborated 4x")


Operation = Annotated[
    AddNode | AddAssertion | AddEdge | ProposeHypothesis | UpdateHypothesis
    | NoEvidence | Retract | Merge | Retype,
    Field(discriminator="op"),
]
