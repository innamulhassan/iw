"""projection.py — the graph's single-assertion-store read/write projection (P6 store-flip).

The graph stores ONE assertion collection; the facts/events views are derived. These four
converters are its exact-inverse read/write seams (Fact/Event ⇄ Assertion) — every field
round-trips byte-identically (proven by the golden suite + unit tests). Formerly co-located with
the AddFact/AddEvent→AddAssertion compat shim in `domain/shim.py`; that op-level shim was RETIRED
(the live planner now emits AddAssertion natively — F4), so this permanent store-projection seam
lives here under an honest name, no longer masquerading as a "deprecated shim".

`species_for_predicate` (the §9.1 Descriptor-vs-State boundary test) stays here because the
record converters use it (an assertion reconstructed from a Fact re-derives its species) and the
live planner reuses it to classify a hand-authored fact. The species rides on the Assertion, never
on the reconstructed Fact — so a misclassification cannot change any graph output or golden. When
in doubt the boundary test says State (the cheap direction: a window you never query costs nothing).
"""
from __future__ import annotations

from .assertion import Assertion, channel_for_source
from .enums import Species
from .event import Event
from .fact import Fact

# ── §9.1 boundary test as data ────────────────────────────────────────────────
# DESCRIPTOR = knowledge ABOUT the entity whose history never participates in causal
# reasoning: identity-adjacent facts (repo, owner, language, node_name) and content payloads
# (diff/blame/distribution/change-size). Everything else a measured/inferred fact asserts is
# operational and its onset value can matter → STATE. Readings (metrics with an explicit
# stat+window) are handled separately; a hand-authored fact never has a stat/window, so measured
# metrics fall through to STATE, matching "when in doubt → State".
_DESCRIPTOR_PREDICATES: frozenset[str] = frozenset({
    # identity-adjacent / timeless facts about the entity
    "repo", "owner", "language", "node_name", "image", "table_count", "index_health",
    # content payloads (must stay renderable to the LLM — never demoted to evidence[])
    "diff_summary", "blame_line", "blame", "status_code_dist",
    "files_changed", "lines_added", "lines_deleted", "last_duration", "last_seen",
})


def species_for_predicate(predicate: str, *, has_reading_shape: bool = False) -> Species:
    """The boundary test: EVENT is decided by the op kind (species=EVENT), never here. A fact with
    an explicit reading shape (stat+window) is a READING; a content/identity-adjacent predicate is a
    DESCRIPTOR; otherwise STATE (the cheap default)."""
    if has_reading_shape:
        return Species.READING
    if predicate in _DESCRIPTOR_PREDICATES:
        return Species.DESCRIPTOR
    return Species.STATE


# ── P6 store-flip: RECORD-level converters (Fact/Event ⇄ Assertion) ───────────
# The graph stores ONE assertion collection; these four are its exact-inverse read/write seams.
# Every field round-trips byte-identically (proven by the golden suite + unit tests): the
# species on a converted Fact is re-derived by the same §9.1 boundary test — deterministic, and
# never surfaced in any rendered view, so a reclassification cannot move a byte in the bundle. The
# channel is derived from the source exactly (LLM → inferred, engine → engine, observing
# tools/humans → measured) — never DECLARED, so every converted Fact stays in the facts view
# (DECLARED is the node-prop channel, P6 step 2).

def assertion_of_fact(f: Fact) -> Assertion:
    """Fact record → Assertion record (the store's write seam for facts)."""
    return Assertion(
        id=f.id, subject_ref=f.subject_ref, name=f.predicate, value=f.value, unit=f.unit,
        where=f.where, species=species_for_predicate(f.predicate),
        channel=channel_for_source(f.source),
        valid_from=f.valid_from, valid_to=f.valid_to, observed_at=f.observed_at,
        source=f.source, source_native_name=f.source_native_name,
        confidence=f.confidence, source_reliability=f.source_reliability,
        evidence=f.evidence, supersedes=f.supersedes, state=f.state,
        provisional=f.provisional, created_by=f.created_by)


def fact_of_assertion(a: Assertion) -> Fact:
    """Assertion record → Fact record (the facts view's read seam — exact inverse of
    `assertion_of_fact`; `name` returns to `predicate`)."""
    return Fact(
        id=a.id, subject_ref=a.subject_ref, predicate=a.name, value=a.value, unit=a.unit,
        where=a.where, valid_from=a.valid_from, valid_to=a.valid_to, observed_at=a.observed_at,
        source=a.source, source_native_name=a.source_native_name,
        confidence=a.confidence, source_reliability=a.source_reliability,
        evidence=a.evidence, supersedes=a.supersedes, state=a.state,
        provisional=a.provisional, created_by=a.created_by)


def assertion_of_event(e: Event) -> Assertion:
    """Event record → Assertion record (species EVENT; `type` → `name`, `payload` → `value`)."""
    return Assertion(
        id=e.id, subject_ref=e.entity_ref, name=e.type, value=e.payload,
        species=Species.EVENT, channel=channel_for_source(e.source),
        occurred_at=e.occurred_at, observed_at=e.observed_at,
        source=e.source, source_native_name=e.source_native_name, state=e.state,
        invalidated_by=e.invalidated_by, provisional=e.provisional, created_by=e.created_by)


def event_of_assertion(a: Assertion) -> Event:
    """Assertion record → Event record (the events view's read seam — exact inverse of
    `assertion_of_event`; an Event payload is always a dict, so `value` round-trips exactly)."""
    return Event(
        id=a.id, entity_ref=a.subject_ref, type=a.name, occurred_at=a.occurred_at,
        observed_at=a.observed_at, payload=a.value if isinstance(a.value, dict) else {},
        source=a.source, source_native_name=a.source_native_name, state=a.state,
        invalidated_by=a.invalidated_by, provisional=a.provisional, created_by=a.created_by)
