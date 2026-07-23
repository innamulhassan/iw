"""Governed graph-traversal + focus-slice tools — the P7 planner's reasoning substrate.

The owner's P7 directive (EXECUTION-LOG 2026-07-22): *projections drive reasoning, not
fold-and-forget* — the planner must be able to EXPAND its investigation over the graph
projection instead of receiving one flat capped dump. This module reintroduces the v2 B9
governed tool surface the audits found designed-then-dropped (domain-audit
`3-design-lineage-gaps.md` §2.2: `neighbours/walk/blast_radius/path` gone; the bounded
B9.3 render-slice replaced by "full graph capped at 40"):

- `neighbours` / `walk` / `path`   — bounded structural traversal (the planner's "look around");
- `blast_radius`                   — direction-aware impact closure over the dependency spine;
- `focus_slice`                    — the bounded reasoning view (v2 03-design B9.3): cause path +
  suspects + frontier IN FULL, healthy/ruled-out collapsed to a count, ~budget nodes regardless
  of graph size, with the invariant `full + frontier + collapsed == total`.

The SCHEMA-USABILITY query grammar (2026-07-23 primitives §7/§8.2 — the LLM's "reach for more"):

- `state_as_of(node, name, T)`     — the STATE tile whose `[valid_from,valid_to)` contains T
  (what was its version/image/severity AT onset — the bitemporal payoff);
- `metric_summary(subject, name, window)` — a summary READING over a window (node OR edge subject),
  as if resolved through the `metric_query` handle (the engine stores samples, never the raw curve);
- `spans_of(node, [phase], [window])` — the SPAN datums a node participates in + its
  `PARTICIPATED_IN` edges into reified occurrences; **span_phase ALWAYS exposed** (§4.6), overlap
  filter answers the core RCA join ("does a change/outage SPAN contain onset?");
- `trail_of(subject, [name])`      — the STATE supersede-chain + the change-index (events, spans,
  CHANGE_EVENTs, AND STATE version-boundaries — so a silent bump is never missed, §9 fix J).

Discipline (uniform):

- **Pure, read-only, deterministic.** Functions only read the Graph's public surface
  (`nodes`/`edges`/`node()`/`facts_of()`/`structural_distances()`/`id_remaps`); nothing is
  mutated, and every list in every view is explicitly sorted, so output is byte-stable under
  journal replay and independent of fold insertion order.
- **Governed spine** (mirrors `Graph.structural_distances`, P4): traversal crosses only ACTIVE
  edges whose origin is NOT `INFERRED` (declared/discovered structure — never the causal layer),
  and never enters HYPOTHESIS nodes. The inferred layer is read exclusively by `focus_slice`,
  which renders it rather than walking it.
- **P5 identity**: node-id arguments are resolved through `graph.id_remaps` first — a graduated
  (merged/retyped) id stays addressable forever. An id that still resolves to no node raises
  `KeyError` (a typo must read as an error, never as "no neighbours"); `focus_slice` alone
  degrades to a focus-less view, because the render surface must work before the symptom node
  exists.
- **INV-9**: every tunable (budget, hop caps, node caps, edge-type sets, facts-per-node) is a
  PARAMETER. Module-level sets below are overridable defaults derived from the domain catalog,
  not engine constants.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Literal

from ..domain.edge import Edge
from ..domain.edges import STRUCTURAL_EDGE_TYPES
from ..domain.enums import EdgeType, FactState, NodeType, Origin, SpanPhase
from ..domain.fact import Fact
from .graph import Graph

Direction = Literal["out", "in", "both"]
_DIRECTIONS = ("out", "in", "both")

# ── default edge-type sets (overridable per call — INV-9) ─────────────────────────────
# Impact flow over the structural spine is direction-aware, per structural.py's own
# convention ("dependency edges point dependent -> provider"):
#   - dependency edges: src depends on dst  → impact climbs dst → src (provider fails,
#     dependents break): DEPENDS_ON / CALLS / RUNS_ON / HOSTED_ON / READS_FROM / ...
#   - containment edges: src carries dst    → impact descends src → dst (the container
#     fails, its contents/interfaces go with it): CONTAINS (parent→child),
#     EXPOSES (provider→interface).
# Derived from the catalog's STRUCTURAL_EDGE_TYPES so a new structural type defaults into
# the dependent→provider reading instead of silently vanishing from impact analysis.
CONTAINMENT_EDGE_TYPES: frozenset[EdgeType] = frozenset({EdgeType.CONTAINS, EdgeType.EXPOSES})
DEPENDENCY_EDGE_TYPES: frozenset[EdgeType] = STRUCTURAL_EDGE_TYPES - CONTAINMENT_EDGE_TYPES


# ── shared plumbing ───────────────────────────────────────────────────────────────────
def _resolve(graph: Graph, ref: str) -> str:
    """Follow the P5 remap table (chain-compressed — one hop suffices)."""
    return graph.id_remaps.get(ref, ref)


def _require_node(graph: Graph, ref: str, arg: str) -> str:
    if not isinstance(ref, str) or not ref:
        raise KeyError(f"{arg}: empty node id")
    nid = _resolve(graph, ref)
    if graph.node(nid) is None:
        raise KeyError(f"{arg}: unknown node {ref!r}" + (f" (resolved to {nid!r})" if nid != ref else ""))
    return nid


def _require_subject(graph: Graph, ref: str, arg: str = "subject") -> str:
    """Resolve a subject that may be a NODE or an EDGE — span/reading subjects reach both (§4.1:
    subject_ref is EntityId|EdgeId; edge-borne RED rides a discovered CALLS edge). Remap-resolve,
    then require the id exists as a node OR an edge, so a typo reads as an error, never as an empty
    result (the same discipline `_require_node` enforces)."""
    if not isinstance(ref, str) or not ref:
        raise KeyError(f"{arg}: empty id")
    rid = _resolve(graph, ref)
    if graph.node(rid) is None and rid not in graph.edges:
        raise KeyError(f"{arg}: unknown node/edge {ref!r}"
                       + (f" (resolved to {rid!r})" if rid != ref else ""))
    return rid


def _overlaps(s_from: datetime | None, s_to: datetime | None,
              w_start: datetime | None, w_end: datetime | None) -> bool:
    """Whether a span interval `[s_from, s_to)` overlaps the query window `[w_start, w_end)` — an
    open span end (`s_to=None`, in-flight/abandoned) extends to +inf; an open window bound is
    unbounded on that side. The interval-overlap that answers the core RCA containment join."""
    if w_start is not None and s_to is not None and s_to <= w_start:
        return False
    if w_end is not None and s_from is not None and s_from >= w_end:
        return False
    return True


def _check_direction(direction: str) -> None:
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be one of {_DIRECTIONS}, got {direction!r}")


def _structural_edges(graph: Graph, edge_types: Iterable[EdgeType] | None = None) -> list[Edge]:
    """The governed traversal spine: ACTIVE, non-INFERRED, both endpoints present, and never
    a HYPOTHESIS endpoint (identical discipline to Graph.structural_distances, P4)."""
    wanted = None if edge_types is None else frozenset(edge_types)
    out: list[Edge] = []
    for e in graph.edges.values():
        if e.state != FactState.ACTIVE or e.origin == Origin.INFERRED:
            continue
        if wanted is not None and e.type not in wanted:
            continue
        s, d = graph.node(e.src), graph.node(e.dst)
        if s is None or d is None or NodeType.HYPOTHESIS in (s.type, d.type):
            continue
        out.append(e)
    return out


def _adjacency(edges: Iterable[Edge], direction: Direction) -> dict[str, list[tuple[str, Edge]]]:
    """nid → [(neighbour, edge)], each list id-sorted so BFS expansion (and therefore any
    tie-break between equal-hop discoveries) is canonical, not insertion-dependent."""
    adj: dict[str, list[tuple[str, Edge]]] = {}
    for e in edges:
        if direction in ("out", "both"):
            adj.setdefault(e.src, []).append((e.dst, e))
        if direction in ("in", "both"):
            adj.setdefault(e.dst, []).append((e.src, e))
    for lst in adj.values():
        lst.sort(key=lambda t: (t[0], t[1].id))
    return adj


def _edge_view(e: Edge) -> dict:
    return {"type": e.type.value, "src": e.src, "dst": e.dst, "origin": e.origin.value}


def _edge_sort_key(v: dict) -> tuple:
    return (v["src"], v["dst"], v["type"], v["origin"])


# ── 1. neighbours ─────────────────────────────────────────────────────────────────────
def neighbours(graph: Graph, node_id: str, edge_types: Iterable[EdgeType] | None = None,
               direction: Direction = "both") -> dict:
    """Adjacent nodes over the governed structural spine — one entry per edge (a MultiDiGraph
    can hold e.g. a declared DEPENDS_ON and a discovered CALLS between the same pair, and both
    are information). `direction="out"` follows edges where the node is `src`; `"in"` where it
    is `dst`; `"both"` both. Raises KeyError for an unknown node, ValueError for a bad direction.
    """
    _check_direction(direction)
    nid = _require_node(graph, node_id, "node_id")
    entries: list[dict] = []
    for e in _structural_edges(graph, edge_types):
        if e.src == nid and direction in ("out", "both"):
            other, direc = e.dst, "out"
        elif e.dst == nid and direction in ("in", "both"):
            other, direc = e.src, "in"
        else:
            continue
        entries.append({"id": other, "node_type": graph.node(other).type.value,
                        "edge_type": e.type.value, "direction": direc, "origin": e.origin.value})
    entries.sort(key=lambda v: (v["id"], v["edge_type"], v["direction"]))
    return {"node": nid, "neighbours": entries, "count": len(entries)}


# ── 2. walk ───────────────────────────────────────────────────────────────────────────
def walk(graph: Graph, start: str, max_hops: int, edge_types: Iterable[EdgeType] | None = None,
         *, direction: Direction = "both", max_nodes: int | None = None) -> dict:
    """Bounded breadth-first walk from `start` over the governed structural spine: the
    reachable subgraph within `max_hops`, as node views (with hop distance) plus the induced
    governed edge set among the visited nodes. Level-order with id-sorted expansion — hop
    counts and any `max_nodes` truncation are deterministic. `truncated=True` when `max_nodes`
    refused admission to a reachable node."""
    _check_direction(direction)
    if max_hops < 0:
        raise ValueError(f"max_hops must be >= 0, got {max_hops}")
    if max_nodes is not None and max_nodes < 1:
        raise ValueError(f"max_nodes must be >= 1, got {max_nodes}")
    sid = _require_node(graph, start, "start")
    spine = _structural_edges(graph, edge_types)
    adj = _adjacency(spine, direction)

    hops: dict[str, int] = {sid: 0}
    frontier, depth, truncated = [sid], 0, False
    while frontier and depth < max_hops:
        depth += 1
        nxt: list[str] = []
        for n in sorted(frontier):
            for m, _e in adj.get(n, ()):
                if m in hops:
                    continue
                if max_nodes is not None and len(hops) >= max_nodes:
                    truncated = True
                    continue
                hops[m] = depth
                nxt.append(m)
        frontier = nxt

    nodes = [{"id": nid, "type": graph.node(nid).type.value, "hops": h}
             for nid, h in sorted(hops.items(), key=lambda kv: (kv[1], kv[0]))]
    edges = sorted((_edge_view(e) for e in spine if e.src in hops and e.dst in hops),
                   key=_edge_sort_key)
    return {"start": sid, "max_hops": max_hops, "nodes": nodes, "edges": edges,
            "count": len(nodes), "truncated": truncated}


# ── 3. blast_radius ───────────────────────────────────────────────────────────────────
def blast_radius(graph: Graph, node_id: str, *,
                 dependency_edges: Iterable[EdgeType] | None = None,
                 containment_edges: Iterable[EdgeType] | None = None,
                 max_hops: int | None = None) -> dict:
    """The set of nodes structurally dependent on `node_id` — who breaks if it fails.
    Direction-aware over the dependency spine: impact climbs dependency edges dst→src
    (src depends on dst: DEPENDS_ON / RUNS_ON / CALLS / ...) and descends containment
    edges src→dst (CONTAINS / EXPOSES); it never rides an edge the wrong way, so a node's
    own providers are NOT in its blast radius. Both edge-type sets and the hop cap are
    parameters (INV-9); defaults are the catalog-derived sets above."""
    if max_hops is not None and max_hops < 0:
        raise ValueError(f"max_hops must be >= 0, got {max_hops}")
    nid = _require_node(graph, node_id, "node_id")
    dep = DEPENDENCY_EDGE_TYPES if dependency_edges is None else frozenset(dependency_edges)
    con = CONTAINMENT_EDGE_TYPES if containment_edges is None else frozenset(containment_edges)

    impact: dict[str, list[tuple[str, Edge]]] = {}   # failed node → nodes it takes down
    for e in _structural_edges(graph, dep | con):
        if e.type in dep:
            impact.setdefault(e.dst, []).append((e.src, e))   # provider fails → dependent breaks
        if e.type in con:
            impact.setdefault(e.src, []).append((e.dst, e))   # container fails → contents break
    for lst in impact.values():
        lst.sort(key=lambda t: (t[0], t[1].id))

    hops: dict[str, int] = {nid: 0}
    frontier, depth = [nid], 0
    while frontier and (max_hops is None or depth < max_hops):
        depth += 1
        nxt: list[str] = []
        for n in sorted(frontier):
            for m, _e in impact.get(n, ()):
                if m not in hops:
                    hops[m] = depth
                    nxt.append(m)
        frontier = nxt

    impacted = [{"id": m, "type": graph.node(m).type.value, "hops": h}
                for m, h in sorted(hops.items(), key=lambda kv: (kv[1], kv[0])) if m != nid]
    return {"node": nid, "impacted": impacted, "count": len(impacted)}


# ── 4. path ───────────────────────────────────────────────────────────────────────────
def path(graph: Graph, src: str, dst: str, *, edge_types: Iterable[EdgeType] | None = None,
         direction: Direction = "both", max_hops: int | None = None) -> dict:
    """A shortest structural path src → dst over the governed spine, if one exists within
    `max_hops`. Each hop reports the edge and whether it was ridden forward (`src→dst`) or
    against its direction (only possible with direction="both"). BFS with id-sorted expansion:
    among equal-length paths the id-ordered one is returned — canonical and replay-stable.
    `found=False` (never an exception) when no path exists; unknown endpoints raise KeyError."""
    _check_direction(direction)
    if max_hops is not None and max_hops < 0:
        raise ValueError(f"max_hops must be >= 0, got {max_hops}")
    s = _require_node(graph, src, "src")
    d = _require_node(graph, dst, "dst")
    if s == d:
        return {"src": s, "dst": d, "found": True, "hops": 0, "nodes": [s], "edges": []}

    adj = _adjacency(_structural_edges(graph, edge_types), direction)
    parent: dict[str, tuple[str, Edge]] = {}
    seen, frontier, depth = {s}, [s], 0
    while frontier and d not in parent and (max_hops is None or depth < max_hops):
        depth += 1
        nxt: list[str] = []
        for n in sorted(frontier):
            for m, e in adj.get(n, ()):
                if m in seen:
                    continue
                seen.add(m)
                parent[m] = (n, e)
                nxt.append(m)
        frontier = nxt

    if d not in parent:
        return {"src": s, "dst": d, "found": False, "hops": None, "nodes": [], "edges": []}
    hop_edges: list[dict] = []
    nodes = [d]
    cur = d
    while cur != s:
        prev, e = parent[cur]
        hop_edges.append({**_edge_view(e), "forward": e.src == prev})
        nodes.append(prev)
        cur = prev
    nodes.reverse()
    hop_edges.reverse()
    return {"src": s, "dst": d, "found": True, "hops": len(hop_edges),
            "nodes": nodes, "edges": hop_edges}


# ── 5. focus_slice ────────────────────────────────────────────────────────────────────
def _latest_facts(graph: Graph, nid: str, cap: int) -> list[dict]:
    """Active facts, ONE per predicate (the latest by valid window, then observation time) —
    the fix for the audit's flooding finding (a 6-point reading series evicting every other
    predicate from the planner's view). Predicate groups are ordered freshest-first so a cap
    keeps the newest signals; ties break on predicate name."""
    best: dict[str, Fact] = {}
    for f in graph.facts_of(nid):
        cur = best.get(f.predicate)
        key = (f.valid_from, f.observed_at, f.id)
        if cur is None or key > (cur.valid_from, cur.observed_at, cur.id):
            best[f.predicate] = f
    chosen = sorted(best.values(), key=lambda f: f.predicate)                   # tie order
    chosen.sort(key=lambda f: (f.valid_from, f.observed_at), reverse=True)      # stable: freshest first
    return [{"predicate": f.predicate, "value": f.value, "unit": f.unit,
             "valid_from": f.valid_from.isoformat(), "source": f.source.value}
            for f in chosen[:cap]]


def _causal_closure(graph: Graph, focus: str, causal: list[Edge]) -> list[tuple[int, str]]:
    """Nodes on the ACTIVE cause path of `focus`: BFS over ACTIVE inferred CAUSED_BY edges in
    both directions (effect→cause claims about the focus, and the hypotheses making them),
    level-ordered, excluding the focus itself. Returns [(hop, id)] in (hop, id) order."""
    adj: dict[str, list[str]] = {}
    for e in causal:
        adj.setdefault(e.src, []).append(e.dst)
        adj.setdefault(e.dst, []).append(e.src)
    for lst in adj.values():
        lst.sort()
    hops = {focus: 0}
    frontier, depth = [focus], 0
    while frontier:
        depth += 1
        nxt: list[str] = []
        for n in sorted(frontier):
            for m in adj.get(n, ()):
                if m not in hops:
                    hops[m] = depth
                    nxt.append(m)
        frontier = nxt
    return sorted(((h, n) for n, h in hops.items() if n != focus))


def focus_slice(graph: Graph, anomaly_ref: str | None, budget: int, *,
                max_facts_per_node: int = 6, frontier_hops: int = 1) -> dict:
    """THE bounded reasoning view (v2 03-design B9.3, designed-then-dropped — reinstated for
    the P7 reasoning loop). Never a flat capped dump: the slice is tiered by investigative
    relevance and holds ~`budget` rendered nodes REGARDLESS of graph size.

    - **full tier** (`nodes`, complete cards with latest-per-predicate facts):
        `focus`       — the anomaly/symptom node (never dropped while it exists);
        `cause_path`  — nodes linked to the focus through ACTIVE inferred CAUSED_BY edges
                        (the believed causal chain, hypotheses included), nearest first;
        `suspect`     — every other endpoint of the ACTIVE inferred layer (CAUSED_BY claims,
                        SUPPORTS/REFUTES evidence subjects, live hypothesis nodes), ordered by
                        structural distance to the focus;
    - **frontier** — nodes within `frontier_hops` governed structural hops of the full tier:
        the expansion surface ("what would I look at next"), compact cards with their anchor;
    - **collapsed** — everything else (healthy / not implicated / ruled out) as a COUNT plus a
        per-type histogram, never node-by-node;
    - **ruled_out** — RETRACTED inferred edges (refuted causal claims), so the planner sees
        what was already eliminated and does not re-suggest it.

    Invariant (asserted by tests): `len(nodes) + len(frontier) + collapsed_count == total`.
    Budget pressure drops frontier before suspects before cause-path — never the focus — and
    anything dropped is counted in `collapsed_count`, so the invariant survives truncation.
    A None/unknown `anomaly_ref` degrades to a focus-less slice (the view must render before
    the symptom node exists); an empty graph yields the empty slice. All knobs are parameters
    (INV-9)."""
    if budget < 1:
        raise ValueError(f"budget must be >= 1, got {budget}")
    if frontier_hops < 0:
        raise ValueError(f"frontier_hops must be >= 0, got {frontier_hops}")
    if max_facts_per_node < 0:
        raise ValueError(f"max_facts_per_node must be >= 0, got {max_facts_per_node}")

    focus: str | None = None
    if anomaly_ref:
        rid = _resolve(graph, anomaly_ref)
        if graph.node(rid) is not None:
            focus = rid

    inferred = [e for e in graph.edges.values()
                if e.state == FactState.ACTIVE and e.origin == Origin.INFERRED
                and graph.node(e.src) is not None and graph.node(e.dst) is not None]
    causal = [e for e in inferred if e.type == EdgeType.CAUSED_BY]

    # tier assembly (each node lands in exactly one tier; higher tier wins)
    tier: dict[str, str] = {}
    ordered_full: list[str] = []
    if focus is not None:
        tier[focus] = "focus"
        ordered_full.append(focus)
    if focus is not None:
        for _h, n in _causal_closure(graph, focus, causal):
            if n not in tier:
                tier[n] = "cause_path"
                ordered_full.append(n)
    dist: Mapping[str, int] = graph.structural_distances(focus) if focus is not None else {}
    suspect_ids = {x for e in inferred for x in (e.src, e.dst)} - tier.keys()
    # BOOTSTRAP (live retest 2026-07-22): before any CAUSAL claim is anchored the cause-path
    # tier is empty and the symptom node is a structural ISLAND (the planner may not author
    # edges, and no tool links the model-authored Anomaly), so the slice rendered the bare
    # focus — plus at most the endpoints of stray non-causal inferred edges (a recurrence
    # prior's SIMILAR_TO/RECURRENCE_OF) — and collapsed the entire seeded topology. A live
    # planner whose root_candidate must be an id COPIED from the slice then deadlocks: it can
    # never propose, so the causal layer never appears (the change-correlation hint masked
    # this in change-ful scenarios; every no-change scenario starved). Pre-anchor, the whole
    # seeded topology IS the suspect set — budget still bounds what renders; the first
    # hypothesis narrows the view back to the believed paths.
    if not causal:
        suspect_ids = (suspect_ids | set(graph.nodes)) - tier.keys() - {
            nid for nid, n in graph.nodes.items() if n.type == NodeType.HYPOTHESIS}
    for n in sorted(suspect_ids, key=lambda n: (dist.get(n, len(graph.nodes) + 1), n)):
        tier[n] = "suspect"
        ordered_full.append(n)

    # frontier: multi-source BFS over the governed structural spine from the full tier
    adj = _adjacency(_structural_edges(graph), "both")
    fdist: dict[str, int] = dict.fromkeys(ordered_full, 0)
    frontier_order: list[str] = []
    ring, depth = list(ordered_full), 0
    while ring and depth < frontier_hops:
        depth += 1
        nxt: list[str] = []
        for n in sorted(ring):
            for m, _e in adj.get(n, ()):
                if m not in fdist:
                    fdist[m] = depth
                    nxt.append(m)
        ring = nxt
        frontier_order.extend(sorted(nxt))

    # budget: full tier first (already priority-ordered), remainder to the frontier
    kept_full = ordered_full[:budget]
    kept_frontier = frontier_order[:max(budget - len(kept_full), 0)]
    truncated = len(kept_full) < len(ordered_full) or len(kept_frontier) < len(frontier_order)

    rendered = set(kept_full) | set(kept_frontier)
    nodes = []
    for nid in kept_full:
        n = graph.node(nid)
        nodes.append({"id": nid, "type": n.type.value, "tier": tier[nid], "props": n.props,
                      "facts": _latest_facts(graph, nid, max_facts_per_node)})
    frontier_nodes = []
    for nid in kept_frontier:
        n = graph.node(nid)
        anchors = sorted({m for m, _e in adj.get(nid, ()) if m in rendered and fdist.get(m, -1) == fdist[nid] - 1})
        frontier_nodes.append({"id": nid, "type": n.type.value, "hops": fdist[nid], "attached_to": anchors})

    edges = sorted((_edge_view(e) for e in graph.edges.values()
                    if e.state == FactState.ACTIVE and e.src in rendered and e.dst in rendered),
                   key=_edge_sort_key)
    ruled_out = sorted(({"type": e.type.value, "src": e.src, "dst": e.dst,
                         "invalidated_by": e.invalidated_by}
                        for e in graph.edges.values()
                        if e.state == FactState.RETRACTED and e.origin == Origin.INFERRED),
                       key=lambda v: (v["src"], v["dst"], v["type"]))[:budget]

    collapsed = [n for nid, n in graph.nodes.items() if nid not in rendered]
    by_type: dict[str, int] = {}
    for n in collapsed:
        by_type[n.type.value] = by_type.get(n.type.value, 0) + 1
    return {
        "focus": focus,
        "budget": budget,
        "total": len(graph.nodes),
        "nodes": nodes,
        "frontier": frontier_nodes,
        "edges": edges,
        "ruled_out": ruled_out,
        "collapsed_count": len(collapsed),
        "collapsed_types": dict(sorted(by_type.items())),
        "truncated": truncated,
    }


# ── 6. state_as_of — the STATE tile active at an instant (per node + name) ───────────────
def state_as_of(graph: Graph, node_id: str, name: str, at: datetime) -> dict:
    """The value held by `node_id`'s `name` attribute AT instant `at` — the tile whose
    `[valid_from, valid_to)` contains `at` (2026-07-23 primitives §7; `graph.facts_valid_at` is the
    whole-graph form). This is the load-bearing bitemporal payoff: "what version/image/severity was
    it AT onset?" returns the value that was true THEN (1.4.2-at-onset), not the current one
    (1.4.3-now) a mutable-property model would misattribute. SUPERSEDED tiles are honoured — a
    superseded value was still the truth of its now-closed window; only RETRACTED tiles (disavowed
    as wrong) are excluded. On a bitemporal overlap the latest-known tile wins (`observed_at` then
    `valid_from`). `found=False` when no tile covers `at`; an unknown node raises KeyError."""
    nid = _require_node(graph, node_id, "node_id")
    best: Fact | None = None

    def _key(f: Fact) -> tuple:
        return (f.observed_at is not None, f.observed_at or f.valid_from, f.valid_from, f.id)

    for f in graph.facts_of(nid, active_only=False):
        if f.predicate != name or f.state == FactState.RETRACTED:
            continue
        if f.valid_from <= at and (f.valid_to is None or at < f.valid_to):
            if best is None or _key(f) > _key(best):
                best = f
    if best is None:
        return {"node": nid, "name": name, "as_of": at.isoformat(), "found": False}
    return {"node": nid, "name": name, "as_of": at.isoformat(), "found": True,
            "value": best.value, "unit": best.unit,
            "valid_from": best.valid_from.isoformat(),
            "valid_to": best.valid_to.isoformat() if best.valid_to is not None else None,
            "source": best.source.value, "state": best.state.value}


# ── 7. metric_summary — a summary READING over a window (node OR edge subject) ───────────
def metric_summary(graph: Graph, subject: str, name: str, *,
                   start: datetime | None = None, end: datetime | None = None) -> dict:
    """A materialized summary READING for `subject`'s `name` metric over `[start, end)` (2026-07-23
    primitives §7/§8.2 — resolved as if through the `metric_query` handle: the engine stores
    judgment-granularity samples, never the raw curve, so this summary is the addressable object).
    `subject` may be a NodeId OR an EdgeId (edge-borne RED on a discovered CALLS/READS_FROM edge).
    Summarises the numeric samples — count / first / last / min / max / mean — deterministically
    (ordered by valid_from). A None bound is open on that side; a non-numeric sample is counted but
    excluded from min/max/mean. `count=0` when the window holds no sample; an unknown subject
    (neither a node nor an edge carrying facts) raises KeyError."""
    ref = _require_subject(graph, subject)
    samples: list[Fact] = []
    for f in graph.facts_of(ref, active_only=False):
        if f.predicate != name or f.state == FactState.RETRACTED:
            continue
        if start is not None and f.valid_from < start:
            continue
        if end is not None and f.valid_from >= end:
            continue
        samples.append(f)
    samples.sort(key=lambda f: (f.valid_from, f.id))
    nums = [f.value for f in samples
            if isinstance(f.value, int | float) and not isinstance(f.value, bool)]
    out: dict = {
        "subject": ref, "name": name,
        "window": {"start": start.isoformat() if start is not None else None,
                   "end": end.isoformat() if end is not None else None},
        "count": len(samples),
        "unit": samples[-1].unit if samples else None,
    }
    if samples:
        out["first"] = samples[0].value
        out["last"] = samples[-1].value
    if nums:
        out["min"] = min(nums)
        out["max"] = max(nums)
        out["mean"] = round(sum(nums) / len(nums), 4)
    return out


# ── 8. spans_of — the SPANs a node participates in, span_phase ALWAYS exposed ────────────
def spans_of(graph: Graph, node_id: str, *, phase: SpanPhase | str | None = None,
             start: datetime | None = None, end: datetime | None = None) -> dict:
    """The bounded happenings `node_id` participates in (2026-07-23 primitives §7 / §4.6): SPAN
    datums whose subject IS the node, PLUS `PARTICIPATED_IN` edges into reified occurrence nodes (a
    trace / incident / change the node participated in; the edge's `[valid_from,valid_to)` is its
    INVOLVEMENT sub-interval). **`span_phase` is ALWAYS exposed** — the §4.6 universality invariant:
    an ABANDONED span (close lost) must never read as 'ongoing at query time', or a dropped close
    would over-link as a live outage covering onset and break the RCA overlap join. Optional `phase`
    filters the span datums by span_phase; a `[start,end)` keeps only spans whose interval OVERLAPS
    the window — the core RCA join ("does a change-window / vendor-outage / rollout SPAN's interval
    CONTAIN the onset?"). Deterministic (spans by started_at, participations by occurrence id).
    Unknown node raises KeyError."""
    nid = _require_node(graph, node_id, "node_id")
    want = SpanPhase(phase) if isinstance(phase, str) else phase
    spans: list[dict] = []
    for s in graph.spans_of(nid):        # raw Assertions → span_phase guaranteed present (§4.6)
        if want is not None and s.span_phase is not want:
            continue
        if not _overlaps(s.valid_from, s.valid_to, start, end):
            continue
        spans.append({
            "id": s.id, "name": s.name, "subject": s.subject_ref,
            "span_phase": s.span_phase.value,
            "started_at": s.valid_from.isoformat() if s.valid_from is not None else None,
            "ended_at": s.valid_to.isoformat() if s.valid_to is not None else None,
            "correlation_id": s.correlation_id, "outcome": s.value, "source": s.source.value,
        })
    spans.sort(key=lambda v: (v["started_at"] or "", v["id"]))
    participations: list[dict] = []
    for e in graph.out_edges(nid, EdgeType.PARTICIPATED_IN):
        if e.state != FactState.ACTIVE:
            continue
        occ = graph.node(e.dst)
        participations.append({
            "occurrence": e.dst,
            "occurrence_type": occ.type.value if occ is not None else None,
            "involvement_from": e.valid_from.isoformat() if e.valid_from is not None else None,
            "involvement_to": e.valid_to.isoformat() if e.valid_to is not None else None,
        })
    participations.sort(key=lambda v: v["occurrence"])
    return {"node": nid, "spans": spans, "participations": participations,
            "count": len(spans) + len(participations)}


# ── 9. trail_of — the STATE supersede-chain + the change-index over a subject ────────────
def trail_of(graph: Graph, subject: str, name: str | None = None) -> dict:
    """The RCA-first change-trail of `subject` (2026-07-23 primitives §7/§8.2 — the §9 fix J
    change-index): the STATE supersede-chain (the held values over time, per predicate) PLUS a
    time-ordered change-index over the subject — its EVENTs, its SPANs, the CHANGE_EVENT nodes it
    was `CHANGED_BY`, AND **each STATE version-boundary** (a `valid_from` where a held value
    changed). Scanning STATE boundaries is the whole point: a silent version bump folded ONLY as a
    STATE tile (image 1.4.2→1.4.3 with no change ticket) is invisible to an event-only change index —
    here it surfaces as a `state` change entry, so change-first RCA never blind-spots it. `name`
    narrows the trail (and its boundaries) to one predicate. Deterministic (trail by
    (predicate, valid_from), index by (at, kind, ref)). Unknown node raises KeyError."""
    nid = _require_node(graph, subject, "subject")
    tiles = [f for f in graph.facts_of(nid, active_only=False)
             if f.state != FactState.RETRACTED and (name is None or f.predicate == name)]
    tiles.sort(key=lambda f: (f.predicate, f.valid_from, f.id))
    trail = [{"predicate": f.predicate, "value": f.value, "unit": f.unit,
              "valid_from": f.valid_from.isoformat(),
              "valid_to": f.valid_to.isoformat() if f.valid_to is not None else None,
              "state": f.state.value, "source": f.source.value} for f in tiles]

    changes: list[dict] = []
    for f in tiles:                                   # STATE version-boundaries (the §9 fix J core)
        changes.append({"kind": "state", "ref": f.id, "at": f.valid_from.isoformat(),
                        "detail": f"{f.predicate}={f.value}"})
    if name is None:
        for ev in graph.events_of(nid):
            if ev.state == FactState.ACTIVE:
                changes.append({"kind": "event", "ref": ev.id, "at": ev.occurred_at.isoformat(),
                                "detail": ev.type})
        for s in graph.spans_of(nid):
            changes.append({"kind": "span", "ref": s.id,
                            "at": s.valid_from.isoformat() if s.valid_from is not None else "",
                            "detail": f"{s.name} [{s.span_phase.value}]"})
        for e in graph.out_edges(nid, EdgeType.CHANGED_BY):
            if e.state == FactState.ACTIVE:
                changes.append({"kind": "change_event", "ref": e.dst,
                                "at": e.valid_from.isoformat() if e.valid_from is not None else "",
                                "detail": e.dst})
    changes.sort(key=lambda c: (c["at"], c["kind"], c["ref"]))
    return {"subject": nid, "name": name, "trail": trail, "changes": changes,
            "count": len(changes)}
