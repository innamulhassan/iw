"""render_slice — a bounded, LLM-facing (and FE-facing) view of the graph. Hands the FULL
graph (capped at max_nodes) so the planner sees the evidence its own tools produced; `focus`
is only a HIGHLIGHT label, never a filter. Collapsing to the focus neighbourhood starved the
planner whenever the symptom node was topologically isolated (VALIDATION-VERDICT §A gap 5).
"""
from __future__ import annotations

from .graph import Graph


def render_slice(graph: Graph, focus: str | None = None, *, max_nodes: int = 40,
                 max_facts_per_node: int = 6) -> dict:
    # Full graph, capped — never a focus-reachability slice (GAP 5). Keep the focus node
    # first so the cap can never drop the symptom the planner is reasoning about.
    if focus and focus in graph.nodes:
        ordered = [focus] + [nid for nid in graph.nodes if nid != focus]
    else:
        ordered = list(graph.nodes)
    ids = set(ordered[:max_nodes])

    nodes = []
    for nid, n in graph.nodes.items():
        if nid not in ids:
            continue
        facts = graph.facts_of(nid)[:max_facts_per_node]
        nodes.append({
            "id": n.id, "type": n.type.value, "props": n.props,
            "highlight": nid == focus,   # the focus is surfaced as a label, not a filter (GAP 5)
            "facts": [{"predicate": f.predicate, "value": f.value, "unit": f.unit,
                       "valid_from": f.valid_from.isoformat(), "source": f.source.value}
                      for f in facts],
        })
    edges = [{"type": e.type.value, "src": e.src, "dst": e.dst, "origin": e.origin.value}
             for e in graph.edges.values() if e.src in ids and e.dst in ids]
    return {"focus": focus, "nodes": nodes, "edges": edges,
            "truncated": len(graph.nodes) > len(ids)}
