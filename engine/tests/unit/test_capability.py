"""Capability-layer tests — intent resolution, normalize->ops folding cleanly through the
reducer (zero rejections = the adapter emits only registry-valid types), and the
write-boundary (a write cannot execute outside an approved gate)."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.prometheus import PrometheusAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, Effect, NodeType
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "service": {"name": "payments-api", "env": "prod"},
    "alerts": [{"id": "ALT-1", "alertname": "HighErrorRate",
                "at": "2026-07-19T14:00:00Z", "state": "firing"}],
    "metrics": [{"predicate": "red_errors", "value": 0.4,
                 "at": "2026-07-19T14:00:00Z", "reliability": 0.97}],
}


def test_prometheus_normalize_folds_cleanly():
    layer = CapabilityLayer([PrometheusAdapter()])
    ops, inv = layer.invoke("fetch_metrics", RAW, allow_write=False)
    assert inv.provider == "prometheus" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections            # adapter emits only registry-valid types
    svc = registry.node_id(NodeType.SERVICE, {"service_name": "payments-api", "env": "prod"})
    assert any(n.id == svc for n in mat.nodes)
    assert any(f.predicate == "red_errors" and f.value == 0.4 for f in mat.facts)
    assert any(e.type == EdgeType.FIRED_ON for e in mat.edges)


def test_unknown_intent_is_recorded_not_crashing():
    layer = CapabilityLayer([PrometheusAdapter()])
    ops, inv = layer.invoke("nonexistent_intent", {}, allow_write=False)
    assert ops == [] and inv.blocked and "no capability" in inv.reason


def test_write_blocked_outside_gate():
    class _Fix:
        provider = "ocp"
        intents = frozenset({"apply_mitigation"})
        effect = Effect.WRITE

        def normalize(self, raw):
            return []

    layer = CapabilityLayer([_Fix()])
    _, blocked = layer.invoke("apply_mitigation", {}, allow_write=False)
    assert blocked.blocked and "write blocked" in blocked.reason
    _, ok = layer.invoke("apply_mitigation", {}, allow_write=True)
    assert not ok.blocked
