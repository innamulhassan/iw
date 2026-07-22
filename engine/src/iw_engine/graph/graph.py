"""The graph projection — an in-memory, bi-temporal, typed MultiDiGraph.

A projection of the PhaseResult stream (principle 2): nodes/edges/facts/events are
applied here by the fold; the graph never mutates itself from the outside. Idempotent
upsert by id (deterministic identity). Facts are never mutated — a superseding fact
closes the prior one's valid_to. MultiDiGraph so a structural DEPENDS_ON and an inferred
CAUSED_BY between the same pair coexist (distinct edge ids).
"""
from __future__ import annotations

from datetime import datetime

import networkx as nx

from ..domain.edge import Edge
from ..domain.enums import EdgeType, FactState, NodeType, Origin
from ..domain.event import Event
from ..domain.fact import Fact
from ..domain.node import Node
from ..domain.registry import edge_id as _edge_id
from . import resolver


class Graph:
    def __init__(self) -> None:
        self._g = nx.MultiDiGraph()               # traversal spine (node ids + edge keys)
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self.facts: dict[str, Fact] = {}
        self.events: dict[str, Event] = {}
        # P5 identity layer (DOMAIN-v3 §2.1, R-J5's alias table): "scheme:id" → node id.
        # Maintained here (upsert_node is fold-only) so journal replay rebuilds it exactly;
        # first binding wins per key — a later conflicting claim never silently rebinds
        # (§9.2: conflict = journaled contradiction, surfaced by the reducer).
        self.alias_index: dict[str, str] = {}
        # P5 step 4 (§9.2): old node id → canonical id, one entry per applied Remap record
        # (merge/retype/resolve). THE "old id becomes an alias" surface: a graduated id stays
        # resolvable forever, so write-once identity is never violated by a retype/merge.
        # Chain-compressed (values are always current), persisted with the graph, and only
        # ever written through remap_id (fold-applied, journaled records ⇒ replay-identical).
        self.id_remaps: dict[str, str] = {}

    # ── mutation (only the fold calls these) ──────────────────────────────────
    def upsert_node(self, node: Node) -> Node:
        prior = self.nodes.get(node.id)
        if prior is not None:
            merged = {**prior.props, **{k: v for k, v in node.props.items() if v is not None}}
            # aliases are identity surface: union, PRIOR wins per scheme (write-once flavor —
            # symmetric with the alias_index's first-binding-wins below).
            aliases = {**node.aliases, **prior.aliases}
            node = prior.model_copy(update={"props": merged, "aliases": aliases})
        self.nodes[node.id] = node
        self._g.add_node(node.id, type=node.type.value)
        for scheme, val in node.aliases.items():
            self.alias_index.setdefault(resolver.alias_key(scheme, val), node.id)
        return node

    def add_edge(self, edge: Edge) -> Edge:
        self.edges[edge.id] = edge                 # idempotent by edge id (type+src+dst+origin)
        self._g.add_edge(edge.src, edge.dst, key=edge.id, type=edge.type.value)
        return edge

    def add_fact(self, fact: Fact) -> Fact:
        if fact.supersedes and fact.supersedes in self.facts:
            old = self.facts[fact.supersedes]
            # CLAMP, not reject: model_copy skips the Fact._window_ok validator, so a
            # back-dated correction (new.valid_from < old.valid_from) would silently
            # persist an inverted window (valid_to < valid_from). Clamping to
            # old.valid_from yields a zero-length window — "no instant at which the old
            # value was the truth" — the correct reading of a back-dated correction.
            # Clamp is the safe choice because add_fact is the single mutation seam for
            # BOTH live fold and journal replay: it has no rejection channel, and raising
            # here could brick crash-resume replay of an already-written journal
            # (2026-07-22 review, finding 18).
            closed_at = max(fact.valid_from, old.valid_from)
            self.facts[fact.supersedes] = old.model_copy(
                update={"valid_to": closed_at, "state": FactState.SUPERSEDED})
        self.facts[fact.id] = fact
        return fact

    def retract_fact(self, fact_id: str) -> None:
        if fact_id in self.facts:
            self.facts[fact_id] = self.facts[fact_id].model_copy(
                update={"state": FactState.RETRACTED})

    def retract_edge(self, edge_id: str, *, invalidated_by: str | None = None,
                     at: datetime | None = None) -> None:
        """Tombstone a wrong inferred edge (a refuted CAUSED_BY) — symmetric with retract_fact
        (VALIDATION-VERDICT §B P0 #2). The edge stays in the graph as evidence of what was
        believed; state=RETRACTED closes it and valid_to records when."""
        if edge_id in self.edges:
            self.edges[edge_id] = self.edges[edge_id].model_copy(
                update={"state": FactState.RETRACTED, "invalidated_by": invalidated_by,
                        "valid_to": at})

    def add_event(self, event: Event) -> Event:
        self.events[event.id] = event
        return event

    def remap_id(self, old: str, new: str) -> None:
        """P5 step 4 — the deterministic reference remap (DOMAIN-v3 §9.2; the subsystem P3
        deferred Retype/Merge to). Applied ONLY by the fold from journaled Remap records, so a
        replay reproduces every rewrite bit-for-bit at the same seq:

        - `old → new` enters (and chain-compresses) the id_remaps table — the old id remains
          resolvable forever ("the old id becomes an alias"; write-once never violated);
        - fact.subject_ref / event.entity_ref pointing at `old` are rewritten (fact/event IDS
          are minted once and never move — provenance ordering, supersession chains and the
          hypothesis store's fact-id refs stay byte-stable);
        - edges touching `old` are re-keyed via registry.edge_id (endpoint ids are embedded in
          the edge id); a rewrite landing on an already-existing identical edge collapses onto
          it (first-writer-wins — the two split-brain halves asserted the same relation);
        - the old node record folds into the new one (canonical wins per prop/scheme) and is
          removed; alias-index bindings follow.

        Tolerates an `old` that was never a node (a `resolve` record redirecting a would-be
        twin id): only the table + index rewrites apply."""
        if old == new:
            return
        self.id_remaps[old] = new
        for k, v in self.id_remaps.items():
            if v == old:
                self.id_remaps[k] = new
        for fid, f in self.facts.items():
            if f.subject_ref == old:
                self.facts[fid] = f.model_copy(update={"subject_ref": new})
        for eid, e in self.events.items():
            if e.entity_ref == old:
                self.events[eid] = e.model_copy(update={"entity_ref": new})
        touched = [e for e in self.edges.values() if old in (e.src, e.dst)]
        if self._g.has_node(old):
            self._g.remove_node(old)              # drops old's incident nx edges; re-added below
        for e in touched:
            del self.edges[e.id]
        for e in touched:
            src = new if e.src == old else e.src
            dst = new if e.dst == old else e.dst
            nid = _edge_id(e.type, src, dst, e.origin)
            if nid in self.edges:
                continue                          # collapsed onto the surviving identical edge
            moved = e.model_copy(update={"id": nid, "src": src, "dst": dst})
            self.edges[nid] = moved
            self._g.add_edge(moved.src, moved.dst, key=moved.id, type=moved.type.value)
        old_node = self.nodes.pop(old, None)
        if old_node is not None and new in self.nodes:
            tgt = self.nodes[new]
            self.nodes[new] = tgt.model_copy(update={
                "props": {**old_node.props, **tgt.props},
                "aliases": {**old_node.aliases, **tgt.aliases}})
        for k, v in self.alias_index.items():
            if v == old:
                self.alias_index[k] = new
        if new in self.nodes:
            for scheme, val in self.nodes[new].aliases.items():
                self.alias_index.setdefault(resolver.alias_key(scheme, val), new)

    def retract_event(self, event_id: str, *, invalidated_by: str | None = None) -> None:
        """Tombstone a wrong telemetry Event (flaky exporter, misattributed occurrence) — an
        Event is point-in-time so it is never superseded, only RETRACTED (VALIDATION-VERDICT §B
        P0 #2). Kept in the journal as an append-only record of what was once observed."""
        if event_id in self.events:
            self.events[event_id] = self.events[event_id].model_copy(
                update={"state": FactState.RETRACTED, "invalidated_by": invalidated_by})

    # ── queries ───────────────────────────────────────────────────────────────
    def node(self, nid: str) -> Node | None:
        return self.nodes.get(nid)

    def nodes_of_type(self, ntype: NodeType) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == ntype]

    def facts_of(self, subject_ref: str, *, active_only: bool = True) -> list[Fact]:
        out = [f for f in self.facts.values() if f.subject_ref == subject_ref]
        if active_only:
            out = [f for f in out if f.state == FactState.ACTIVE]
        return out

    def events_of(self, entity_ref: str) -> list[Event]:
        return sorted((e for e in self.events.values() if e.entity_ref == entity_ref),
                      key=lambda e: e.occurred_at)

    def out_edges(self, nid: str, etype: EdgeType | None = None) -> list[Edge]:
        return [self.edges[k] for _, _, k in self._g.out_edges(nid, keys=True)
                if etype is None or self.edges[k].type == etype]

    def in_edges(self, nid: str, etype: EdgeType | None = None) -> list[Edge]:
        return [self.edges[k] for _, _, k in self._g.in_edges(nid, keys=True)
                if etype is None or self.edges[k].type == etype]

    def neighbors(self, nid: str, etype: EdgeType | None = None) -> list[str]:
        return [e.dst for e in self.out_edges(nid, etype)]

    def facts_valid_at(self, ts: datetime) -> list[Fact]:
        """Point-in-time (principle 8): facts whose valid window contains ts.

        SUPERSEDED facts are INCLUDED — a superseded fact is still the truth for every
        instant inside its (now-closed) window, and excluding it broke as-of-incident-start
        reconstruction the moment any fact was superseded (INV-5; 2026-07-22 review,
        finding 1). Only RETRACTED facts — observations disavowed as wrong, never true at
        any instant — are excluded from history.
        """
        return [f for f in self.facts.values()
                if f.state != FactState.RETRACTED and f.valid_from <= ts
                and (f.valid_to is None or ts < f.valid_to)]

    def structural_distances(self, anchor: str) -> dict[str, int]:
        """Hop distance from `anchor` to every structurally-connected node — a read-only,
        UNDIRECTED breadth-first walk over the ACTIVE structural spine (declared/discovered
        edges: DEPENDS_ON/RUNS_ON/CHANGED_BY/AFFECTS/...). The causal/evidence layer — any
        edge whose origin is INFERRED (CAUSED_BY, the derived SUPPORTS/REFUTES, ...) — is
        NEVER traversed, and hypothesis nodes are never entered: topological specificity
        (P4 belief arithmetic, DOMAIN-v3 §2.5) must come from OBSERVED structure, so a
        hypothesis's own causal claims can never shorten the distance of its own evidence.
        Level-order BFS: hop counts are independent of intra-level iteration order, so the
        result is deterministic under journal replay."""
        if anchor not in self.nodes:
            return {}
        adj: dict[str, list[str]] = {}
        for e in self.edges.values():
            if e.state != FactState.ACTIVE or e.origin == Origin.INFERRED:
                continue
            s, d = self.nodes.get(e.src), self.nodes.get(e.dst)
            if s is None or d is None:
                continue
            if s.type == NodeType.HYPOTHESIS or d.type == NodeType.HYPOTHESIS:
                continue
            adj.setdefault(e.src, []).append(e.dst)
            adj.setdefault(e.dst, []).append(e.src)
        dist = {anchor: 0}
        frontier, hops = [anchor], 0
        while frontier:
            hops += 1
            nxt: list[str] = []
            for n in frontier:
                for m in adj.get(n, ()):
                    if m not in dist:
                        dist[m] = hops
                        nxt.append(m)
            frontier = nxt
        return dist

    def reachable_from(self, nid: str, *, max_hops: int = 4) -> set[str]:
        seen, frontier = {nid}, [nid]
        for _ in range(max_hops):
            nxt = []
            for n in frontier:
                for m in self.neighbors(n):
                    if m not in seen:
                        seen.add(m)
                        nxt.append(m)
            frontier = nxt
            if not frontier:
                break
        return seen

    # ── serialisation (persistence uses these) ────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "nodes": [n.model_dump(mode="json") for n in self.nodes.values()],
            "edges": [e.model_dump(mode="json") for e in self.edges.values()],
            "facts": [f.model_dump(mode="json") for f in self.facts.values()],
            "events": [e.model_dump(mode="json") for e in self.events.values()],
            "remaps": dict(self.id_remaps),   # P5: graduated ids stay resolvable across a load
        }

    @classmethod
    def from_dict(cls, d: dict) -> Graph:
        g = cls()
        for n in d.get("nodes", []):
            g.upsert_node(Node.model_validate(n))
        for e in d.get("edges", []):
            g.add_edge(Edge.model_validate(e))
        for f in d.get("facts", []):
            fact = Fact.model_validate(f)
            g.facts[fact.id] = fact          # load as-is (windows already resolved)
        for e in d.get("events", []):
            g.add_event(Event.model_validate(e))
        g.id_remaps = {str(k): str(v) for k, v in d.get("remaps", {}).items()}
        return g

    def __len__(self) -> int:
        return len(self.nodes)
