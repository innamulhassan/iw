"""AppD capability-fold test — mirrors test_capability.py's pattern: invoke via
CapabilityLayer, materialize, assert ZERO rejections (the adapter emits only
registry-valid types), then assert the BT facts + exit-call-driven DEPENDS_ON edges
(JDBC->Database, HTTP->Service/ExternalService) land."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.appd import AppDAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, NodeType, Origin
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "service": {"name": "checkout-api", "env": "prod"},
    "bt": {"name": "POST /checkout"},
    "bt_metrics": [
        {"predicate": "art_p95", "value": 1250.0, "unit": "ms", "at": "2026-07-19T14:00:00Z"},
        {"predicate": "epm", "value": 340.0, "at": "2026-07-19T14:00:00Z"},
        {"predicate": "delta_vs_baseline", "value": 2.4, "at": "2026-07-19T14:00:00Z"},
    ],
    "violations": [
        {"id": "HR-9001", "rule": "response-time-95th", "severity": "warning",
         "at": "2026-07-19T14:00:00Z"},
    ],
    "snapshots": [
        {
            "id": "snap-1", "at": "2026-07-19T14:00:05Z",
            "exit_calls": [
                {"type": "JDBC", "db_id": "orders-db", "engine": "postgres",
                 "at": "2026-07-19T14:00:05Z"},
                {"type": "HTTP", "target_service": "payments-api", "target_env": "prod",
                 "at": "2026-07-19T14:00:06Z"},
                {"type": "HTTP", "target_external": "stripe", "vendor": "Stripe",
                 "at": "2026-07-19T14:00:07Z"},
            ],
        },
    ],
    "flowmap": [
        {
            "service": {"name": "payments-api", "env": "prod"},
            "exit_calls": [
                {"type": "HTTP", "target_service": "inventory-api", "target_env": "prod",
                 "at": "2026-07-19T13:59:00Z"},
            ],
        },
    ],
    "traces": [
        {"trace_id": "tr-123", "at": "2026-07-19T14:00:08Z", "duration_ms": 1800,
         "error": False},
    ],
}


def test_appd_normalize_folds_cleanly():
    layer = CapabilityLayer([AppDAdapter()])
    ops, inv = layer.invoke("bt_health", RAW, allow_write=False)
    assert inv.provider == "appd" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections   # adapter emits only registry-valid types

    svc = registry.node_id(NodeType.SERVICE, {"service_name": "checkout-api", "env": "prod"})
    bt = registry.node_id(NodeType.BUSINESS_TRANSACTION,
                          {"service_name": "checkout-api", "bt_name": "POST /checkout"})
    db = registry.node_id(NodeType.DATABASE, {"db_id": "orders-db", "engine": "postgres"})
    payments = registry.node_id(NodeType.SERVICE, {"service_name": "payments-api", "env": "prod"})
    stripe = registry.node_id(NodeType.EXTERNAL_SERVICE,
                              {"service_name": "stripe", "vendor": "Stripe"})
    inventory = registry.node_id(NodeType.SERVICE, {"service_name": "inventory-api", "env": "prod"})

    node_ids = {n.id for n in mat.nodes}
    assert {svc, bt, db, payments, stripe, inventory} <= node_ids

    # bt_health facts land on the BusinessTransaction node
    assert any(f.subject_ref == bt and f.predicate == "art_p95" and f.value == 1250.0
               for f in mat.facts)
    # P2: `epm` with no unit is the errors-per-minute sense of the 1->N split (errors_per_min vs
    # calls_per_min); the reducer canonicalizes it and keeps the native spelling.
    assert any(f.subject_ref == bt and f.predicate == "errors_per_min" and f.value == 340.0
               and f.source_native_name == "epm" for f in mat.facts)
    assert any(f.subject_ref == bt and f.predicate == "delta_vs_baseline" and f.value == 2.4
               for f in mat.facts)

    # healthrule_violations -> Alert fired + FIRED_ON the Service
    assert any(n.type == NodeType.ALERT for n in mat.nodes)
    assert any(e.type == "fired" for e in mat.events)
    assert any(e.type == EdgeType.FIRED_ON and e.dst == svc for e in mat.edges)

    # get_snapshots exit-calls: JDBC -> Database, HTTP -> Service / ExternalService,
    # all DEPENDS_ON dependent->provider, origin=discovered (telemetry, not CMDB)
    depends_on = [e for e in mat.edges if e.type == EdgeType.DEPENDS_ON]
    assert any(e.src == svc and e.dst == db and e.origin == Origin.DISCOVERED for e in depends_on)
    assert any(e.src == svc and e.dst == payments and e.origin == Origin.DISCOVERED
               for e in depends_on)
    assert any(e.src == svc and e.dst == stripe and e.origin == Origin.DISCOVERED
               for e in depends_on)

    # flowmap hop: payments-api -> inventory-api
    assert any(e.src == payments and e.dst == inventory for e in depends_on)

    # fetch_traces -> trace_captured event on the BT
    assert any(e.type == "trace_captured" and e.entity_ref == bt for e in mat.events)


def test_appd_intents_registered():
    layer = CapabilityLayer([AppDAdapter()])
    for intent in ("bt_health", "get_snapshots", "healthrule_violations", "flowmap", "fetch_traces"):
        assert layer.resolve(intent) is not None
