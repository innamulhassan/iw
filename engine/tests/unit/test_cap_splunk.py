"""Splunk adapter — capability-layer test, same pattern as test_capability.py: invoke
via CapabilityLayer, materialize, assert ZERO rejections (adapter emits only
registry-valid types), then assert the expected nodes/facts/edges landed."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.splunk import SplunkAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, NodeType
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "service": {"name": "checkout-api", "env": "prod"},
    "errors": [
        {
            # NPE / exception example — emitted by a specific Pod
            "signature_hash": "sig-a1b2c3d4",
            "exception_class": "NullPointerException",
            "message": 'Cannot invoke "TaxCalculator.compute()" because "calc" is null',
            "file_line": "TaxCalculator.java:88",
            "trace_id": "trc-9f21c7",
            "count": 42,
            "first_seen": "2026-07-19T13:50:00Z",
            "last_seen": "2026-07-19T14:05:00Z",
            "_time": "2026-07-19T14:05:00Z",
            "emitted_by": {"kind": "pod", "uid": "pod-checkout-7d9f4c8b7-xk2p1"},
        },
        {
            # second signature, falls back to the raw's top-level default service
            "signature_hash": "sig-e5f6a7b8",
            "exception_class": "TimeoutException",
            "message": "Read timed out after 5000ms calling inventory-api",
            "file_line": "InventoryClient.java:142",
            "trace_id": "trc-1a2b3c",
            "count": 7,
            "first_seen": "2026-07-19T14:00:00Z",
            "last_seen": "2026-07-19T14:03:00Z",
            "_time": "2026-07-19T14:03:00Z",
        },
    ],
    "fw_denies": [
        {
            # fw-deny example — action=blocked folds into a deny_count fact
            "rule_id": "fw-egress-77",
            "action": "blocked",
            "direction": "outbound",
            "proto": "tcp",
            "port_range": "443",
            "src": "10.0.4.0/24",
            "dst": "203.0.113.9/32",
            "deny_count": 128,
            "_time": "2026-07-19T09:12:00Z",
        },
        {
            # allowed traffic on the same rule must NOT contribute a deny fact
            "rule_id": "fw-egress-77",
            "action": "allowed",
            "_time": "2026-07-19T09:05:00Z",
        },
    ],
}


def test_splunk_normalize_folds_cleanly():
    layer = CapabilityLayer([SplunkAdapter()])
    ops, inv = layer.invoke("search_errors", RAW, allow_write=False)
    assert inv.provider == "splunk" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections            # adapter emits only registry-valid types

    pod_id = registry.node_id(NodeType.POD, {"uid": "pod-checkout-7d9f4c8b7-xk2p1"})
    svc_id = registry.node_id(NodeType.SERVICE, {"service_name": "checkout-api", "env": "prod"})
    sig1_id = registry.node_id(NodeType.ERROR_SIGNATURE, {"signature_hash": "sig-a1b2c3d4"})
    sig2_id = registry.node_id(NodeType.ERROR_SIGNATURE, {"signature_hash": "sig-e5f6a7b8"})
    rule_id = registry.node_id(NodeType.FIREWALL_RULE, {"rule_id": "fw-egress-77"})

    # nodes: pod, service, both error signatures, the firewall rule
    node_ids = {n.id for n in mat.nodes}
    assert {pod_id, svc_id, sig1_id, sig2_id, rule_id} <= node_ids

    # ErrorSignature node props carry the NPE example
    sig1 = next(n for n in mat.nodes if n.id == sig1_id)
    assert sig1.props["exception_class"] == "NullPointerException"
    assert sig1.props["file_line"] == "TaxCalculator.java:88"

    # count / last_seen facts on both signatures
    assert any(f.subject_ref == sig1_id and f.predicate == "count" and f.value == 42
               for f in mat.facts)
    assert any(f.subject_ref == sig1_id and f.predicate == "last_seen"
               and f.value == "2026-07-19T14:05:00Z" for f in mat.facts)
    assert any(f.subject_ref == sig2_id and f.predicate == "count" and f.value == 7
               for f in mat.facts)

    # trace_id carried as evidence on the count fact (AppD join key)
    count_fact = next(f for f in mat.facts if f.subject_ref == sig1_id and f.predicate == "count")
    assert any(e.kind == "trace_id" and e.ref == "trc-9f21c7" for e in count_fact.evidence)

    # EMITTED edges: Pod -> sig1, Service -> sig2
    assert any(e.type == EdgeType.EMITTED and e.src == pod_id and e.dst == sig1_id
               for e in mat.edges)
    assert any(e.type == EdgeType.EMITTED and e.src == svc_id and e.dst == sig2_id
               for e in mat.edges)

    # fw-deny example: exactly one deny_count fact, value from the blocked entry only
    deny_facts = [f for f in mat.facts if f.subject_ref == rule_id and f.predicate == "deny_count"]
    assert len(deny_facts) == 1
    assert deny_facts[0].value == 128

    rule_node = next(n for n in mat.nodes if n.id == rule_id)
    assert rule_node.props["direction"] == "outbound"


def test_splunk_all_intents_route_through_capability_layer():
    layer = CapabilityLayer([SplunkAdapter()])
    for intent in SplunkAdapter.intents:
        ops, inv = layer.invoke(intent, RAW, allow_write=False)
        assert inv.provider == "splunk" and not inv.blocked
        mat = materialize(ops, 1, Graph(), Tunables())
        assert mat.rejections == []
