"""A fully-mocked engine wired for INC-4821 — reused by the API tests (P6) and the E2E (P8).

Mock capabilities return canned INC-4821 data; a ScriptedPlanner per phase drives each loop. Builds
the whole engine with zero real sources / model — the mock-first contract.
"""
from __future__ import annotations

from engine.capability import (
    AdapterRegistry,
    CapabilityLayer,
    CapabilityRegistry,
    MockAdapter,
)
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
from engine.runtime import Engine, MultiPhasePlanner, ScriptedPlanner

from . import inc4821 as fx

_TOPO = {
    "nodes": [{"id": "app:payments-api", "kind": "system", "type": "app", "layer": "app"},
              {"id": "db:payments-ora", "kind": "system", "type": "database", "layer": "database"}],
    "edges": [{"type": "depends_on", "from": "app:payments-api", "to": "db:payments-ora"}],
    "evidence": ["cmdb://payments-api"],
}


def build_layer() -> CapabilityLayer:
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
    a.bind("cmdb", MockAdapter(ProviderKind.mcp_remote, {"cmdb__topo": _TOPO}))
    a.bind("obs", MockAdapter(ProviderKind.mcp_remote, {"obs__tel": {"nodes": [], "evidence": ["obs://y"]}}))
    a.bind("bl", MockAdapter(ProviderKind.a2a_agent, {"bl__failover": {"result": "applied; I/O 28ms→4ms"}}))
    return CapabilityLayer(r, a)


def build_planner() -> MultiPhasePlanner:
    return MultiPhasePlanner({
        "assess": ScriptedPlanner("assess", [("topology", {})], fx.ASSESS_RESULT),
        "root-cause": ScriptedPlanner("root-cause", [("traces", {})], fx.ROOT_CAUSE_RESULT),
        "remediation": ScriptedPlanner("remediation", [("remediation-action", {})], fx.REMEDIATION_RESULT),
        "verify-close": ScriptedPlanner("verify", [("synthetic-replay", {})], fx.VERIFY_RESULT),
    })


def build_fold() -> FoldRegistry:
    reg = FoldRegistry()
    reg.register("cmdb", TopologyFold())
    reg.register("obs", TopologyFold())
    return reg


def build_engine(playbook) -> Engine:
    return Engine(playbook, build_planner(), build_layer(), build_fold())
