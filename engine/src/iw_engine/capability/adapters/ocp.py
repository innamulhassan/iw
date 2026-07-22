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
from ...domain.assertion import Window
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Source, Species, Stat
from ...domain.operations import AddAssertion, AddEdge, AddNode, Operation
from ..layer import CapabilityMeta

# pod_status predicate → (species, stat) for native AddAssertion emission (P1b, §9.1). phase/ready
# are open-interval STATE; node_name is identity-adjacent DESCRIPTOR; cpu/mem are READING gauges
# and restart_count a READING counter. Species/stat ride on the assertion only — the reducer's
# Fact carries neither — so this records the temporal shape without changing any graph output.
_POD_FACT_SPECIES: dict[str, tuple[Species, Stat | None]] = {
    "phase": (Species.STATE, None),
    "ready": (Species.STATE, None),
    "node_name": (Species.DESCRIPTOR, None),
    "restart_count": (Species.READING, Stat.COUNTER),
    "cpu_utilization": (Species.READING, Stat.GAUGE),
    "mem_utilization": (Species.READING, Stat.GAUGE),
}

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
    meta = CapabilityMeta(
        summary="Kubernetes / OpenShift rollout, pod, and event state",
        queries_by="k8s_workload", returns="rollout + pod status, events")

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
            # rollout facts are open-interval STATE (image / replica counts / progress — the
            # onset value can matter). No stat/window; folds to a byte-identical Fact.
            for pred in ("image", "available_replicas", "desired_replicas", "rollout_progress"):
                if pred in dep and dep[pred] is not None:
                    ops.append(AddAssertion(subject=dep_id, name=pred, value=dep[pred],
                                            species=Species.STATE, valid_from=at, observed_at=at,
                                            source=Source.OCP, source_native_name=pred))

        rollout = raw.get("rollout")
        if rollout:
            etype = _ROLLOUT_STATUS_EVENTS.get(rollout.get("status"))
            rat = rollout.get("at", at)
            if etype and rat:
                ops.append(AddAssertion(subject=dep_id, name=etype, species=Species.EVENT,
                                        occurred_at=rat, observed_at=rat,
                                        value={"reason": rollout.get("reason"),
                                               "previous_image": rollout.get("previous_image")},
                                        source=Source.OCP,
                                        source_native_name=rollout.get("status")))

        release = raw.get("release")
        if release:
            rel_props = {"release_id": release["release_id"], "version": release.get("version")}
            ops.append(AddNode(type=NodeType.RELEASE, props=rel_props))
            rel_id = registry.node_id(NodeType.RELEASE, rel_props)
            rel_at = release.get("at")
            if rel_at:
                ops.append(AddAssertion(subject=rel_id, name="released", species=Species.EVENT,
                                        occurred_at=rel_at, observed_at=rel_at,
                                        value={"version": release.get("version")},
                                        source=Source.OCP, source_native_name="released"))
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
                        species, stat = _POD_FACT_SPECIES[pred]
                        window = Window(at=at) if species is Species.READING else None
                        ops.append(AddAssertion(subject=pod_id, name=pred, value=pod[pred],
                                                species=species, stat=stat, window=window,
                                                valid_from=at, observed_at=at, source=Source.OCP,
                                                source_native_name=pred))

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
            # name is the closed-vocabulary etype; the raw k8s `reason` is the vendor's own name.
            ops.append(AddAssertion(subject=entity_id, name=etype, species=Species.EVENT,
                                    occurred_at=at, observed_at=at,
                                    value={"reason": reason, "message": ev.get("message")},
                                    source=Source.OCP, source_native_name=reason))
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
                    # etype is the closed-vocabulary name; the matched log signature is the
                    # vendor's own name for the occurrence.
                    ops.append(AddAssertion(subject=pod_id, name=etype, species=Species.EVENT,
                                            occurred_at=at, observed_at=at,
                                            value={"log_line": text}, source=Source.OCP,
                                            source_native_name=needle))
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
