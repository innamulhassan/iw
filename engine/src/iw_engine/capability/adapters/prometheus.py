"""Prometheus adapter — metrics + alerts. REFERENCE adapter (the template the other 7
follow): a `provider`, an `intents` set, an `effect`, and a pure `normalize(raw)` that
maps the tool's raw JSON shape into typed Operations folding into the incident graph.
The raw shape is realistic and tool-specific; the mock swaps only the query() boundary.
"""
from __future__ import annotations

from ...domain import registry
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Source
from ...domain.operations import AddEdge, AddEvent, AddFact, AddNode, Operation
from ..layer import CapabilityMeta


class PrometheusAdapter:
    provider = "prometheus"
    intents = frozenset({"active_alerts", "instant_query", "range_query", "fetch_metrics"})
    effect = Effect.READ
    binding = Binding.REST   # no first-party MCP server — trivial raw REST
    meta = CapabilityMeta(
        summary="Time-series metrics (RED/USE) and firing alerts",
        queries_by="service_name", returns="alerts + metric facts")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        svc = raw.get("service")
        svc_id: str | None = None
        if svc:
            props = {"service_name": svc["name"], "env": svc["env"]}
            ops.append(AddNode(type=NodeType.SERVICE, props=props))
            svc_id = registry.node_id(NodeType.SERVICE, props)

        for al in raw.get("alerts", []):
            aid = registry.node_id(NodeType.ALERT, {"alert_id": al["id"]})
            ops.append(AddNode(type=NodeType.ALERT,
                               props={"alert_id": al["id"], "rule": al.get("alertname")}))
            ops.append(AddEvent(entity=aid, type="fired", occurred_at=al["at"],
                                observed_at=al["at"], payload={"state": al.get("state", "firing")},
                                source=Source.PROMETHEUS))
            if svc_id:
                ops.append(AddEdge(type=EdgeType.FIRED_ON, src=aid, dst=svc_id))

        for m in raw.get("metrics", []):
            subject = m.get("subject", svc_id)
            if not subject:
                continue
            ops.append(AddFact(subject=subject, predicate=m["predicate"], value=m["value"],
                               unit=m.get("unit"), valid_from=m["at"], observed_at=m["at"],
                               source=Source.PROMETHEUS, source_reliability=m.get("reliability")))
        return ops
