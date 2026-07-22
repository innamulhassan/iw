"""Capability test for the OCP adapter — rollout_status/pod_status/events/pod_logs fold
cleanly through the reducer (zero rejections = the adapter emits only registry-valid
types); the `ocp__restart` write intent rides the SAME adapter with a PER-INTENT effect
(part4-capability §1 — the split OcpRestartAdapter workaround class is retired) and is
blocked outside an approved gate, mirroring the write-boundary test in test_capability.py."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.ocp import OcpAdapter
from iw_engine.domain import registry
from iw_engine.domain.catalog import render_tools, tool_intents
from iw_engine.domain.enums import EdgeType, Effect, NodeType
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


def test_ocp_restart_is_a_per_intent_write_on_the_same_adapter():
    """The ONE OcpAdapter hosts four reads and the `ocp__restart` write via the `effects`
    override; the gate resolves each intent independently — reads flow ungated, the restart
    is blocked outside an approved gate and resolves inside one (folding zero ops from an
    ack payload, which is a clean-empty, not an error)."""
    layer = CapabilityLayer([OcpAdapter()])
    _, read_inv = layer.invoke("pod_status", POD_STATUS_RAW, allow_write=False)
    assert not read_inv.blocked and read_inv.effect is Effect.READ

    _, blocked = layer.invoke("ocp__restart", {}, allow_write=False)
    assert blocked.blocked and "write blocked" in blocked.reason
    assert blocked.effect is Effect.WRITE

    ops, ok = layer.invoke("ocp__restart", {}, allow_write=True)
    assert not ok.blocked and ok.effect is Effect.WRITE and ops == []


def test_catalog_renders_the_ocp_write_per_intent():
    """The tool list mirrors the gate: `ocp__restart` never leaks into the read set or the
    read block; with `include_writes` it renders under the provider's human-gated block."""
    adapters = [OcpAdapter()]
    assert "ocp__restart" not in tool_intents(adapters)
    assert "ocp__restart" in tool_intents(adapters, include_writes=True)

    read_only = render_tools(adapters)
    assert "ocp__restart" not in read_only and "pod_status" in read_only
    with_writes = render_tools(adapters, include_writes=True)
    assert "ocp [WRITE — human-gated]" in with_writes
    # the write block carries ONLY the write intent; the reads stay in the read block
    write_block = with_writes.split("[WRITE — human-gated]")[1]
    assert "ocp__restart" in write_block and "pod_status" not in write_block


def test_unknown_intent_is_recorded_not_crashing():
    layer = CapabilityLayer([OcpAdapter()])
    ops, inv = layer.invoke("nonexistent_intent", {}, allow_write=False)
    assert ops == [] and inv.blocked and "no capability" in inv.reason
