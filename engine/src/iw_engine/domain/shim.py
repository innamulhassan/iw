"""The AddFact/AddEvent → AddAssertion compatibility shim (P1a build-spec step 2) — maps the
legacy AddFact/AddEvent ops onto the AddAssertion atom so the reducer has one materialization
path. P6 (the store-flip) adds the RECORD-level twins: Fact/Event ⇄ Assertion converters, the
exact-inverse pair the graph's single assertion store reads and writes through.

P1b RETAINED this thin + deprecated (step 4 "else" branch), NOT removed: the adapters and the
scenario twins now emit AddAssertion natively, but two callers of the legacy ops remain —
  1. `runtime.live_planner` parses a model's `add_fact`/`add_event` JSON into AddFact/AddEvent
     (the LivePlanner is out of P1b scope per the build-spec anti-scope: "Planner/gates/UI must
     not need changes"); the reducer routes those through this shim.
  2. the reducer/shim compat unit tests (`test_shim`, `test_reducer`, `test_projection`,
     `test_session`) exercise this path directly.
The op classes + this routing are deleted only once the planner emits AddAssertion (a later phase).

The species classifier is the §9.1 Descriptor-vs-State boundary test shipped **as data** (a
module dict now; becomes dictionary data in P2). Its choice is intentionally low-risk: the
species rides on the Assertion, never on the reconstructed Fact — so a misclassification cannot
change any graph output or golden. When in doubt the boundary test says State (the cheap
direction: a window you never query costs nothing).
"""
from __future__ import annotations

from .assertion import Assertion, channel_for_source
from .enums import Species
from .event import Event
from .fact import Fact
from .operations import AddAssertion, AddEvent, AddFact

# ── §9.1 boundary test as data ────────────────────────────────────────────────
# DESCRIPTOR = knowledge ABOUT the entity whose history never participates in causal
# reasoning: identity-adjacent facts (repo, owner, language, node_name) and content payloads
# (diff/blame/distribution/change-size). Everything else a measured/inferred fact asserts is
# operational and its onset value can matter → STATE. Readings (metrics with an explicit
# stat+window) are handled separately; the shim never has a stat/window, so measured metrics
# fall through to STATE, matching "when in doubt → State".
_DESCRIPTOR_PREDICATES: frozenset[str] = frozenset({
    # identity-adjacent / timeless facts about the entity
    "repo", "owner", "language", "node_name", "image", "table_count", "index_health",
    # content payloads (must stay renderable to the LLM — never demoted to evidence[])
    "diff_summary", "blame_line", "blame", "status_code_dist",
    "files_changed", "lines_added", "lines_deleted", "last_duration", "last_seen",
})


def species_for_predicate(predicate: str, *, has_reading_shape: bool = False) -> Species:
    """The boundary test: EVENT is decided by the op kind (AddEvent), never here. A fact with an
    explicit reading shape (stat+window) is a READING; a content/identity-adjacent predicate is a
    DESCRIPTOR; otherwise STATE (the cheap default)."""
    if has_reading_shape:
        return Species.READING
    if predicate in _DESCRIPTOR_PREDICATES:
        return Species.DESCRIPTOR
    return Species.STATE


def assertion_from_fact(op: AddFact) -> AddAssertion:
    """AddFact → AddAssertion. Belief stays unresolved (confidence_level / source_reliability
    pass straight through — the reducer resolves + applies the INV-9 default). `valid_from` is
    carried on every species (today's facts always have one; the descriptor-with-no-window
    nicety is a P1b native-authoring concern, not a shim constraint)."""
    species = species_for_predicate(op.predicate)
    return AddAssertion(
        subject=op.subject, name=op.predicate, value=op.value, unit=op.unit,
        species=species, valid_from=op.valid_from, valid_to=op.valid_to,
        observed_at=op.observed_at, source=op.source,
        confidence_level=op.confidence_level, source_reliability=op.source_reliability,
        evidence=op.evidence)


def assertion_from_event(op: AddEvent) -> AddAssertion:
    """AddEvent → AddAssertion (species EVENT). The event's `type` becomes the assertion `name`
    and its `payload` the `value` (round-trips back to a payload dict in the events view). Belief
    is left unresolved — the reducer applies the INV-9 per-source reliability default so a
    shim-minted event gains the envelope §2.2 promises, without any planner/adapter change."""
    return AddAssertion(
        subject=op.entity, name=op.type, value=op.payload,
        species=Species.EVENT, occurred_at=op.occurred_at, observed_at=op.observed_at,
        source=op.source, source_reliability=None,
        evidence=[])


# ── P6 store-flip: RECORD-level converters (Fact/Event ⇄ Assertion) ───────────
# The graph stores ONE assertion collection; these four are its exact-inverse read/write seams.
# Every field round-trips byte-identically (proven by the golden suite + unit tests): the
# species on a converted Fact is re-derived by the same §9.1 boundary test the op shim uses —
# deterministic, and never surfaced in any rendered view, so a reclassification cannot move a
# byte in the bundle. The channel is derived from the source exactly as the op path does
# (LLM → inferred, engine → engine, observing tools/humans → measured) — never DECLARED, so
# every converted Fact stays in the facts view (DECLARED is the node-prop channel, P6 step 2).

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
