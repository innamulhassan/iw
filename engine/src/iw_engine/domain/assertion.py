"""Assertion — the ONE record (DOMAIN-v3 §2.2). Fact + Event + node-prop all collapse here.

One provenance envelope, five temporal species (identity · descriptor · state · reading ·
event) differing only on the time axis. Belief is keyed on `channel` (not on Source identity):
an INFERRED assertion carries a `confidence`; a MEASURED/DECLARED/ENGINE one carries a
`source_reliability`. The vendor's own name for the thing survives on `source_native_name`.

P1a ships this atom behind a compat shim (see operations.AddFact/AddEvent → AddAssertion and
the reducer): today's Fact/Event/props keep working, re-authored natively in P1b.

P6 (the store-flip, part2 §3 + the P1a design decisions): the graph now stores ONE assertion
collection; Fact/Event become read views over it (graph.facts/graph.events, converted via
domain.shim). The atom therefore carries every Fact/Event lifecycle field — `where` (Fact's
spatial W), `provisional` (the P3 airlock flag), and `invalidated_by` (decision 1: the
retraction lifecycle covers events too, so the tombstone's cause lives on the atom).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Confidence, EvidenceRef
from .enums import Channel, FactState, Source, Species, Stat

# an assertion value is one of a small typed set; `unit` qualifies numbers (mirrors FactValue).
# P6 widens it with datetime/list: node props are DECLARED assertions now, and vendor-declared
# identity surfaces carry timestamps (change windows) and lists — exact types survive the flip
# (a datetime prop must render byte-identically, never silently stringified).
AssertionValue = bool | int | float | str | dict | list | datetime | None


# ── Source → default belief channel (DOMAIN-v3 §2.2) ──────────────────────────
# Belief moves off the Source.LLM identity special-case onto an explicit channel. The map is
# chosen so the Fact-era belief discipline round-trips EXACTLY: channel==INFERRED iff the
# source is the reasoning model (LLM), so an inferred fact keeps its confidence and a measured
# fact keeps its reliability. (Registry placement of this map is deferred to P2; a module dict
# now, per build-spec step 2.)
_SOURCE_CHANNEL: dict[Source, Channel] = {
    Source.LLM: Channel.INFERRED,
    Source.ENGINE: Channel.ENGINE,
}


def channel_for_source(source: Source) -> Channel:
    """The default belief channel for a source. LLM → inferred (confidence); engine → engine;
    every directly-observing tool/human → measured (reliability)."""
    return _SOURCE_CHANNEL.get(source, Channel.MEASURED)


class Window(BaseModel):
    """A reading's observation window (DOMAIN-v3 §2.2): a point `at`, or a range `[start, end)`.
    Exactly one mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime | None = None
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _one_mode(self) -> Window:
        point = self.at is not None
        rng = self.start is not None or self.end is not None
        if point and rng:
            raise ValueError("window is a point (at) OR a range (start/end), not both")
        if not point and not rng:
            raise ValueError("window must declare a point (at) or a range (start/end)")
        if rng and (self.start is None or self.end is None):
            raise ValueError("a range window needs both start and end")
        if rng and self.end < self.start:
            raise ValueError("window end < start")
        return self


class Assertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    subject_ref: str                         # EntityId | EdgeId this is about (edges now reachable)
    name: str                                # dictionary-canonical (P2 validates; P1a accepts any)
    value: AssertionValue = None
    unit: str | None = None
    where: str | None = None                 # optional spatial/context W (Fact-era field, P6 flip)
    species: Species
    channel: Channel                         # belief keyed on channel, NOT Source identity

    # ── time — by species ─────────────────────────────────────────────────────
    valid_from: datetime | None = None       # STATE window start (+ optional on descriptor via shim)
    valid_to: datetime | None = None         # STATE window end (None = still true)
    observed_at: datetime | None = None      # transaction time — all species except identity
    occurred_at: datetime | None = None      # EVENT only — when it happened in the world

    # ── reading qualifiers ─────────────────────────────────────────────────────
    stat: Stat | None = None                 # READING only
    window: Window | None = None             # READING only

    # ── provenance / belief ────────────────────────────────────────────────────
    source: Source
    source_native_name: str | None = None    # the vendor's own name for this (P2 populates from aliases)
    confidence: Confidence | None = None                      # INFERRED channel
    source_reliability: float | None = Field(default=None, ge=0.0, le=1.0)  # MEASURED/DECLARED/ENGINE

    evidence: list[EvidenceRef] = Field(default_factory=list)
    supersedes: str | None = None
    state: FactState = FactState.ACTIVE
    invalidated_by: str | None = None        # id of what proved this wrong (P1a decision 1: the
                                             # retraction lifecycle lives on the atom — events too)
    # P3 airlock: True for knowledge the airlock admitted rather than the closed vocabulary —
    # quarantined name or off-shape reading. Rendered dimly, counted, never silently erased.
    provisional: bool = False
    created_by: int                          # journal seq — lineage

    @model_validator(mode="after")
    def _window_ok(self) -> Assertion:
        if self.valid_to is not None and self.valid_from is not None \
                and self.valid_to < self.valid_from:
            raise ValueError(f"assertion {self.id}: valid_to < valid_from")
        return self

    @model_validator(mode="after")
    def _time_shape(self) -> Assertion:
        """Species/time-shape invariants (build-spec step 1): identity has no observed_at;
        reading requires stat+window; event requires occurred_at; occurred_at is EVENT-only.
        P6 delta: a DECLARED descriptor may omit observed_at — a node-prop declaration is
        asserted configuration truth, timeless like identity (the fold mints it with no
        observation instant, keeping journal replay deterministic — no wall-clock stamp)."""
        if self.species is Species.IDENTITY:
            if self.observed_at is not None:
                raise ValueError(f"assertion {self.id}: identity is write-once — no observed_at")
            if self.valid_from is not None or self.valid_to is not None:
                raise ValueError(f"assertion {self.id}: identity carries no validity window")
        elif not (self.species is Species.DESCRIPTOR and self.channel is Channel.DECLARED):
            if self.observed_at is None:
                raise ValueError(f"assertion {self.id}: {self.species.value} requires observed_at")

        if self.species is Species.READING:
            if self.stat is None or self.window is None:
                raise ValueError(f"assertion {self.id}: reading requires both stat and window")

        if self.species is Species.EVENT:
            if self.occurred_at is None:
                raise ValueError(f"assertion {self.id}: event requires occurred_at")
        elif self.occurred_at is not None:
            raise ValueError(f"assertion {self.id}: occurred_at is EVENT-only")
        return self

    @model_validator(mode="after")
    def _belief_channel(self) -> Assertion:
        """Belief keyed on channel (DOMAIN-v3 §2.2): exactly one belief field is meaningful and
        WHICH one is fixed by the channel — INFERRED carries a confidence, MEASURED/DECLARED/
        ENGINE carry a source_reliability. IDENTITY is asserted truth, not a belief: it carries
        neither. EVENT is lenient in P1a — an occurrence had no belief channel in the Fact/Event
        era, so a shim-minted event may carry belief or none (P1b makes events first-class
        belief-bearing per §2.2); only never both. This is the Fact-era R-C4 discipline restated
        on the envelope."""
        if self.species is Species.IDENTITY:
            if self.confidence is not None or self.source_reliability is not None:
                raise ValueError(
                    f"assertion {self.id}: identity is asserted truth — no belief channel")
            return self
        if self.species is Species.EVENT:
            if self.confidence is not None and self.source_reliability is not None:
                raise ValueError(
                    f"assertion {self.id}: event carries at most one belief field, not both")
            return self
        if self.channel is Channel.INFERRED:
            if self.confidence is None:
                raise ValueError(f"assertion {self.id}: inferred assertion must carry a confidence")
            if self.source_reliability is not None:
                raise ValueError(
                    f"assertion {self.id}: inferred assertion carries confidence, not reliability")
        else:
            if self.source_reliability is None:
                raise ValueError(
                    f"assertion {self.id}: {self.channel.value} assertion must carry "
                    "source_reliability")
            if self.confidence is not None:
                raise ValueError(
                    f"assertion {self.id}: {self.channel.value} assertion carries "
                    "source_reliability, not a confidence")
        return self

    @property
    def is_open(self) -> bool:
        """A still-true state (open valid window) or a live descriptor. Mirrors Fact.is_open —
        the supersession-scan predicate the reducer keys on."""
        return self.valid_to is None and self.state == FactState.ACTIVE
