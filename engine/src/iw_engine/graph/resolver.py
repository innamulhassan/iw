"""resolver — the identity/alias layer (P5, DOMAIN-v3 §2.1 + §9.2; R-J5's "small alias table",
ruled in the original design and never built).

Per-tool identifiers stop being inert prop cargo and become identity surface: any node whose
props carry one of the IDENTITY-BACKBONE keys (the ones the ServiceNow adapter already resolves
off the incident's CMDB CI — `app_id`/`sys_id`/`repo`/`k8s_workload`, `nodes/logical.py`) gains
an `aliases {scheme: id}` block, and the graph maintains an ALIAS INDEX (`"scheme:id"` →
canonical node id). The reducer consults that index to RESOLVE an observation keyed by a tool's
own id to the entity that already exists, instead of minting a split-brain twin (audit 4 S1.4;
audit 2 §7 — cross-tool identity was display-name string luck).

This module is pure derivation (no graph import): the index lives on `Graph`, the resolution
decisions live in the reducer — both consume these helpers so scheme naming has ONE authority.
"""
from __future__ import annotations

from ..domain.enums import NodeType
from ..domain.registry import _slug

# ── the identity backbone: prop name → alias scheme ───────────────────────────
# Exactly the per-tool foreign keys today carried inert on Service props ("the identity backbone
# of a real cross-tool investigation", domain/nodes/logical.py; populated at servicenow.py
# get_incident). Values are kept EXACT (tool ids are opaque and case-sensitive); only the
# whitespace shell is stripped.
ALIAS_SCHEMES: dict[str, str] = {
    "sys_id": "servicenow",     # ServiceNow CMDB sys_id
    "app_id": "appd",           # AppDynamics application id
    "repo": "git",              # source repository
    "k8s_workload": "k8s",      # kubernetes workload (deployment) name
}

# TYPE-SCOPED on purpose: a backbone prop is an alias only where it identifies THE ENTITY
# ITSELF in the other tool's namespace. On a Service, `repo`/`sys_id`/`app_id`/`k8s_workload`
# are that service's own cross-tool credentials (that is the CMDB-CI resolution the ServiceNow
# adapter performs). On a code_commit or pull_request, `repo` is a namespace QUALIFIER of a
# different entity kind — lifting it would make every commit claim to BE the repository.
# Extend this set as adapters start carrying backbone ids for more types (e.g. a datadog host
# adapter registering an instance-id alias on HOST — audit 4 S1.4's split-brain).
BACKBONE_TYPES: frozenset[NodeType] = frozenset({NodeType.SERVICE})


def alias_key(scheme: str, value: object) -> str:
    """The index key for one alias — `"scheme:id"` (e.g. `servicenow:sys_2fe9`). Also the
    spelling an op may use as an assertion SUBJECT when it knows the entity only by a tool id
    (DOMAIN-v3 §2.1: "an observation arriving keyed only appd:app_id=… resolves to the existing
    entity")."""
    return f"{scheme}:{str(value).strip()}"


def aliases_from_props(ntype: NodeType, props: dict) -> dict[str, str]:
    """The `{scheme: id}` aliases a node of `ntype` claims through its props, via the
    identity-backbone table. Deterministic order (the module table's); empty/None values never
    claim; non-backbone types claim nothing."""
    if ntype not in BACKBONE_TYPES:
        return {}
    out: dict[str, str] = {}
    for prop, scheme in ALIAS_SCHEMES.items():
        v = props.get(prop)
        if v is not None and str(v).strip():
            out[scheme] = str(v).strip()
    return out


def provisional_node_id(ntype: NodeType, aliases: dict[str, str]) -> str:
    """Deterministic id for a PROVISIONAL entity (DOMAIN-v3 §9.2 late alias binding): an
    observation whose canonical identity keys are not yet known but which carries at least one
    alias credential. Keyed on the alphabetically-first scheme so repeats of the same
    observation converge on the same provisional entity. The `~` sigil after the type prefix
    cannot arise from natural-key slugging, so the namespace never collides with canonical
    ids; the node additionally carries `provisional=True`."""
    scheme = sorted(aliases)[0]
    return f"{ntype.value}:~{scheme}:{_slug(aliases[scheme])}"
