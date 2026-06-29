"""The render-slice — the bounded projection the LLM sees each turn. B9.3.

In full: the subject node, the current cause path, flagged nodes (suspect/impacted) + their facts,
and the 1-hop frontier (adjacent un-expanded nodes, as stubs). Collapsed: healthy / ruled-out /
resolved → a count + one-line summary. So a 147-node incident renders ~10–30 nodes — the graph
carries the memory, the slice shows only what's live.
"""
from __future__ import annotations

from typing import Optional

from .graph import IncidentGraph

_FLAGGED = ("suspect", "impacted")


def render_slice(graph: IncidentGraph, subject_id: str,
                 cause_path: Optional[list[str]] = None,
                 flagged_labels: tuple[str, ...] = _FLAGGED, *,
                 frontier_cap: int = 20, expand_cap: int = 12) -> dict:
    cause_path = cause_path or []
    present = set(graph.node_ids())

    full_ids: list[str] = []

    def add_full(nid: str) -> None:
        if nid in present and nid not in full_ids:
            full_ids.append(nid)

    add_full(subject_id)
    for nid in cause_path:
        add_full(nid)
    for nid in graph.node_ids():
        n = graph.raw_node(nid)
        flagged = bool(set(n.labels) & set(flagged_labels))
        if flagged or graph._is_unhealthy(n):           # NICE-4: recency-aware, single source of truth
            add_full(nid)

    full_set = set(full_ids)

    # 1-hop frontier — adjacent, not-yet-full nodes as stubs, GLOBALLY bounded (SHOU-2): without a
    # global cap the frontier scales ~expand_cap × |full|, blowing the B9.3 "~10–30 nodes" envelope.
    # Overflow neighbours fold into the collapsed count so full + frontier + collapsed == total holds.
    frontier: list[dict] = []
    frontier_ids: set[str] = set()
    overflow = 0
    for nid in full_ids:
        for stub in graph.neighbours(nid, cap=expand_cap).get("neighbours", []):
            sid = stub["id"]
            if sid in full_set or sid in frontier_ids:
                continue
            frontier_ids.add(sid)
            if len(frontier) < frontier_cap:
                frontier.append(stub)
            else:
                overflow += 1                           # counted once, rendered as collapsed

    rendered_ids = full_set | {s["id"] for s in frontier}
    collapsed_count = sum(1 for nid in present if nid not in rendered_ids)

    overflow_note = f" (incl {overflow} frontier over cap)" if overflow else ""
    return {
        "full": [graph.get(nid) for nid in full_ids],
        "frontier": frontier,
        "collapsed": {
            "count": collapsed_count,
            "summary": f"{collapsed_count} healthy / ruled-out · collapsed{overflow_note}",
        },
        "rendered": len(full_ids) + len(frontier),
        "total": len(graph),
    }
