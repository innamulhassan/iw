"""Capability test for the OCP adapter — rollout_status/pod_status/events/pod_logs fold
cleanly through the reducer (zero rejections = the adapter emits only registry-valid
types); the standalone `ocp__restart` write intent is blocked outside an approved gate,
mirroring the write-boundary test in test_capability.py."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.ocp import OcpAdapter, OcpRestartAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, NodeType
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

ROLLOUT_RAW = {
    "deployment": {
        "uid": "depl-uid-1", "name": "checkout", "namespace": "prod",
        "image": "registry/checkout@sha256:abc123",
        "available_replicas": 3, "desired_replicas": 3, "rollout_progress": 100,
        "at": "2026-07-19T14:00:00Z",
    },
    "rollout": {
        "status": "complete", "reason": "NewReplicaSetAvailable",
        "previous_image": "registry/checkout@sha256:def456",
        "at": "2026-07-19T14:00:30Z",
    },
    "release": {
        "release_id": "rel-2026-07-19-01", "version": "v4.12.0",
        "at": "2026-07-19T13:55:00Z",
    },
}

POD_STATUS_RAW = {
    "pods": [
        {
            "uid": "pod-uid-1", "name": "checkout-7f8b9-abcde", "namespace": "prod",
            "phase": "Running", "ready": True, "restart_count": 0,
            "node_name": "ip-10-0-1-23.ec2.internal",
            "cpu_utilization": 42.5, "mem_utilization": 61.0,
            "at": "2026-07-19T14:00:00Z",
        },
        {
            "uid": "pod-uid-2", "name": "checkout-7f8b9-xyz12", "namespace": "prod",
            "phase": "Failed", "ready": False, "restart_count": 7,
            "node_name": "ip-10-0-1-24.ec2.internal",
            "at": "2026-07-19T14:05:00Z",
        },
    ],
}

EVENTS_RAW = {
    "events": [
        {
            "involved_object": {"kind": "Pod", "uid": "pod-uid-2",
                                "name": "checkout-7f8b9-xyz12", "namespace": "prod"},
            "reason": "OOMKilling",
            "message": "Memory cgroup out of memory: Killed process",
            "at": "2026-07-19T14:05:30Z",
        },
        {
            "involved_object": {"kind": "Pod", "uid": "pod-uid-2",
                                "name": "checkout-7f8b9-xyz12", "namespace": "prod"},
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "at": "2026-07-19T14:06:00Z",
        },
        {
            "involved_object": {"kind": "Deployment", "uid": "depl-uid-1",
                                "name": "checkout", "namespace": "prod"},
            "reason": "ProgressDeadlineExceeded",
            "message": "replica set has timed out progressing",
            "at": "2026-07-19T14:06:30Z",
        },
    ],
}

POD_LOGS_RAW = {
    "pod": {"uid": "pod-uid-2", "name": "checkout-7f8b9-xyz12", "namespace": "prod"},
    "logs": [
        {"line": "2026-07-19T14:05:29Z INFO handling request", "at": "2026-07-19T14:05:29Z"},
        {"line": "2026-07-19T14:05:30Z FATAL OOMKilled: process exceeded memory limit",
         "at": "2026-07-19T14:05:30Z"},
        {"line": "2026-07-19T14:06:00Z WARN CrashLoopBackOff: back-off restarting failed container",
         "at": "2026-07-19T14:06:00Z"},
    ],
}


def test_ocp_rollout_status_folds_cleanly():
    layer = CapabilityLayer([OcpAdapter()])
    ops, inv = layer.invoke("rollout_status", ROLLOUT_RAW, allow_write=False)
    assert inv.provider == "ocp" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections

    dep_id = registry.node_id(NodeType.DEPLOYMENT, {"uid": "depl-uid-1"})
    rel_id = registry.node_id(NodeType.RELEASE, {"release_id": "rel-2026-07-19-01"})
    assert any(n.id == dep_id and n.type == NodeType.DEPLOYMENT for n in mat.nodes)
    assert any(n.id == rel_id and n.type == NodeType.RELEASE for n in mat.nodes)
    assert any(f.predicate == "image" and f.value == "registry/checkout@sha256:abc123"
               for f in mat.facts)
    assert any(f.predicate == "available_replicas" and f.value == 3 for f in mat.facts)
    assert any(e.type == "rollout_complete" and e.entity_ref == dep_id for e in mat.events)
    assert any(e.type == "released" and e.entity_ref == rel_id for e in mat.events)
    assert any(e.type == EdgeType.DEPLOYED_AS and e.src == rel_id and e.dst == dep_id
               for e in mat.edges)


def test_ocp_pod_status_folds_cleanly():
    layer = CapabilityLayer([OcpAdapter()])
    ops, inv = layer.invoke("pod_status", POD_STATUS_RAW, allow_write=False)
    assert inv.provider == "ocp" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections

    pod1_id = registry.node_id(NodeType.POD, {"uid": "pod-uid-1"})
    pod2_id = registry.node_id(NodeType.POD, {"uid": "pod-uid-2"})
    host1_id = registry.node_id(NodeType.HOST, {"fqdn": "ip-10-0-1-23.ec2.internal"})
    assert any(n.id == pod1_id and n.type == NodeType.POD for n in mat.nodes)
    assert any(n.id == pod2_id and n.type == NodeType.POD for n in mat.nodes)
    assert any(n.id == host1_id and n.type == NodeType.HOST for n in mat.nodes)
    assert any(f.subject_ref == pod1_id and f.predicate == "phase" and f.value == "Running"
               for f in mat.facts)
    assert any(f.subject_ref == pod2_id and f.predicate == "restart_count" and f.value == 7
               for f in mat.facts)
    assert any(e.type == EdgeType.RUNS_ON and e.src == pod1_id and e.dst == host1_id
               for e in mat.edges)


def test_ocp_events_folds_cleanly():
    layer = CapabilityLayer([OcpAdapter()])
    ops, inv = layer.invoke("events", EVENTS_RAW, allow_write=False)
    assert inv.provider == "ocp" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections

    pod_id = registry.node_id(NodeType.POD, {"uid": "pod-uid-2"})
    dep_id = registry.node_id(NodeType.DEPLOYMENT, {"uid": "depl-uid-1"})
    assert any(e.entity_ref == pod_id and e.type == "OOMKilled" for e in mat.events)
    assert any(e.entity_ref == pod_id and e.type == "restarted" for e in mat.events)
    assert any(e.entity_ref == dep_id and e.type == "rollback" for e in mat.events)


def test_ocp_pod_logs_folds_cleanly():
    layer = CapabilityLayer([OcpAdapter()])
    ops, inv = layer.invoke("pod_logs", POD_LOGS_RAW, allow_write=False)
    assert inv.provider == "ocp" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections

    pod_id = registry.node_id(NodeType.POD, {"uid": "pod-uid-2"})
    assert any(n.id == pod_id and n.type == NodeType.POD for n in mat.nodes)
    assert any(e.entity_ref == pod_id and e.type == "OOMKilled" for e in mat.events)
    assert any(e.entity_ref == pod_id and e.type == "restarted" for e in mat.events)


def test_ocp_restart_is_a_separate_write_effect_adapter_blocked_outside_gate():
    layer = CapabilityLayer([OcpRestartAdapter()])
    _, blocked = layer.invoke("ocp__restart", {}, allow_write=False)
    assert blocked.blocked and "write blocked" in blocked.reason


def test_unknown_intent_is_recorded_not_crashing():
    layer = CapabilityLayer([OcpAdapter()])
    ops, inv = layer.invoke("nonexistent_intent", {}, allow_write=False)
    assert ops == [] and inv.blocked and "no capability" in inv.reason
