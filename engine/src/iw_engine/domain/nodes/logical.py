"""L0 — logical/business tier (DESIGN §3 / DESIGN-INPUT §B.2).

Application (business grouping) / Service (independently deployable unit) / Component
(internal module, no independent deploy) / ApiEndpoint (per-route RED) / Team (owner).
DESIGN §2.1 R-G3: keep these four distinct L0 types (drop `Microservice` — it IS a
Service); each carries a machine-readable discriminator so the LLM can choose.
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec

SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        type=NodeType.APPLICATION,
        tier="L0",
        identity_keys=("name",),
        static_props=("name", "description"),
        fact_predicates=("business_criticality",),
        event_types=("decommissioned",),
        discriminator=(
            "A business/product grouping that OWNS one or more Services; it is never "
            "itself deployed or called. If the thing can be deployed or invoked "
            "directly, it is a Service, not an Application."
        ),
    ),
    NodeSpec(
        type=NodeType.SERVICE,
        tier="L0",
        identity_keys=("service_name", "env"),
        # `service_name`/`env` are the identity; the rest are the CI's per-TOOL identifiers,
        # resolved from the incident's CMDB CI, so each tool is queried by ITS OWN id (AppD by
        # app_id, git by repo, the platform by k8s_workload, ServiceNow by sys_id) — not by
        # reusing the display name everywhere. This is the identity backbone of a real cross-tool
        # investigation ("get the app_id from the incident, then query AppD with it").
        static_props=("service_name", "env", "repo", "language",
                      "app_id", "sys_id", "k8s_workload"),
        fact_predicates=(
            "tier",
            "slo_target",
            "red_rate",
            "red_errors",
            "red_latency_p50",
            "red_latency_p99",
            "degraded",
        ),
        event_types=("alert_fired", "deployed", "scaled", "degraded_started", "degraded_cleared"),
        discriminator=(
            "An independently deployable unit with its own Deployment, ApiEndpoints, "
            "and RED metrics. If it cannot be deployed independently (no Deployment of "
            "its own), it is a Component. If it is a business grouping that owns "
            "Services rather than being deployed itself, it is an Application. "
            "`tier`/`slo_target` are Facts (time-varying, R-G5), never static props."
        ),
    ),
    NodeSpec(
        type=NodeType.COMPONENT,
        tier="L0",
        identity_keys=("service_name", "env", "component_name"),
        static_props=("component_name", "language", "module_path"),
        fact_predicates=("error_rate", "latency_p99"),
        event_types=("exception_raised",),
        discriminator=(
            "An internal module of a Service with no independent Deployment/Pod of its "
            "own. If the fixture data shows it with its own Deployment, it is a "
            "Service, not a Component."
        ),
    ),
    NodeSpec(
        type=NodeType.API_ENDPOINT,
        tier="L0",
        identity_keys=("service_name", "env", "method", "route_template"),
        static_props=("method", "route_template"),
        fact_predicates=("red_rate", "red_errors", "red_latency_p99", "status_code_dist"),
        event_types=("5xx_spike", "timeout_burst"),
        discriminator=(
            "A specific method+route_template on a Service, carrying finer-grained RED "
            "than the Service aggregate. Use the Service node when the fixture has no "
            "route_template."
        ),
    ),
    NodeSpec(
        type=NodeType.TEAM,
        tier="L0",
        identity_keys=("team_name",),
        static_props=("team_name", "contact_channel"),
        fact_predicates=(),
        event_types=(),
        discriminator=(
            "The human/organizational owner, referenced via OWNS/MEMBER_OF edges; "
            "never itself a runtime, symptom, or causal node."
        ),
    ),
)
