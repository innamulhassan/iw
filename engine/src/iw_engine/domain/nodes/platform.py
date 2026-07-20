"""L2 — platform/orchestration tier (DESIGN §3 / DESIGN-INPUT §B.2).

Namespace / Cluster / Host (USE metrics attach here) / ConfigItem (the generic
platform-level CMDB CI shape, one step before the GENERIC_CI escape hatch).
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec

SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        type=NodeType.NAMESPACE,
        tier="L2",
        identity_keys=("cluster_ref", "name"),
        static_props=("name", "cluster_ref"),
        fact_predicates=("pod_count", "resource_quota_util"),
        event_types=("created", "quota_exceeded"),
        discriminator=(
            "A logical partition within a Cluster grouping Deployments/Pods; workloads "
            "relate to it via CONTAINS/MEMBER_OF."
        ),
    ),
    NodeSpec(
        type=NodeType.CLUSTER,
        tier="L2",
        identity_keys=("cluster_id",),
        static_props=("cluster_id", "name", "region", "provider"),
        fact_predicates=("node_count", "capacity_util"),
        event_types=("upgraded", "degraded"),
        discriminator=(
            "The top-level orchestration platform (e.g. a K8s cluster) that Hosts and "
            "Namespaces belong to."
        ),
    ),
    NodeSpec(
        type=NodeType.HOST,
        tier="L2",
        identity_keys=("fqdn",),
        static_props=("fqdn", "asset_id", "cpu_cores", "mem_gb", "region"),
        fact_predicates=(
            "cpu_utilization",
            "mem_utilization",
            "disk_utilization",
            "net_utilization",
            "cpu_saturation",
            "disk_saturation",
        ),
        event_types=("reboot", "disk_fail", "NotReady", "recovered"),
        discriminator=(
            "A physical/virtual machine — USE metrics (util+saturation) attach here. "
            "Pods RUNS_ON a Host; a Host does not itself RUNS_ON anything (the bottom "
            "of the runtime/platform spine besides Cluster)."
        ),
    ),
    NodeSpec(
        type=NodeType.CONFIG_ITEM,
        tier="L2",
        identity_keys=("ci_id",),
        static_props=("ci_id", "sys_class_name", "name"),
        fact_predicates=(),
        event_types=("attribute_changed",),
        discriminator=(
            "The generic CMDB CI shape for a platform/infra item with no more specific "
            "typed NodeType — prefer this over GENERIC_CI whenever `sys_class_name` "
            "maps to a recognizable platform concept; fall back to GENERIC_CI only when "
            "even that mapping is unclear."
        ),
    ),
)
