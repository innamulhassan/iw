"""IncidentGraph — the engine-owned, in-process per-incident graph + governed tool surface. B9.

networkx.DiGraph under the hood (cycle-safe traversal). The LLM NEVER receives this object; it acts
only through the tool surface (B9.2), each tool a governed capability. Bulk graph-building is
automatic via the fold-adapter (fold.py); the LLM only queries + records explicit findings
(`annotate`). Mutations are validated against the Part D domain types and folds are idempotent
(B9.6), so a replayed step never duplicates and conflicting facts are kept side-by-side.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

import networkx as nx

from engine.domain import Edge, Fact, Node

UNKNOWN = "unknown"
# (node id, fact key, source, observed_at, evidence_ref) — evidence_ref is part of the key so a
# corrected finding (new evidence) is KEPT (B9.6 "never silently overwrites"); a true replay carries
# the same evidence_ref and stays idempotent.
_FactKey = tuple[str, str, str, Optional[str], Optional[str]]


class IncidentGraph:
    def __init__(self) -> None:
        self._g = nx.DiGraph()

    # ── internal mutation (used by fold + annotate; never exposed to the LLM) ──────────
    def upsert_node(self, node: Node) -> str:
        """Add a node, or merge into an existing one (labels/props/sources unioned, facts folded
        idempotently). Never overwrites."""
        nid = node.id
        if self._g.has_node(nid):
            cur: Node = self._g.nodes[nid]["node"]
            cur.labels = list(dict.fromkeys([*cur.labels, *node.labels]))
            cur.props = {**cur.props, **node.props}
            cur.sources = list(dict.fromkeys([*cur.sources, *node.sources]))
            cur.name = cur.name or node.name
            cur.layer = cur.layer or node.layer
            if node.summary:
                cur.summary = node.summary
            for f in node.facts:
                self._fold_fact(nid, f)
        else:
            self._g.add_node(nid, node=node.model_copy(deep=True))
        return nid

    def _fact_key(self, node_id: str, f: Fact) -> _FactKey:
        return (node_id, f.key, f.source, f.observed_at, f.evidence_ref)

    def _fold_fact(self, node_id: str, fact: Fact) -> bool:
        """Idempotent fact insert, keyed by (node id, fact key, source, observed_at, evidence_ref)
        — B9.6. A replay (same key) is a no-op; a different source OR a corrected finding (new
        evidence_ref) on the same fact key is KEPT (conflicting/corrected facts stay visible, never
        silently overwritten)."""
        node: Node = self._g.nodes[node_id]["node"]
        k = self._fact_key(node_id, fact)
        if any(self._fact_key(node_id, e) == k for e in node.facts):
            return False
        node.facts.append(fact)
        return True

    def add_fact(self, node_id: str, **fact_kwargs: Any) -> bool:
        if not self._g.has_node(node_id):
            raise KeyError(f"{node_id!r} unknown — fold/annotate the node first (never invent)")
        return self._fold_fact(node_id, Fact(**fact_kwargs))

    def add_edge(self, edge: Edge) -> None:
        for end in (edge.from_, edge.to):
            if not self._g.has_node(end):
                raise KeyError(f"edge endpoint {end!r} unknown — fold its node first")
        self._g.add_edge(edge.from_, edge.to, type=edge.type, props=edge.props, sources=edge.sources)

    # ── the tool surface (B9.2) — what the LLM may call, all returning plain data ────────
    def get(self, id: str) -> dict:
        """read · a node + its facts."""
        if not self._g.has_node(id):
            return {"id": id, "status": UNKNOWN}
        return self._g.nodes[id]["node"].model_dump(by_alias=True)

    def neighbours(self, id: str, edge: Optional[str] = None, dir: str = "out", cap: int = 12) -> dict:
        """read · adjacent nodes — expand the frontier. Caps breadth (B9.6 expand-too-wide)."""
        if not self._g.has_node(id):
            return {"id": id, "status": UNKNOWN}
        if dir == "in":
            pairs = [(u, d) for (u, _v, d) in self._g.in_edges(id, data=True)]
        else:
            pairs = [(v, d) for (_u, v, d) in self._g.out_edges(id, data=True)]
        if edge:
            pairs = [(n, d) for (n, d) in pairs if d.get("type") == edge]
        nodes = [n for (n, _d) in pairs]
        out: dict = {"id": id, "dir": dir, "neighbours": [self._stub(n) for n in nodes[:cap]]}
        if len(nodes) > cap:
            out["more"] = len(nodes) - cap  # "N more, collapsed" — the agent narrows
        return out

    def walk(self, from_id: str, edges: list[str], dir: str = "out",
             until: Optional[dict] = None, max_depth: int = 12) -> dict:
        """read · traverse along the given edge types (e.g. depends_on downward to the cause).
        Cycle-safe via a visited set (B9.6 cycles). With `until`, BFS the edge-type-filtered
        subgraph for the NEAREST matching node — so a branching topology (a→dead vs a→c→target,
        the norm for dependency fan-out) does not silently dead-end on the wrong branch — and
        returns `reached` so a miss is never silent. Without `until`, a greedy single-path frontier
        walk (`reached` is None)."""
        if not self._g.has_node(from_id):
            return {"id": from_id, "status": UNKNOWN}
        if until is not None:
            return self._bfs_until(from_id, edges, dir, until, max_depth)
        path = self._greedy_path(from_id, edges, dir, max_depth)
        return {"from": from_id, "path": path, "nodes": [self._stub(n) for n in path], "reached": None}

    def _greedy_path(self, from_id: str, edges: list[str], dir: str, max_depth: int) -> list[str]:
        path = [from_id]
        visited = {from_id}
        cur = from_id
        for _ in range(max_depth):
            step = self._g.out_edges(cur, data=True) if dir == "out" else self._g.in_edges(cur, data=True)
            nxt = None
            for (u, v, d) in step:
                cand = v if dir == "out" else u
                if d.get("type") in edges and cand not in visited:
                    nxt = cand
                    break
            if nxt is None:
                break
            path.append(nxt)
            visited.add(nxt)
            cur = nxt
        return path

    def _bfs_until(self, from_id: str, edges: list[str], dir: str, until: dict, max_depth: int) -> dict:
        q: deque[tuple[str, list[str]]] = deque([(from_id, [from_id])])
        visited = {from_id}
        while q:
            cur, p = q.popleft()
            if cur != from_id and self._matches(cur, until):
                return {"from": from_id, "path": p, "nodes": [self._stub(n) for n in p], "reached": True}
            if len(p) - 1 >= max_depth:
                continue
            step = self._g.out_edges(cur, data=True) if dir == "out" else self._g.in_edges(cur, data=True)
            for (u, v, d) in step:
                cand = v if dir == "out" else u
                if d.get("type") in edges and cand not in visited:
                    visited.add(cand)
                    q.append((cand, [*p, cand]))
        return {"from": from_id, "path": [from_id], "nodes": [self._stub(from_id)], "reached": False}

    def find(self, predicate: dict) -> dict:
        """read · nodes matching a structured predicate (label/layer/kind/type/id/name/unhealthy/
        impacted). Structured (not arbitrary code) so it stays deterministic + governable; an
        unrecognized key matches nothing (never silently match-all)."""
        matches = [self._stub(nid) for nid in self._g.nodes if self._matches(nid, predicate)]
        return {"predicate": predicate, "matches": matches, "count": len(matches)}

    def blast_radius(self, id: str, depends_edge: str = "depends_on", affects_edge: str = "affects") -> dict:
        """read · the impacted set: things that depend_on (or are hosted_on) id (dependents) + things
        id affects. hosted_on shares depends_on's impact direction (a hosted thing fails when its
        host fails), so the app→db→storage chain is fully covered for a storage-rooted query."""
        if not self._g.has_node(id):
            return {"id": id, "status": UNKNOWN}
        depends_types = (depends_edge, "hosted_on")
        dep, aff = nx.DiGraph(), nx.DiGraph()
        for (u, v, d) in self._g.edges(data=True):
            if d.get("type") in depends_types:
                dep.add_edge(u, v)
            elif d.get("type") == affects_edge:
                aff.add_edge(u, v)
        impacted: set[str] = set()
        if id in dep:
            impacted |= nx.ancestors(dep, id)   # X depends_on id ⇒ X impacted
        if id in aff:
            impacted |= nx.descendants(aff, id)  # id affects Y ⇒ Y impacted
        impacted.discard(id)
        return {"id": id, "impacted": sorted(impacted), "count": len(impacted)}

    def path(self, from_id: str, to_id: str) -> dict:
        """read · the shortest causal path."""
        if not (self._g.has_node(from_id) and self._g.has_node(to_id)):
            return {"from": from_id, "to": to_id, "status": UNKNOWN}
        try:
            p = nx.shortest_path(self._g, from_id, to_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            p = []
        return {"from": from_id, "to": to_id, "path": p}

    def annotate(self, target: str, key: str, value: Any, evidence_ref: str,
                 by_step: Optional[str] = None) -> dict:
        """write · the agent records a finding (a fact, or a label like `suspect`), provenance-
        stamped. Requires an evidence_ref — a finding with no evidence is REJECTED (B9.6). Audited,
        not gated like a remediation (it changes the working graph, not the world)."""
        if not evidence_ref:
            raise ValueError("annotate requires an evidence_ref — a finding without evidence is rejected (B9.6)")
        if not self._g.has_node(target):
            raise KeyError(f"annotate target {target!r} unknown — fold/add it first (never invent)")
        node: Node = self._g.nodes[target]["node"]
        if key == "label":
            if value not in node.labels:
                node.labels.append(value)
            return {"target": target, "label": value, "evidence_ref": evidence_ref, "by_step": by_step}
        self._fold_fact(target, Fact(key=key, value=value, source="agent",
                                     evidence_ref=evidence_ref, observed_at=by_step or "annotate"))
        return {"target": target, "fact": {key: value}, "evidence_ref": evidence_ref, "by_step": by_step}

    # ── helpers + introspection (engine/tests, NOT the LLM) ─────────────────────────────
    def _stub(self, nid: str) -> dict:
        n: Node = self._g.nodes[nid]["node"]
        return {"id": n.id, "kind": n.kind, "type": n.type, "layer": n.layer,
                "labels": list(n.labels), "expandable": True}

    _MATCH_KEYS = frozenset({"label", "layer", "kind", "type", "id", "name", "unhealthy", "impacted"})

    def _matches(self, nid: str, predicate: dict) -> bool:
        n: Node = self._g.nodes[nid]["node"]
        # an UNRECOGNIZED predicate key is a no-match (never a silent match-all — find/walk must stay
        # deterministic). An empty predicate {} still matches every node.
        for k in predicate:
            if k not in self._MATCH_KEYS:
                return False
        if "label" in predicate and predicate["label"] not in n.labels:
            return False
        if "layer" in predicate and n.layer != predicate["layer"]:
            return False
        if "kind" in predicate and n.kind != predicate["kind"]:
            return False
        if "type" in predicate and n.type != predicate["type"]:
            return False
        if "id" in predicate and n.id != predicate["id"]:
            return False
        if "name" in predicate and n.name != predicate["name"]:
            return False
        # `unhealthy` and `impacted` are the same query: a node carrying a non-ok fact.
        if (predicate.get("unhealthy") or predicate.get("impacted")) and not self._is_unhealthy(n):
            return False
        return True

    @staticmethod
    def _is_unhealthy(n: Node) -> bool:
        # recency-aware (NICE-4): per fact key keep the LATEST impact_state (by observed_at), so a
        # stale 'degraded' reading doesn't outvote a fresh 'ok'. (get() stays unordered on purpose —
        # the agent reconciles conflicting facts; this is only the engine-level health verdict.)
        latest: dict[str, Fact] = {}
        for f in n.facts:
            if f.impact_state is None:
                continue
            prev = latest.get(f.key)
            if prev is None or (f.observed_at or "") >= (prev.observed_at or ""):
                latest[f.key] = f
        return any(f.impact_state is not None and f.impact_state.value != "ok" for f in latest.values())

    def __len__(self) -> int:
        return self._g.number_of_nodes()

    def node_ids(self) -> list[str]:
        return list(self._g.nodes)

    def raw_node(self, id: str) -> Node:
        return self._g.nodes[id]["node"]
