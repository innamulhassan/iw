"""Splunk adapter — log search + firewall denies. Follows the Prometheus reference
adapter's shape exactly: `provider`, `intents`, `effect`, and a pure `normalize(raw)`
that maps the tool's raw JSON shape into typed Operations folding into the incident
graph. Deduped exception clusters fold into an `ErrorSignature` node EMITTED by
whichever Service/Pod produced them; clean policy denies (`action="blocked"`) fold
into a `deny_count` fact on the `FirewallRule` node they hit.
"""
from __future__ import annotations

from ...domain import registry
from ...domain.assertion import Window
from ...domain.common import EvidenceRef
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Source, Species, Stat
from ...domain.operations import AddAssertion, AddEdge, AddNode, Operation
from ..layer import CapabilityMeta


class SplunkAdapter:
    provider = "splunk"
    intents = frozenset({
        "search_errors",
        "error_signature_topk",
        "search_fw_denies",
        "transaction_trace",
        "fetch_logs",
    })
    effect = Effect.READ
    binding = Binding.MCP   # Splunk ships a first-party MCP server (GA)
    meta = CapabilityMeta(
        summary="Logs, error signatures, and firewall-deny records",
        queries_by="service_name", returns="error signatures, denials")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        default_svc = raw.get("service")
        default_pod = raw.get("pod")

        def emitter_id(emitted_by: dict | None) -> str | None:
            """Resolve (and, if new, emit) the Service|Pod node a log line came from —
            explicit per-entry `emitted_by` wins, else the raw's top-level default."""
            emitted_by = emitted_by or (
                {"kind": "service", **default_svc} if default_svc
                else {"kind": "pod", **default_pod} if default_pod
                else None
            )
            if not emitted_by:
                return None
            if emitted_by["kind"] == "service":
                props = {"service_name": emitted_by["name"], "env": emitted_by["env"]}
                ops.append(AddNode(type=NodeType.SERVICE, props=props))
                return registry.node_id(NodeType.SERVICE, props)
            if emitted_by["kind"] == "pod":
                props = {"uid": emitted_by["uid"]}
                ops.append(AddNode(type=NodeType.POD, props=props))
                return registry.node_id(NodeType.POD, props)
            return None

        for err in raw.get("errors", []):
            src_id = emitter_id(err.get("emitted_by"))

            sig_key = {"signature_hash": err["signature_hash"]}
            ops.append(AddNode(type=NodeType.ERROR_SIGNATURE, props={
                **sig_key,
                "exception_class": err.get("exception_class"),
                "first_seen": err.get("first_seen"),
                "file_line": err.get("file_line"),
            }))
            sig_id = registry.node_id(NodeType.ERROR_SIGNATURE, sig_key)

            at = err["_time"]
            reliability = err.get("reliability")   # None -> reducer fills tunables default
            evidence = [EvidenceRef(kind="trace_id", ref=err["trace_id"])] if err.get("trace_id") else []
            # the deduped occurrence count is a measured READING (stat=count, point window at the
            # observation time); last_seen is a timestamp PROPERTY (the §9.1 content/identity-
            # adjacent set — the P1a shim classified it so). Both fold to a byte-identical Fact.
            ops.append(AddAssertion(subject=sig_id, name="count", value=err["count"],
                                    species=Species.READING, stat=Stat.COUNT, window=Window(at=at),
                                    valid_from=at, observed_at=at, source=Source.SPLUNK,
                                    source_reliability=reliability, evidence=evidence,
                                    source_native_name="count"))
            ops.append(AddAssertion(subject=sig_id, name="last_seen",
                                    value=err.get("last_seen", at), species=Species.PROPERTY,
                                    valid_from=at, observed_at=at, source=Source.SPLUNK,
                                    source_reliability=reliability, source_native_name="last_seen"))

            if src_id:
                ops.append(AddEdge(type=EdgeType.EMITTED, src=src_id, dst=sig_id))

        for deny in raw.get("fw_denies", []):
            if deny.get("action") != "blocked":
                continue
            rule_key = {"rule_id": deny["rule_id"]}
            ops.append(AddNode(type=NodeType.FIREWALL_RULE, props={
                **rule_key,
                "direction": deny.get("direction"),
                "proto": deny.get("proto"),
                "port_range": deny.get("port_range"),
                "src": deny.get("src"),
                "dst": deny.get("dst"),
            }))
            rule_id = registry.node_id(NodeType.FIREWALL_RULE, rule_key)
            at = deny["_time"]
            ops.append(AddAssertion(subject=rule_id, name="deny_count",
                                    value=deny.get("deny_count", 1), species=Species.READING,
                                    stat=Stat.COUNT, window=Window(at=at), valid_from=at,
                                    observed_at=at, source=Source.SPLUNK,
                                    source_reliability=deny.get("reliability"),
                                    source_native_name="deny_count"))
        return ops
