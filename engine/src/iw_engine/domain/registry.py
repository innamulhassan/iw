"""The registry — the single source of truth that binds the closed vocabulary to its
rules. NODE_SPECS/EDGE_SPECS come from the tier catalogs under nodes/ and edges/; this
module validates the catalog is COMPLETE (every NodeType/EdgeType has a spec — the
closure guarantee, DESIGN §2.1 R-G1) and provides the deterministic id helpers +
validation predicates the reducer and scenario authors share.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from .edges import EDGE_SPECS, STRUCTURAL_EDGE_TYPES  # dict[EdgeType, EdgeSpec] + spine set
from .enums import EdgeClass, EdgeType, NodeType, Origin
from .nodes import NODE_SPECS  # dict[NodeType, NodeSpec]
from .spec import EdgeSpec, NodeSpec

# ── closure guarantee (evaluated at import) ───────────────────────────────────
_missing_nodes = [t.value for t in NodeType if t not in NODE_SPECS]
_missing_edges = [t.value for t in EdgeType if t not in EDGE_SPECS]
if _missing_nodes:
    raise RuntimeError(f"registry incomplete — NodeTypes without a spec: {_missing_nodes}")
if _missing_edges:
    raise RuntimeError(f"registry incomplete — EdgeTypes without a spec: {_missing_edges}")


def node_spec(t: NodeType) -> NodeSpec:
    return NODE_SPECS[t]


def edge_spec(t: EdgeType) -> EdgeSpec:
    return EDGE_SPECS[t]


def edge_class(t: EdgeType) -> EdgeClass:
    """The settled semantic class of an edge type (NODE-EDGE-PRIMITIVES §5.2) — the behavioral
    mapping the reducer/queries key on (STRUCTURAL/PROVENANCE/PARTICIPATION/CAUSAL/EVIDENTIAL/
    CORRESPONDENCE/REMEDIATION), orthogonal to the physical group module it lives in."""
    return EDGE_SPECS[t].edge_class


def is_immutable_edge(t: EdgeType) -> bool:
    """PROVENANCE/lineage discipline (§5.2 class 2): a lineage edge never un-happens — it is
    superseded-on-rebuild, NEVER retracted-as-wrong. The reducer refuses to tombstone one."""
    return EDGE_SPECS[t].immutable


def is_symmetric_edge(t: EdgeType) -> bool:
    """CORRESPONDENCE discipline (§5.2 class 6): stored in a canonical direction, read as
    symmetric. `graph.symmetric_neighbours` reads such an edge from either endpoint."""
    return EDGE_SPECS[t].symmetric


# ── deterministic ids (planner + reducer + scenarios share these) ─────────────
def _slug(v: object) -> str:
    # P5 identity hardening (DOMAIN-v3 §2.1 / audit 4 probe D): `_` collapses like space/`/`,
    # so `payments_api` == `payments-api` == `Payments API` — cross-tool spellings of one name
    # stop minting split-brain twins.
    return str(v).strip().replace(" ", "-").replace("/", "-").replace("_", "-").lower()


def node_id(ntype: NodeType, props: dict) -> str:
    """Idempotent identity — same type + identity_keys → same id (upsert key)."""
    spec = NODE_SPECS[ntype]
    keys = spec.identity_keys or tuple(sorted(props.keys()))
    parts = [_slug(props.get(k, "")) for k in keys]
    return f"{ntype.value}:" + "|".join(parts)


def subject_node_id(subject_type: NodeType, subject_id: str) -> str:
    """The investigation's SUBJECT/ORIGIN node id — the owner's "incident is the first
    node", as playbook DATA (P7 step 5: `Playbook.subject_node` beside `symptom_node`; the
    engine keys on the role binding, never on an incident convention). The subject's
    external id lands on the subject-node type's FIRST identity key."""
    spec = NODE_SPECS[subject_type]
    key = spec.identity_keys[0] if spec.identity_keys else "id"
    return node_id(subject_type, {key: subject_id})


def missing_identity_keys(ntype: NodeType, props: dict) -> tuple[str, ...]:
    """The identity keys absent (or slug-empty) in `props` — non-empty means `node_id` would
    mint a degenerate id (`generic_ci:`, `service:|prod`). The reducer REJECTS such an AddNode
    (P5 / DOMAIN-v3 §2.1 identity hardening: "a missing identity key is a rejection, not a
    `type:` degenerate id"); this stays a pure predicate so scenario authors and the planner
    pre-check share the same authority."""
    spec = NODE_SPECS[ntype]
    return tuple(k for k in spec.identity_keys
                 if props.get(k) is None or not _slug(props[k]))


def fact_id(subject: str, predicate: str, valid_from: datetime | str) -> str:
    raw = f"{subject}|{predicate}|{valid_from}"
    return "fact:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def event_id(entity: str, etype: str, occurred_at: datetime | str) -> str:
    raw = f"{entity}|{etype}|{occurred_at}"
    return "evt:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def edge_id(etype: EdgeType, src: str, dst: str, origin: Origin) -> str:
    return f"edge:{etype.value}:{src}->{dst}:{origin.value}"


# ── validation predicates (reducer uses these) ────────────────────────────────
def edge_airlocked(etype: EdgeType, src_type: NodeType, dst_type: NodeType) -> bool:
    """P3 TYPE AIRLOCK (DOMAIN-v3 §2.4 row 2 — "generic structural participation"): `generic_ci`
    may SUBSTITUTE for either endpoint of a STRUCTURAL edge, so an unknown CI can be placed in
    the topology instead of staying edge-isolated (3/316 pairs). Governed, not open: the edge
    type stays a closed member, the layer is the structural spine only (never causal/evidence),
    and the NON-generic endpoint must already be legal on its side of that edge type — generic_ci
    stands in for one half of an existing pair, it does not mint new pair semantics. Both-generic
    is allowed (two undiscovered CIs can still be topologically linked). The reducer marks every
    such edge provisional + origin=discovered with a confidence penalty."""
    if etype not in STRUCTURAL_EDGE_TYPES or NodeType.GENERIC_CI not in (src_type, dst_type):
        return False
    allowed = EDGE_SPECS[etype].allowed
    if src_type is NodeType.GENERIC_CI and dst_type is NodeType.GENERIC_CI:
        return True
    if src_type is NodeType.GENERIC_CI:
        return any(d is dst_type for _, d in allowed)
    return any(s is src_type for s, _ in allowed)


def edge_allowed(etype: EdgeType, src_type: NodeType, dst_type: NodeType) -> bool:
    return ((src_type, dst_type) in EDGE_SPECS[etype].allowed
            or edge_airlocked(etype, src_type, dst_type))


def predicate_allowed(ntype: NodeType, predicate: str) -> bool:
    preds = NODE_SPECS[ntype].fact_predicates
    return True if not preds else predicate in preds   # empty spec = unconstrained (peripheral types)


def event_allowed(ntype: NodeType, etype: str) -> bool:
    evs = NODE_SPECS[ntype].event_types
    return True if not evs else etype in evs


def all_node_types() -> set[NodeType]:
    return set(NODE_SPECS.keys())
