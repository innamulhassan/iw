"""Single-use demo wiring — a capability layer whose adapters return realistic per-intent incident
data, so the LLM planner has genuine evidence to reason over (no real ServiceNow/Datadog needed).

Mirrors the capability/intent map of the test fixture (three providers cover every playbook intent),
but the returned data VARIES by intent (the planner puts the intent in the call args). Topology folds
into the incident graph; everything else is read as step evidence by the model.
"""
from __future__ import annotations

from engine.capability import AdapterRegistry, CapabilityLayer, CapabilityRegistry
from engine.domain import (
    Access,
    CapabilityPolicy,
    DeclaredCapability,
    Effect,
    PolicyStatus,
    Provider,
    ProviderKind,
)
from engine.graph_runtime import FoldRegistry, TopologyFold

# Realistic synthetic evidence for a payments-api latency incident, keyed by INTENT.
DEMO_DATA: dict[str, dict] = {
    "incident-source": {"incident": "INC-4821", "priority": "P1", "opened_at": "09:12",
                        "short_description": "payments-api p99 4.2s (was 260ms) on /charge",
                        "cmdb_ci": "app:payments-api", "evidence": ["servicenow://INC-4821"]},
    "topology": {"nodes": [{"id": "app:payments-api", "kind": "system", "type": "app", "layer": "app"},
                           {"id": "db:payments-ora", "kind": "system", "type": "database", "layer": "database"},
                           {"id": "stor:pay-vol", "kind": "system", "type": "storage", "layer": "storage"}],
                 "edges": [{"type": "depends_on", "from": "app:payments-api", "to": "db:payments-ora"},
                           {"type": "hosted_on", "from": "db:payments-ora", "to": "stor:pay-vol"}],
                 "evidence": ["cmdb://payments-api"]},
    "change-history": {"app_changes": "none on payments-api in the window",
                       "infra_change": "storage array pay-vol RAID rebuild started 08:58 (disk 1.4.7 failed)",
                       "evidence": ["github://deploys", "netapp://events"]},
    "telemetry": {"target": "app:payments-api", "p99_ms": 4200, "baseline_ms": 260, "error_rate": "2%",
                  "evidence": ["datadog://payments-api"]},
    "metrics": {"target": "db:payments-ora", "query_p99_ms": 900, "cpu": "normal", "locks": "normal",
                "note": "DB slow but CPU/locks fine — points downstream", "evidence": ["datadog://payments-ora"]},
    "logs": {"pattern": "slow query; I/O wait dominates", "count": 1280, "evidence": ["splunk://payments-ora"]},
    "traces": {"slowest_span": "db.query", "db_time_pct": 78,
               "note": "78% of request time is DB I/O wait", "evidence": ["tempo://trace/abc"]},
    "layer-deep-dive": {"target": "stor:pay-vol", "io_wait_ms": 28, "baseline_ms": 4,
                        "raid": "degraded — rebuild in progress", "failed_disk": "1.4.7",
                        "evidence": ["netapp://pay-vol"]},
    "similar-incidents": {"related": [{"id": "INC-2980", "similarity": 0.82, "what": "storage RAID rebuild latency"}],
                          "evidence": ["servicenow://similar"]},
    "remediation-action": {"result": "failed pay-vol over to aggr02; I/O wait 28ms->4ms; p99 recovering",
                           "applied_at": "09:41", "rollback": "fail back to pay-vol once rebuild completes",
                           "evidence": ["runbook://failover"]},
    "escalation": {"result": "paged storage on-call (s.kim) via PagerDuty; acked 09:22",
                   "evidence": ["pagerduty://INC-4821"]},
    "synthetic-replay": {"journey": "/charge", "status": "green", "p99_ms": 260, "success_rate": "99.9%",
                         "evidence": ["datadog://synthetics"]},
}


class DemoAdapter:
    """Returns realistic data by the call's intent (set by the planner) — a richer stand-in than the
    plain MockAdapter for a live single-use demo. Same CapabilityAdapter interface."""

    def __init__(self, kind: ProviderKind) -> None:
        self.kind = kind

    def invoke(self, capability_id: str, input: dict) -> dict:
        intent = (input or {}).get("intent")
        data = DEMO_DATA.get(intent)
        if data is not None:
            return data
        return {"capability": capability_id, "intent": intent,
                "note": "no demo data for this intent", "evidence": []}


def build_demo_layer(cmdb_adapter=None, obs_adapter=None) -> CapabilityLayer:
    r = CapabilityRegistry()
    r.add_provider(Provider(id="cmdb", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="obs", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="bl", kind=ProviderKind.a2a_agent, trusted=True))
    r.sync_capability(DeclaredCapability(id="cmdb__topo", provider="cmdb", effect_hint=Effect.read,
                                         intents=["topology", "incident-source", "change-history",
                                                  "similar-incidents"]))
    r.sync_capability(DeclaredCapability(id="obs__tel", provider="obs", effect_hint=Effect.read,
                                         intents=["telemetry", "metrics", "logs", "traces",
                                                  "layer-deep-dive", "synthetic-replay"]))
    r.register_capability(
        DeclaredCapability(id="bl__failover", provider="bl", effect_hint=Effect.write,
                           intents=["remediation-action", "escalation"]),
        policy=CapabilityPolicy(capability_id="bl__failover", effect=Effect.write,
                                access=Access.ask, status=PolicyStatus.active),
    )
    a = AdapterRegistry()
    a.bind("cmdb", cmdb_adapter or DemoAdapter(ProviderKind.mcp_remote))   # browser-backed when provided
    a.bind("obs", obs_adapter or DemoAdapter(ProviderKind.mcp_remote))     # browser-backed when provided
    a.bind("bl", DemoAdapter(ProviderKind.a2a_agent))
    return CapabilityLayer(r, a)


def build_demo_fold() -> FoldRegistry:
    reg = FoldRegistry()
    reg.register("cmdb", TopologyFold())   # topology results -> incident graph nodes/edges
    reg.register("obs", TopologyFold())    # no-ops on non-topology results (safe .get)
    return reg
