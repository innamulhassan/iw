"""Event-aggregation / AIOps-correlation adapter (BigPanda, Moogsoft, ...).

This is the layer ABOVE raw monitoring. Monitoring tools (Prometheus, AppD, ...) fire many
individual alerts; the aggregation platform DEDUPLICATES and CORRELATES them into a single
high-level incident, reduces noise, and enriches with the affected-service blast radius and the
co-firing / flapping signals. The investigation taps this FIRST — instead of a wall of raw
alerts, it gets "these N alerts across these M services are ONE incident, and here are the
related/co-firing incidents." That correlated cluster is a strong hypothesis prior.

normalize() folds the correlated cluster into the graph: the member Alert nodes (each FIRED_ON
its service), the affected Service nodes, and the co-firing/related Incident nodes linked back to
the primary with SIMILAR_TO — the same shapes the rest of the engine already reasons over, just
sourced from the correlation engine rather than a single monitoring tool.
"""
from __future__ import annotations

from ...domain import registry
from ...domain.enums import Binding, ConfidenceLevel, EdgeType, Effect, NodeType, Source
from ...domain.operations import AddEdge, AddEvent, AddNode, Operation
from ..layer import CapabilityMeta


class BigPandaAdapter:
    provider = "bigpanda"
    intents = frozenset({"get_correlated_incident", "list_correlated_alerts", "get_flapping_signals"})
    effect = Effect.READ
    binding = Binding.MCP   # AIOps platforms expose a tool/API surface; wrap it as MCP or REST
    meta = CapabilityMeta(
        summary="Event correlation — collapses an alert storm into one incident",
        queries_by="incident_id", returns="correlated incident, member alerts, related incidents")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []

        # the primary (correlated) incident — the single incident the cluster rolls up to. The
        # correlation engine is what MINTS this incident from the alert storm, so create the node.
        primary = raw.get("primary_incident")
        primary_id: str | None = None
        if primary:
            p_props = {"incident_id": primary, "severity": raw.get("severity")}
            ops.append(AddNode(type=NodeType.INCIDENT, props=p_props))
            primary_id = registry.node_id(NodeType.INCIDENT, p_props)

        # the affected-service blast radius the correlation computed
        svc_ids: dict[str, str] = {}
        for svc in raw.get("affected_services", []):
            props = {"service_name": svc["name"], "env": svc.get("env", "prod")}
            ops.append(AddNode(type=NodeType.SERVICE, props=props))
            svc_ids[svc["name"]] = registry.node_id(NodeType.SERVICE, props)

        # the member alerts the platform correlated into this one incident
        for al in raw.get("correlated_alerts", []):
            aid = registry.node_id(NodeType.ALERT, {"alert_id": al["id"]})
            ops.append(AddNode(type=NodeType.ALERT,
                               props={"alert_id": al["id"], "rule": al.get("alertname")}))
            at = al.get("at")
            if at:
                ops.append(AddEvent(entity=aid, type="fired", occurred_at=at, observed_at=at,
                                    payload={"state": al.get("state", "firing"),
                                             "correlated": True}, source=Source.BIGPANDA))
            svc_id = svc_ids.get(al.get("service"))
            if svc_id:
                ops.append(AddEdge(type=EdgeType.FIRED_ON, src=aid, dst=svc_id))

        # the co-firing / related incidents the platform clustered alongside the primary — the
        # "N other services reported the same in the same window" prior, as SIMILAR_TO links
        for ri in raw.get("related_incidents", []):
            ri_props = {"incident_id": ri["number"], "severity": ri.get("priority")}
            ops.append(AddNode(type=NodeType.INCIDENT, props=ri_props))
            ri_id = registry.node_id(NodeType.INCIDENT, ri_props)
            at = ri.get("opened_at")
            if at:
                ops.append(AddEvent(entity=ri_id, type="declared", occurred_at=at, observed_at=at,
                                    payload={"affected_ci": ri.get("cmdb_ci")}, source=Source.BIGPANDA))
            if primary_id:
                ops.append(AddEdge(type=EdgeType.SIMILAR_TO, src=primary_id, dst=ri_id,
                                   confidence_level=ConfidenceLevel(ri.get("confidence", "med"))))
        return ops
