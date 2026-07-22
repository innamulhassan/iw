"""The AddFact/AddEvent → AddAssertion compatibility shim (P1a build-spec step 2) — maps the
legacy AddFact/AddEvent ops onto the AddAssertion atom so the reducer has one materialization
path.

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

from .enums import Species
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
