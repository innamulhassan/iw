"""Fold-adapters — the only place a source's shape meets the graph schema. B9.4.

Each capability result is mapped into nodes/facts/edges by a per-source adapter. A new source = a
new fold-adapter, no engine change. Folding is idempotent (the graph dedups facts by
(node, key, source, observed_at)), so a crash-replay never duplicates.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.domain import Edge, Node

from .graph import IncidentGraph


@runtime_checkable
class FoldAdapter(Protocol):
    """Map one capability result into the graph; return the touched node ids."""

    def fold(self, result: dict, graph: IncidentGraph) -> list[str]: ...


class TopologyFold:
    """topology result {nodes:[Node...], edges:[Edge...]} → nodes + typed edges (e.g. depends_on)."""

    def fold(self, result: dict, graph: IncidentGraph) -> list[str]:
        touched: list[str] = []
        for nd in result.get("nodes", []):
            touched.append(graph.upsert_node(Node.model_validate(nd)))
        for ed in result.get("edges", []):
            graph.add_edge(Edge.model_validate(ed))
        return touched


class MetricsFold:
    """metrics result {target, facts:[Fact-kwargs...]} → facts on the target node."""

    def fold(self, result: dict, graph: IncidentGraph) -> list[str]:
        target = result["target"]
        for f in result.get("facts", []):
            graph.add_fact(target, **f)
        return [target]


class AlertFold:
    """alert result {alert:{id,...}, on:<node-id>} → an alert node + an `observed_on` edge."""

    def fold(self, result: dict, graph: IncidentGraph) -> list[str]:
        alert = dict(result["alert"])
        alert.setdefault("kind", "alert")
        alert.setdefault("type", "alert")
        nid = graph.upsert_node(Node.model_validate(alert))
        on = result.get("on")
        if on is not None:
            graph.add_edge(Edge.model_validate({"type": "observed_on", "from": nid, "to": on}))
        return [nid]


class FoldRegistry:
    """source id → its fold-adapter. The engine resolves a capability's source, then folds."""

    def __init__(self) -> None:
        self._by_source: dict[str, FoldAdapter] = {}

    def register(self, source: str, adapter: FoldAdapter) -> None:
        self._by_source[source] = adapter

    def fold(self, source: str, result: dict, graph: IncidentGraph) -> list[str]:
        adapter = self._by_source.get(source)
        if adapter is None:
            raise KeyError(f"no fold-adapter registered for source {source!r}")
        return adapter.fold(result, graph)


def default_registry() -> FoldRegistry:
    """A registry wired with the built-in sample adapters."""
    reg = FoldRegistry()
    reg.register("topology", TopologyFold())
    reg.register("metrics", MetricsFold())
    reg.register("alerts", AlertFold())
    return reg
