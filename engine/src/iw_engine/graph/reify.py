"""Rung-2 REIFY + the ABANDONED reaper (2026-07-23 primitives §4.6/§4.7 — the SpanFold phase).

The promotion ladder's top rung: a SPAN datum PROMOTES to a reified occurrence NODE when it earns
identity (§4.2 — arity>2, own attributes/children, or structural referability), gated by the
stable-id precondition (§4.3). These functions are the ENGINE MECHANISM the fold invokes — never
the LLM (the parser rejects a planner-authored `reify`): the fold applies the reification tests and
calls `reify_span`, which mints the node + its SPAN self-assertion + the participants' PARTICIPATED_IN
edges (each edge's [valid_from,valid_to) carrying that participant's INVOLVEMENT sub-interval, §5.2 F).
The occurrence's OWN total-extent/outcome lives on the reified node's self-span, never on an edge.

`reap_abandoned_spans` is the ABANDONED reaper (§4.6): a DETERMINISTIC, journaled decision — given a
REPLAY CLOCK (never a wall-clock read on the query path) + per-span-name TTLs, it marks OPEN spans
whose close was lost. The returned assertions ride the PhaseResult delta, so replay reaps the same
spans at the same seq; a later real close overwrites ABANDONED -> CLOSED by span_id (bitemporal
honesty). Pure + order-independent throughout — every result is explicitly sorted.

Deferred (recorded, not guessed): the AUTOMATIC in-fold reification TRIGGER (wiring `should_reify`
into the live fold so a qualifying span self-promotes) and the assertion-id citation re-home (a
hypothesis citing the pre-reification span-id resolving to the reified node — the node-oriented
`graph.remap_id` re-homes NODE references, but a span citation is an AssertionId in the hypothesis
store, beyond that subsystem). Both are named build-deltas for the typed-schema/next step; the
mechanism + the reaper land here, un-wired, so no scenario's confirmed root or outcome moves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain import registry
from ..domain.assertion import Assertion
from ..domain.edge import Edge
from ..domain.enums import EdgeType, FactState, NodeType, Origin, SpanPhase, Species
from ..domain.node import Node


@dataclass(frozen=True)
class Participant:
    """One participant in a reified occurrence + its INVOLVEMENT sub-interval (§5.2 F). `node_type`
    rides here so `reify_span` can validate the PARTICIPATED_IN pair without touching the graph
    (the function stays pure). `involved_to=None` = still involved."""

    node_id: str
    node_type: NodeType
    involved_from: datetime
    involved_to: datetime | None = None


@dataclass
class ReifyResult:
    """The delta artifacts a reification emits — folded like any other delta (node -> nodes_touched,
    self_span -> spans_added, participations -> edges_added), so journal replay reproduces it."""

    node: Node
    self_span: Assertion
    participations: list[Edge] = field(default_factory=list)


def should_reify(span: Assertion, *, arity: int = 1, referenced: bool = False,
                 has_own_children: bool = False) -> bool:
    """The Rung-2 promotion test (§4.2), gated by the §4.3 stable-id PRECONDITION. Promote a SPAN
    datum to a reified node when ANY test holds:
      (1) arity > 2 participants;
      (2) it needs its OWN attributes/metrics/sub-relationships (`has_own_children`);
      (3) other data must structurally REFER to it (`referenced` — an edge points at it, or it is a
          hypothesis root_candidate). This is STRUCTURAL referability, explicitly NOT a mere
          evidential citation (§6): a cited reading does not reify.
    PRECONDITION (§4.3): a stable `correlation_id` — you cannot point at, hang children off, or
    attach >2 participants to an id-less span, so no correlation_id => the span stays a Rung-0/1
    datum, NEVER a node. A non-span never reifies here."""
    if span.species is not Species.SPAN or not span.correlation_id:
        return False
    return arity > 2 or has_own_children or referenced


def reify_span(span: Assertion, target_type: NodeType, node_props: dict, *,
               participants: tuple[Participant, ...] | list[Participant] = (), seq: int,
               edge_reliability: float | None = None) -> ReifyResult:
    """ENGINE-invoked promotion (§4.7): mint a reified occurrence NODE from a span datum. Sets the
    node's SPAN self-assertion to the datum's extent (same started_at/ended_at/span_phase/outcome/
    correlation_id — the correlation_id joins the Rung-1 hops nesting inside it, §4.4), and attaches
    each participant via a PARTICIPATED_IN edge whose [valid_from,valid_to) IS that participant's
    involvement sub-interval. The reified node is keyed by `target_type`'s identity (`node_props`).

    Pure — returns the delta artifacts for the fold to journal; raises ValueError on a non-span, a
    missing §4.3 correlation_id, or an illegal PARTICIPATED_IN pair (the engine mints only legal
    edges). `edge_reliability` (the engine passes `tunables.discovered_edge_reliability`) is the
    observed-channel belief on the participation edges; None leaves them belief-free (valid per the
    edge's never-both-additive rule)."""
    if span.species is not Species.SPAN:
        raise ValueError("reify_span: not a SPAN datum")
    if not span.correlation_id:
        raise ValueError("reify_span: a span with no correlation_id cannot reify (§4.3 precondition)")
    node_id = registry.node_id(target_type, node_props)
    node = Node(id=node_id, type=target_type, props=dict(node_props),
                source=span.source, created_by=seq)
    self_span = span.model_copy(update={
        "id": registry.span_id(node_id, span.name, span.valid_from),
        "subject_ref": node_id, "created_by": seq})
    participations: list[Edge] = []
    for p in participants:
        if not registry.edge_allowed(EdgeType.PARTICIPATED_IN, p.node_type, target_type):
            raise ValueError(
                f"reify_span: illegal PARTICIPATED_IN {p.node_type.value}->{target_type.value}")
        eid = registry.edge_id(EdgeType.PARTICIPATED_IN, p.node_id, node_id, Origin.DISCOVERED)
        participations.append(Edge(
            id=eid, type=EdgeType.PARTICIPATED_IN, src=p.node_id, dst=node_id,
            origin=Origin.DISCOVERED, source=span.source,
            source_reliability=edge_reliability,
            valid_from=p.involved_from, valid_to=p.involved_to,
            observed_at=span.observed_at, created_by=seq))
    return ReifyResult(node=node, self_span=self_span, participations=participations)


def reap_abandoned_spans(spans: list[Assertion] | tuple[Assertion, ...], now: datetime,
                         ttls: dict[str, timedelta], *,
                         default_ttl: timedelta | None = None) -> list[Assertion]:
    """The ABANDONED reaper (§4.6) — DETERMINISTIC + journaled, never a wall-clock read. Given a
    REPLAY CLOCK `now` and per-span-name TTLs, return an ABANDONED copy of every ACTIVE + OPEN span
    whose (now - started_at) exceeds its TTL (a close was lost). The copy keeps `valid_to=None`
    (still no close) — `valid_to=None + ABANDONED` = 'close lost', the one distinction OPEN cannot
    express (§4.6). Each shares its source span's id, so the fold's add_span overwrites OPEN ->
    ABANDONED in place; a later real close overwrites ABANDONED -> CLOSED. A span whose name has no
    TTL (and no `default_ttl`) is left OPEN. Sorted by id — order-independent, replay-stable."""
    out: list[Assertion] = []
    for s in spans:
        if (s.species is not Species.SPAN or s.span_phase is not SpanPhase.OPEN
                or s.state is not FactState.ACTIVE or s.valid_from is None):
            continue
        ttl = ttls.get(s.name, default_ttl)
        if ttl is None or now - s.valid_from <= ttl:
            continue
        out.append(s.model_copy(update={"span_phase": SpanPhase.ABANDONED}))
    return sorted(out, key=lambda a: a.id)
