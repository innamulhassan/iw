"""L1 — workload/runtime tier (DESIGN §3 / DESIGN-INPUT §B.2).

Deployment (desired-state controller) / ReplicaSet (generation snapshot) / Pod (running
instance) / Container (per-container facts) / Process (bare OS process) / BatchJob
(scheduled/triggered unit of work). R-G5: any time-varying attribute (running `image`,
`node_name`, replica counts) is a Fact, never a static prop.
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec

SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        type=NodeType.DEPLOYMENT,
        tier="L1",
        identity_keys=("uid",),
        static_props=("uid", "name", "namespace", "strategy"),
        fact_predicates=("image", "available_replicas", "desired_replicas", "rollout_progress"),
        event_types=("rollout_started", "rollout_complete", "rollback", "image_change"),
        discriminator=(
            "The desired-state controller owning a Service's ReplicaSets/Pods. "
            "Distinguishes from ReplicaSet (a generation snapshot) and Pod (a running "
            "instance). `image` is a Fact (R-G5), not a static prop — it changes on "
            "every deploy."
        ),
    ),
    NodeSpec(
        type=NodeType.REPLICASET,
        tier="L1",
        identity_keys=("uid",),
        static_props=("uid", "name", "namespace", "revision"),
        fact_predicates=("desired_replicas", "ready_replicas"),
        event_types=("scaled",),
        discriminator=(
            "A generation/revision snapshot owned by a Deployment. Pods REALIZES a "
            "ReplicaSet; a ReplicaSet itself does not RUNS_ON a Host."
        ),
    ),
    NodeSpec(
        type=NodeType.POD,
        tier="L1",
        identity_keys=("uid",),
        static_props=("uid", "namespace", "name", "qos_class"),
        fact_predicates=(
            "phase",
            "ready",
            "node_name",
            "cpu_utilization",
            "mem_utilization",
            "restart_count",
        ),
        event_types=("scheduled", "started", "OOMKilled", "evicted", "restarted", "terminated"),
        discriminator=(
            "A running instance realizing a ReplicaSet, scheduled onto exactly one "
            "Host via RUNS_ON. `node_name` is a Fact (R-G5), not a static prop — it "
            "changes on reschedule. If it's not an orchestrated workload instance, "
            "model as Process (bare) instead."
        ),
    ),
    NodeSpec(
        type=NodeType.CONTAINER,
        tier="L1",
        identity_keys=("pod_uid", "container_name"),
        static_props=("container_name", "image_repo"),
        fact_predicates=("image", "cpu_utilization", "mem_utilization", "restart_count"),
        event_types=("started", "OOMKilled", "restarted", "terminated"),
        discriminator=(
            "A single container within a Pod, for per-container facts finer-grained "
            "than the Pod aggregate. Use Pod alone when the fixture doesn't "
            "distinguish per-container data."
        ),
    ),
    NodeSpec(
        type=NodeType.PROCESS,
        tier="L1",
        identity_keys=("host_ref", "process_name"),
        static_props=("process_name", "executable_path"),
        fact_predicates=("cpu_utilization", "mem_utilization", "fd_count"),
        event_types=("started", "crashed", "restarted"),
        discriminator=(
            "A bare OS process on a Host with no Pod/Container orchestration above it. "
            "If it runs inside Kubernetes, model as Pod/Container instead."
        ),
    ),
    NodeSpec(
        type=NodeType.BATCH_JOB,
        tier="L1",
        identity_keys=("job_name", "schedule_id"),
        static_props=("job_name", "schedule"),
        fact_predicates=("last_duration", "last_exit_code", "backlog_size"),
        event_types=("started", "completed", "failed", "skipped"),
        discriminator=(
            "A scheduled/triggered unit of work with a bounded start/end (cron or "
            "event-triggered), not a long-running Service. Can itself be a root cause "
            "(e.g. a batch job holding a DB lock, or a leaked connection pool)."
        ),
    ),
)
