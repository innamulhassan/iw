"""OpenShift/OCP adapter — rollout status, pod status, cluster events, pod logs. Follows
the PrometheusAdapter template exactly: a `provider`, an `intents` set, an `effect`, and a
pure `normalize(raw)` that maps the tool's raw JSON shape into typed Operations folding
into the incident graph (DESIGN-INPUT-v1.md §E.2 OCP row + §B.2/B.3 catalog).

Graph fold:
  - Deployment node (identity `uid`) + rollout facts (image/available_replicas/
    desired_replicas/rollout_progress) + rollout events (rollout_started/rollout_complete/
    rollback), from `rollout_status`.
  - Release node (identity `release_id`) `DEPLOYED_AS` -> Deployment, when a release is
    given alongside the rollout (`rollout_status` with a `release` block).
  - Pod node (identity `uid`) + phase facts (phase/ready/node_name/restart_count/
    cpu_utilization/mem_utilization) + `RUNS_ON` -> Host, from `pod_status`.
  - Cluster events (OOMKilling/BackOff/etc, on Pod or Deployment involvedObjects) folded
    to the closed event vocabulary (OOMKilled/restarted/terminated/... for Pod,
    rollout_started/rollout_complete/rollback for Deployment), from `events`.
  - Pod log lines scanned for OOMKilled/CrashLoopBackOff signatures and folded to the same
    Pod events, from `pod_logs` — a second, independent path to the same signal.
"""
from __future__ import annotations

from ...domain import registry
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Source
from ...domain.operations import AddEdge, AddEvent, AddFact, AddNode, Operation

# k8s Event `reason` -> this registry's closed Pod event vocabulary
# (pod event_types: scheduled, started, OOMKilled, evicted, restarted, terminated)
_POD_REASON_EVENTS = {
    "OOMKilling": "OOMKilled",
    "OOMKilled": "OOMKilled",
    "BackOff": "restarted",            # CrashLoopBackOff surfaces as repeated BackOff events
    "CrashLoopBackOff": "restarted",
    "Killing": "terminated",
    "Evicted": "evicted",
    "Scheduled": "scheduled",
    "Started": "started",
}

# k8s Event `reason` -> this registry's closed Deployment event vocabulary
# (deployment event_types: rollout_started, rollout_complete, rollback, image_change)
_DEPLOYMENT_REASON_EVENTS = {
    "ScalingReplicaSet": "rollout_started",
    "NewReplicaSetAvailable": "rollout_complete",
    "DeploymentRollback": "rollback",
    "ProgressDeadlineExceeded": "rollback",
}

# rollout_status `rollout.status` -> Deployment event vocabulary
_ROLLOUT_STATUS_EVENTS = {
    "started": "rollout_started",
    "complete": "rollout_complete",
    "rollback": "rollback",
}

# pod_logs line signatures -> Pod event vocabulary (first match wins per line)
_LOG_SIGNATURES = (
    ("OOMKilled", "OOMKilled"),
    ("OOMKilling", "OOMKilled"),
    ("CrashLoopBackOff", "restarted"),
)


class OcpAdapter:
    provider = "ocp"
    intents = frozenset({"rollout_status", "pod_status", "events", "pod_logs"})
    effect = Effect.READ
    binding = Binding.MCP   # OpenShift ships a first-party MCP server (read-only default)

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        ops += self._fold_rollout_status(raw)
        ops += self._fold_pod_status(raw)
        ops += self._fold_events(raw)
        ops += self._fold_pod_logs(raw)
        return ops

    # ── rollout_status ────────────────────────────────────────────────────────
    def _fold_rollout_status(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        dep = raw.get("deployment")
        if not dep:
            return ops

        dep_props = {"uid": dep["uid"], "name": dep.get("name"), "namespace": dep.get("namespace")}
        ops.append(AddNode(type=NodeType.DEPLOYMENT, props=dep_props))
        dep_id = registry.node_id(NodeType.DEPLOYMENT, dep_props)

        at = dep.get("at")
        if at:
            for pred in ("image", "available_replicas", "desired_replicas", "rollout_progress"):
                if pred in dep and dep[pred] is not None:
                    ops.append(AddFact(subject=dep_id, predicate=pred, value=dep[pred],
                                       valid_from=at, observed_at=at, source=Source.OCP,
                                       source_reliability=0.99))

        rollout = raw.get("rollout")
        if rollout:
            etype = _ROLLOUT_STATUS_EVENTS.get(rollout.get("status"))
            rat = rollout.get("at", at)
            if etype and rat:
                ops.append(AddEvent(entity=dep_id, type=etype, occurred_at=rat, observed_at=rat,
                                    payload={"reason": rollout.get("reason"),
                                             "previous_image": rollout.get("previous_image")},
                                    source=Source.OCP))

        release = raw.get("release")
        if release:
            rel_props = {"release_id": release["release_id"], "version": release.get("version")}
            ops.append(AddNode(type=NodeType.RELEASE, props=rel_props))
            rel_id = registry.node_id(NodeType.RELEASE, rel_props)
            rel_at = release.get("at")
            if rel_at:
                ops.append(AddEvent(entity=rel_id, type="released", occurred_at=rel_at,
                                    observed_at=rel_at, payload={"version": release.get("version")},
                                    source=Source.OCP))
            ops.append(AddEdge(type=EdgeType.DEPLOYED_AS, src=rel_id, dst=dep_id))

        return ops

    # ── pod_status ────────────────────────────────────────────────────────────
    def _fold_pod_status(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        for pod in raw.get("pods", []):
            pod_props = {"uid": pod["uid"], "name": pod.get("name"), "namespace": pod.get("namespace")}
            ops.append(AddNode(type=NodeType.POD, props=pod_props))
            pod_id = registry.node_id(NodeType.POD, pod_props)

            at = pod.get("at")
            if at:
                for pred in ("phase", "ready", "node_name", "restart_count",
                             "cpu_utilization", "mem_utilization"):
                    if pred in pod and pod[pred] is not None:
                        ops.append(AddFact(subject=pod_id, predicate=pred, value=pod[pred],
                                           valid_from=at, observed_at=at, source=Source.OCP,
                                           source_reliability=0.99))

            node_name = pod.get("node_name")
            if node_name:
                host_props = {"fqdn": node_name}
                ops.append(AddNode(type=NodeType.HOST, props=host_props))
                host_id = registry.node_id(NodeType.HOST, host_props)
                ops.append(AddEdge(type=EdgeType.RUNS_ON, src=pod_id, dst=host_id))
        return ops

    # ── events ────────────────────────────────────────────────────────────────
    def _fold_events(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        for ev in raw.get("events", []):
            obj = ev.get("involved_object", {})
            kind = obj.get("kind")
            reason = ev.get("reason")
            at = ev.get("at")
            if not at or "uid" not in obj:
                continue

            if kind == "Pod":
                node_type, reason_map = NodeType.POD, _POD_REASON_EVENTS
            elif kind == "Deployment":
                node_type, reason_map = NodeType.DEPLOYMENT, _DEPLOYMENT_REASON_EVENTS
            else:
                continue  # out of scope for this adapter's graph fold

            props = {"uid": obj["uid"], "name": obj.get("name"), "namespace": obj.get("namespace")}
            ops.append(AddNode(type=node_type, props=props))
            entity_id = registry.node_id(node_type, props)

            etype = reason_map.get(reason)
            if etype is None:
                continue  # unmapped k8s reason — no registry-valid event to fold
            ops.append(AddEvent(entity=entity_id, type=etype, occurred_at=at, observed_at=at,
                                payload={"reason": reason, "message": ev.get("message")},
                                source=Source.OCP))
        return ops

    # ── pod_logs ──────────────────────────────────────────────────────────────
    def _fold_pod_logs(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        pod = raw.get("pod")
        logs = raw.get("logs")
        if not pod or not logs:
            return ops

        pod_props = {"uid": pod["uid"], "name": pod.get("name"), "namespace": pod.get("namespace")}
        ops.append(AddNode(type=NodeType.POD, props=pod_props))
        pod_id = registry.node_id(NodeType.POD, pod_props)

        for line in logs:
            text = line.get("line", "")
            at = line.get("at")
            if not at:
                continue
            for needle, etype in _LOG_SIGNATURES:
                if needle in text:
                    ops.append(AddEvent(entity=pod_id, type=etype, occurred_at=at, observed_at=at,
                                        payload={"log_line": text}, source=Source.OCP))
                    break  # one event per line
        return ops


class OcpRestartAdapter:
    """Write-effect placeholder for the `ocp__restart` intent (DESIGN-INPUT-v1.md §E.2:
    "`ocp__restart` **write**->gate"). Deliberately kept OUT of `OcpAdapter.intents`: the
    CapabilityLayer applies a single `Effect` per adapter across its WHOLE intents set
    (capability/layer.py: `CapabilityLayer.invoke` checks `a.effect == Effect.WRITE` once
    per resolved adapter) — folding a write intent into `OcpAdapter` (effect=Effect.READ)
    would silently grant `ocp__restart` read-effect and skip the human-approval write
    gate. So this stays a separate adapter with its own effect, per E.2 ruling (4)
    ("reconcile ... with the domain Effect enum").

    TODO: normalize() is out of scope here — a restart is an ACTION (rollout restart /
    pod delete against the live OCP API), not a read-and-fold; there's no raw tool
    payload to fold into graph ops beyond perhaps recording that the restart happened
    (an AddEvent on the target Deployment/Pod once the write path executes). Implement
    once the write-gate gets its own approved-mitigation flow.
    """

    provider = "ocp"
    intents = frozenset({"ocp__restart"})
    effect = Effect.WRITE
    binding = Binding.A2A   # remediation delegation — reserved write-side binding (§C)

    def normalize(self, raw: dict) -> list[Operation]:
        raise NotImplementedError("ocp__restart normalize is out of scope for this adapter")
