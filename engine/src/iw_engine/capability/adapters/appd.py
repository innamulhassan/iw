"""AppDynamics adapter — business-transaction health + snapshot exit-call discovery
(DESIGN-INPUT §E.2: `BusinessTransaction` facts art_p95/epm/delta-vs-baseline; a
snapshot's exit-calls *discover* downstream backend CIs + `DEPENDS_ON` — exit-call type
is the branch switch, JDBC->Database, HTTP->Service/ExternalService, app-only->code (no
edge)). Follows the prometheus.py template: provider/intents/effect/normalize(raw).
"""
from __future__ import annotations

from ...domain import registry
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Origin, Source
from ...domain.operations import AddEdge, AddEvent, AddFact, AddNode, Operation
from ..layer import CapabilityMeta


class AppDAdapter:
    provider = "appd"
    intents = frozenset({
        "bt_health", "get_snapshots", "healthrule_violations", "flowmap", "fetch_traces",
    })
    effect = Effect.READ
    binding = Binding.MCP   # covered via the Splunk-Observability MCP convergence
    meta = CapabilityMeta(
        summary="Application topology, traces, and the JDBC/HTTP exit-call boundary",
        queries_by="app_id", returns="BT health, exit-calls, flow maps")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        svc = raw.get("service")
        svc_id: str | None = None
        if svc:
            props = {"service_name": svc["name"], "env": svc["env"]}
            ops.append(AddNode(type=NodeType.SERVICE, props=props))
            svc_id = registry.node_id(NodeType.SERVICE, props)

        bt = raw.get("bt")
        bt_id: str | None = None
        if bt and svc_id:
            props = {"service_name": svc["name"], "bt_name": bt["name"]}
            ops.append(AddNode(type=NodeType.BUSINESS_TRANSACTION, props=props))
            bt_id = registry.node_id(NodeType.BUSINESS_TRANSACTION, props)

        # bt_health — BT facts (art_p95 / epm / delta_vs_baseline)
        for m in raw.get("bt_metrics", []):
            subject = bt_id or svc_id
            if not subject:
                continue
            ops.append(AddFact(subject=subject, predicate=m["predicate"], value=m["value"],
                               unit=m.get("unit"), valid_from=m["at"], observed_at=m["at"],
                               source=Source.APPD, source_reliability=m.get("reliability")))

        # healthrule_violations — Alert FIRED_ON the underlying Service
        for v in raw.get("violations", []):
            aid = registry.node_id(NodeType.ALERT, {"alert_id": v["id"]})
            ops.append(AddNode(type=NodeType.ALERT,
                               props={"alert_id": v["id"], "rule": v.get("rule"),
                                      "severity": v.get("severity")}))
            ops.append(AddEvent(entity=aid, type="fired", occurred_at=v["at"],
                                observed_at=v["at"], payload={"severity": v.get("severity")},
                                source=Source.APPD))
            if svc_id:
                ops.append(AddEdge(type=EdgeType.FIRED_ON, src=aid, dst=svc_id))

        # get_snapshots — exit-call-driven discovery + DEPENDS_ON
        for snap in raw.get("snapshots", []):
            if svc_id:
                self._fold_exit_calls(ops, svc_id, snap.get("exit_calls", []))

        # flowmap — the same exit-call fold, each entry carrying its own source service
        # (a flowmap spans the whole observed call topology, not just one BT's service)
        for hop in raw.get("flowmap", []):
            hop_svc = hop.get("service", svc)
            if not hop_svc:
                continue
            hop_props = {"service_name": hop_svc["name"], "env": hop_svc["env"]}
            ops.append(AddNode(type=NodeType.SERVICE, props=hop_props))
            hop_id = registry.node_id(NodeType.SERVICE, hop_props)
            self._fold_exit_calls(ops, hop_id, hop.get("exit_calls", []))

        # fetch_traces — a trace is captured on the BT/Service; may itself carry
        # exit-calls (a distributed trace's backend hops), folded the same way
        for tr in raw.get("traces", []):
            subject = bt_id or svc_id
            if subject:
                ops.append(AddEvent(entity=subject, type="trace_captured", occurred_at=tr["at"],
                                    observed_at=tr["at"],
                                    payload={"trace_id": tr["trace_id"],
                                             "duration_ms": tr.get("duration_ms"),
                                             "error": tr.get("error", False)},
                                    source=Source.APPD))
            if svc_id:
                self._fold_exit_calls(ops, svc_id, tr.get("exit_calls", []))

        return ops

    @staticmethod
    def _fold_exit_calls(ops: list[Operation], src_id: str, exit_calls: list[dict]) -> None:
        """The exit-call branch switch (DESIGN-INPUT §E.2): JDBC discovers a Database,
        HTTP discovers a Service or ExternalService — both DEPENDS_ON dependent->
        provider, origin=discovered (telemetry, not the CMDB-declared spine). Any other
        exit-call type (app-only/code) yields no downstream CI — no edge."""
        for call in exit_calls:
            ctype = call.get("type")
            if ctype == "JDBC":
                props = {"db_id": call["db_id"], "engine": call.get("engine")}
                ops.append(AddNode(type=NodeType.DATABASE, props=props))
                dst = registry.node_id(NodeType.DATABASE, props)
                ops.append(AddEdge(type=EdgeType.DEPENDS_ON, src=src_id, dst=dst,
                                   origin=Origin.DISCOVERED))
            elif ctype == "HTTP":
                if "target_external" in call:
                    props = {"service_name": call["target_external"], "vendor": call.get("vendor")}
                    ops.append(AddNode(type=NodeType.EXTERNAL_SERVICE, props=props))
                    dst = registry.node_id(NodeType.EXTERNAL_SERVICE, props)
                else:
                    props = {"service_name": call["target_service"], "env": call.get("target_env")}
                    ops.append(AddNode(type=NodeType.SERVICE, props=props))
                    dst = registry.node_id(NodeType.SERVICE, props)
                ops.append(AddEdge(type=EdgeType.DEPENDS_ON, src=src_id, dst=dst,
                                   origin=Origin.DISCOVERED))
            # else: app-only/code exit-call — an in-process hop, no downstream CI to discover
