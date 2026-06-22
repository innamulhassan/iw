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
                 flagged_labels: tuple[str, ...] = _FLAGGED) -> dict:
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
        unhealthy = any(f.impact_state is not None and f.impact_state.value != "ok" for f in n.facts)
        if flagged or unhealthy:
            add_full(nid)

    full_set = set(full_ids)

    # 1-hop frontier — adjacent, not-yet-full nodes, as expandable stubs
    frontier: list[dict] = []
    frontier_ids: set[str] = set()
    for nid in full_ids:
        for stub in graph.neighbours(nid, cap=64).get("neighbours", []):
            sid = stub["id"]
            if sid not in full_set and sid not in frontier_ids:
                frontier.append(stub)
                frontier_ids.add(sid)

    rendered_ids = full_set | frontier_ids
    collapsed_count = sum(1 for nid in present if nid not in rendered_ids)

    return {
        "full": [graph.get(nid) for nid in full_ids],
        "frontier": frontier,
        "collapsed": {
            "count": collapsed_count,
            "summary": f"{collapsed_count} healthy / ruled-out · collapsed",
        },
        "rendered": len(full_ids) + len(frontier),
        "total": len(graph),
    }
