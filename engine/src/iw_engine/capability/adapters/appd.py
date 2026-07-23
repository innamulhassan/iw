"""AppDynamics adapter — business-transaction health + snapshot exit-call discovery
(DESIGN-INPUT §E.2: `BusinessTransaction` facts art_p95/epm/delta-vs-baseline; a
snapshot's exit-calls *discover* downstream backend CIs + `DEPENDS_ON` — exit-call type
is the branch switch, JDBC->Database, HTTP->Service/ExternalService, app-only->code (no
edge)). Follows the prometheus.py template: provider/intents/effect/normalize(raw).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ...domain import registry
from ...domain.assertion import Window
from ...domain.common import EvidenceRef
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Origin, Source, Species, Stat
from ...domain.operations import AddAssertion, AddEdge, AddNode, Operation
from ..layer import CapabilityMeta


def _as_dt(v: datetime | str) -> datetime:
    """A span's ended_at is started_at + duration, so a raw ISO-string `at` must become a datetime
    BEFORE the interval math (readings pass the string straight to pydantic and never do arithmetic
    on it). Accepts an already-parsed datetime unchanged; tolerates a trailing `Z` (UTC)."""
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))


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
            # BT telemetry (art_p95 / epm / delta_vs_baseline) → a summary READING + a `metric_query`
            # HANDLE (the stream-ladder, §3/§8.1 — the same rule metrics/logs/spans share: never
            # inline a raw stream, author the unit + a handle to re-fetch the curve). Fixtures state
            # no stat/window, so stat=gauge + point window keeps the reducer's Fact byte-identical;
            # the handle rides evidence[] (graph-internal, not bundle-serialized) so goldens hold.
            ops.append(AddAssertion(subject=subject, name=m["predicate"], value=m["value"],
                                    unit=m.get("unit"), species=Species.READING,
                                    stat=Stat.GAUGE, window=Window(at=m["at"]),
                                    valid_from=m["at"], observed_at=m["at"], source=Source.APPD,
                                    source_reliability=m.get("reliability"),
                                    evidence=[EvidenceRef(kind="metric_query",
                                                          ref=f'{m["predicate"]}{{bt="{subject}"}}',
                                                          label=m["predicate"])],
                                    source_native_name=m["predicate"]))

        # healthrule_violations — Alert FIRED_ON the underlying Service
        for v in raw.get("violations", []):
            aid = registry.node_id(NodeType.ALERT, {"alert_id": v["id"]})
            ops.append(AddNode(type=NodeType.ALERT,
                               props={"alert_id": v["id"], "rule": v.get("rule"),
                                      "severity": v.get("severity")}))
            ops.append(AddAssertion(subject=aid, name="fired", species=Species.EVENT,
                                    occurred_at=v["at"], observed_at=v["at"],
                                    value={"severity": v.get("severity")},
                                    source=Source.APPD, source_native_name="fired"))
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

        # fetch_traces — a distributed trace is a bounded HAPPENING with start + duration + outcome:
        # it folds to a SPAN, NOT an event (2026-07-23 primitives §2.6/§3 ladder: spans/traces are a
        # HANDLE (trace_id) + a SPAN datum). `[started_at, ended_at)` = at .. at+duration_ms (a trace
        # with no duration is in-flight -> the engine derives OPEN); correlation_id = trace_id (§4.4,
        # joining sibling hops + a future Rung-2 reified occurrence); the error flag is the outcome on
        # `value`; the vendor's own name survives on source_native_name. The subject is the BT/Service
        # node (a Rung-1 node-borne happening that promotes to a reified BUSINESS_TRANSACTION on
        # referability, §4.2). It may itself carry exit-calls (the trace's backend hops), folded the
        # same way.
        for tr in raw.get("traces", []):
            subject = bt_id or svc_id
            if subject:
                started = _as_dt(tr["at"])              # parse once: ended_at needs interval math
                dur = tr.get("duration_ms")
                ended = started + timedelta(milliseconds=dur) if dur is not None else None
                ops.append(AddAssertion(subject=subject, name="trace", species=Species.SPAN,
                                        valid_from=started, valid_to=ended, observed_at=started,
                                        value={"error": tr.get("error", False)},
                                        correlation_id=tr["trace_id"], source=Source.APPD,
                                        source_reliability=tr.get("reliability"),
                                        source_native_name="trace_captured"))
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
            elif ctype == "REDIS":
                # REDIS discovers a Cache exactly as JDBC discovers a Database (live retest
                # 2026-07-22: the branch was missing, so no tool could ever create the cache
                # node and every prometheus cache:<id> fact rejected 'unknown subject' — the
                # scripted twin masked it by hand-authoring the node, which a live planner
                # is forbidden to do).
                props = {"cache_id": call["cache_id"]}
                ops.append(AddNode(type=NodeType.CACHE, props=props))
                dst = registry.node_id(NodeType.CACHE, props)
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
