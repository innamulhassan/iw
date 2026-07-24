"""Hypothesis — a first-class, evidence-backed causal explanation (never a floating
string, the old model's core defect). It holds BOTH supporting and refuting facts
(anti-confirmation-bias, principle 10); its causal_chain is an ordered list of typed
links (events / facts / changes) forming a timeline. The confirmed hypothesis with a
verified fix IS the root cause (DESIGN §2.1 R-G2).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .common import Confidence
from .enums import ChainLinkKind, ChainRole, HypothesisStatus, StrEnum


class ChainLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ChainLinkKind          # event | fact | change
    ref: str                     # EventId | FactId | NodeId(ChangeEvent)
    ts: datetime
    role: ChainRole              # cause | condition | effect
    note: str | None = None


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str               # "if true we'd also see X" — drives the next INVESTIGATE
    checked: bool = False
    held: bool | None = None


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    statement: str
    causal_chain: list[ChainLink] = Field(default_factory=list)
    root_candidate: str | None = None            # NodeId — the chain head (initiating change/fault)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: Confidence
    # AssertionIds of ANY species (§6 break A — the Fact was a special case; an Event/Span/Reading
    # cited as evidence resolves in the one assertion store, weighed by its species-appropriate time
    # anchor). The field name keeps its Fact-era spelling for store/journal compatibility; its
    # semantics are now "cited evidence assertions", the SUPPORTS/REFUTES edges recomputed from them.
    supporting_facts: list[str] = Field(default_factory=list)   # AssertionIds (SUPPORTS)
    refuting_facts: list[str] = Field(default_factory=list)     # AssertionIds (REFUTES)
    predictions: list[Prediction] = Field(default_factory=list)
    created_by: int
    updated_by: list[int] = Field(default_factory=list)
    # GOLDEN-DETERMINISTIC belief timestamps: stamped by the store's apply() from the DETERMINISTIC
    # phase clock (the seq's journaled ts), NEVER a wall-clock read — so a replayed store reproduces
    # them bit-for-bit (R-J1). `proposed_at` is frozen at first CREATE; `updated_at` moves on every
    # change (create / evidence / rerank / verdict). ISO strings (None until a clock-bearing apply).
    proposed_at: str | None = None
    updated_at: str | None = None


class HypAction(StrEnum):
    CREATE = "create"
    ATTACH_EVIDENCE = "attach_evidence"
    RERANK = "rerank"
    CONFIRM = "confirm"
    REFUTE = "refute"
    SUPERSEDE = "supersede"


class HypDelta(BaseModel):
    """A hypothesis store mutation carried in PhaseResult.hypotheses_updated."""

    model_config = ConfigDict(extra="forbid")

    action: HypAction
    hypothesis: Hypothesis | None = None         # for CREATE
    hypothesis_id: str | None = None             # for updates
    new_status: HypothesisStatus | None = None
    confidence: Confidence | None = None
    add_supporting: list[str] = Field(default_factory=list)
    add_refuting: list[str] = Field(default_factory=list)
    add_chain: list[ChainLink] = Field(default_factory=list)
    basis: str = ""
