"""CMDB adapter — the topology backbone. Follows the Prometheus reference adapter's shape
exactly: `provider`, `intents`, `effect`, and a pure `normalize(raw)`. Unlike the
telemetry adapters, CMDB contributes no facts/events — it is the trusted, `declared`
structural spine (typed CI nodes + DEPENDS_ON/RUNS_ON/CONNECTS_TO/HOSTED_ON edges) that
every other tool's facts/events attach on top of (DESIGN-INPUT §E.2).

Raw shape (`get_dependencies`/`impact_analysis`/`seed_graph` all return the same
relationship-record list; `get_ci_class`/`find_ci_by_attr` return bare CI records — one
`normalize` handles both via the shared `_ensure_node` fold):

    {
      "env": "prod",                                  # default Service env
      "dependencies": [
        {"parent": "payments-api", "parent_type": "cmdb_ci_service",
         "child": "payments-db", "child_type": "cmdb_ci_database",
         "rel_type": "Depends on::Used by"},
        ...
      ],
      "cis": [{"name": "payments-api", "sys_class_name": "cmdb_ci_service"}, ...],
      "ci_attrs": {"payments-db": {"engine": "postgresql", ...}, ...},
    }

`sys_class_name` is the step-3 dispatch key (DESIGN-INPUT §E.2): mapped through
`_CI_CLASS_MAP` to a typed NodeType, falling back to the registry's own generic
`ConfigItem` CI shape for any class this adapter doesn't recognise (never GENERIC_CI —
DESIGN §... "prefer ConfigItem over GenericCi whenever sys_class_name maps to a
recognizable platform concept").

`rel_type` (a raw CMDB relationship-type string, e.g. "Runs on::Hosts") is mapped to
whichever structural EdgeType is actually registry-legal for the concrete
(parent_type, child_type) pair — `_edge_type_for` tries the semantically-preferred
EdgeType first (RUNS_ON before its HOSTED_ON near-synonym, since RUNS_ON is reserved
for workload instances per the registry) and falls back to the next legal candidate;
a relationship with no legal representation is dropped rather than emitted illegally
(the adapter's own guard against the reducer's rejection path — CRITICAL per the
closed registry, DESIGN §2.1 R-G1)."""
from __future__ import annotations

from ...domain import registry
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Origin, Source
from ...domain.operations import AddEdge, AddNode, Operation
from ..layer import CapabilityMeta

# sys_class_name -> typed NodeType (the step-3 dispatch key, §E.2). Unrecognised classes
# fall back to NodeType.CONFIG_ITEM (the registry's generic CMDB CI shape) in normalize().
_CI_CLASS_MAP: dict[str, NodeType] = {
    "cmdb_ci_service": NodeType.SERVICE,
    "cmdb_ci_business_app": NodeType.SERVICE,
    "cmdb_ci_database": NodeType.DATABASE,
    "cmdb_ci_db_instance": NodeType.DATABASE,
    "cmdb_ci_server": NodeType.HOST,
    "cmdb_ci_linux_server": NodeType.HOST,
    "cmdb_ci_win_server": NodeType.HOST,
    "cmdb_ci_msgqueue": NodeType.MESSAGE_QUEUE,
    "cmdb_ci_message_queue": NodeType.MESSAGE_QUEUE,
    "cmdb_ci_lb": NodeType.LOAD_BALANCER,
    "cmdb_ci_load_balancer": NodeType.LOAD_BALANCER,
    "cmdb_ci_batch_job": NodeType.BATCH_JOB,
    "cmdb_ci_network_segment": NodeType.NETWORK_SEGMENT,
}

# rel_type keyword (matched against the text before "::") -> ordered EdgeType candidates,
# most-specific first. The first candidate that is registry-legal for the concrete
# (parent_type, child_type) pair wins (see _edge_type_for).
_REL_KEYWORDS: tuple[tuple[str, tuple[EdgeType, ...]], ...] = (
    ("depend", (EdgeType.DEPENDS_ON,)),
    ("use", (EdgeType.DEPENDS_ON,)),
    ("run", (EdgeType.RUNS_ON, EdgeType.HOSTED_ON)),
    ("host", (EdgeType.HOSTED_ON, EdgeType.RUNS_ON)),
    ("connect", (EdgeType.CONNECTS_TO, EdgeType.HOSTED_ON)),
)


# CI-wide descriptive attributes CMDB carries on every class (ownership + version), folded
# onto whatever typed node the CI maps to — only when present, so a thin ci_attrs entry mints
# the identical node (finish-completeness: "CI attributes — owner, environment, version").
_SHARED_CI_ATTRS: tuple[str, ...] = ("owner", "version")


def _props_for(ntype: NodeType, name: str, sys_class_name: str, env: str, attrs: dict) -> dict:
    """Build the identity + static props for a CI, keyed off its typed NodeType. Ownership +
    version descriptors (`_SHARED_CI_ATTRS`) are folded on top for every class when supplied."""
    if ntype is NodeType.SERVICE:
        base = {"service_name": name, "env": attrs.get("env", env),
                **{k: attrs[k] for k in ("repo", "language") if k in attrs}}
    elif ntype is NodeType.DATABASE:
        keys = ("engine", "ha_role", "endpoint")
        base = {"db_id": name, **{k: attrs[k] for k in keys if k in attrs}}
    elif ntype is NodeType.HOST:
        keys = ("asset_id", "cpu_cores", "mem_gb", "region")
        base = {"fqdn": name, **{k: attrs[k] for k in keys if k in attrs}}
    elif ntype is NodeType.MESSAGE_QUEUE:
        base = {"topic_id": name, **{k: attrs[k] for k in ("broker", "partitions") if k in attrs}}
    elif ntype is NodeType.LOAD_BALANCER:
        base = {"lb_id": name, "name": name}
    elif ntype is NodeType.BATCH_JOB:
        base = {"job_name": name, "schedule_id": attrs.get("schedule_id", "adhoc"),
                **({"schedule": attrs["schedule"]} if "schedule" in attrs else {})}
    elif ntype is NodeType.NETWORK_SEGMENT:
        base = {"segment_id": name, **{k: attrs[k] for k in ("cidr", "vlan") if k in attrs}}
    else:
        # escape valve: the generic CMDB CI shape (registry docstring: "prefer this over
        # GENERIC_CI whenever sys_class_name maps to a recognizable platform concept").
        base = {"ci_id": name, "sys_class_name": sys_class_name, "name": name}
    return {**base, **{k: attrs[k] for k in _SHARED_CI_ATTRS if k in attrs}}


def _edge_type_for(rel_type: str, src_t: NodeType, dst_t: NodeType) -> EdgeType | None:
    """Map a raw rel_type string to the registry-legal EdgeType for this concrete pair,
    or None if the relationship has no legal graph representation (dropped, not forced)."""
    head = rel_type.split("::")[0].strip().lower()
    for kw, candidates in _REL_KEYWORDS:
        if kw in head:
            for cand in candidates:
                if registry.edge_allowed(cand, src_t, dst_t):
                    return cand
            return None
    return None


class CmdbAdapter:
    provider = "cmdb"
    intents = frozenset({
        "get_dependencies",
        "impact_analysis",
        "get_ci_class",
        "find_ci_by_attr",
        "seed_graph",
    })
    effect = Effect.READ
    binding = Binding.MCP   # served via the ServiceNow CMDB MCP surface
    meta = CapabilityMeta(
        summary="The declared dependency topology and CI identity",
        queries_by="service_name", returns="CI nodes + structural edges")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        seen: set[str] = set()
        env = raw.get("env", "prod")
        ci_attrs = raw.get("ci_attrs", {})

        def ensure_node(name: str, sys_class_name: str) -> tuple[str, NodeType]:
            ntype = _CI_CLASS_MAP.get(sys_class_name, NodeType.CONFIG_ITEM)
            props = _props_for(ntype, name, sys_class_name, env, ci_attrs.get(name, {}))
            nid = registry.node_id(ntype, props)
            if nid not in seen:
                seen.add(nid)
                # CMDB has no facts/events to re-author — it is the declared structural spine.
                # Its P1b contribution is threading the mint source onto AddNode (step 1's "adapters
                # that mint nodes pass their source"), the provenance P6 needs to turn node props
                # into sourced assertions. The reducer's Node carries no source field yet, so this
                # is inert for graph output — goldens stay byte-identical.
                ops.append(AddNode(type=ntype, props=props, source=Source.CMDB))
            return nid, ntype

        # bare CI records (get_ci_class / find_ci_by_attr shape) — nodes only, no edges.
        for ci in raw.get("cis", []):
            ensure_node(ci["name"], ci["sys_class_name"])

        # dependency records (get_dependencies / impact_analysis / seed_graph shape).
        for dep in raw.get("dependencies", []):
            parent_id, parent_t = ensure_node(dep["parent"], dep["parent_type"])
            child_id, child_t = ensure_node(dep["child"], dep["child_type"])
            etype = _edge_type_for(dep["rel_type"], parent_t, child_t)
            if etype is None:
                continue  # no legal (parent_t, etype, child_t) triple — drop, don't force
            # CMDB is the trusted, declared source regardless of the edge type's own
            # default_origin (DESIGN §2.1 R-G8 / Origin.DECLARED docstring).
            ops.append(AddEdge(type=etype, src=parent_id, dst=child_id, origin=Origin.DECLARED))
        return ops
