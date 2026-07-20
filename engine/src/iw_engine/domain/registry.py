"""The registry — the single source of truth that binds the closed vocabulary to its
rules. NODE_SPECS/EDGE_SPECS come from the tier catalogs under nodes/ and edges/; this
module validates the catalog is COMPLETE (every NodeType/EdgeType has a spec — the
closure guarantee, DESIGN §2.1 R-G1) and provides the deterministic id helpers +
validation predicates the reducer and scenario authors share.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from .edges import EDGE_SPECS  # dict[EdgeType, EdgeSpec]
from .enums import EdgeType, NodeType, Origin
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


# ── deterministic ids (planner + reducer + scenarios share these) ─────────────
def _slug(v: object) -> str:
    return str(v).strip().replace(" ", "-").replace("/", "-").lower()


def node_id(ntype: NodeType, props: dict) -> str:
    """Idempotent identity — same type + identity_keys → same id (upsert key)."""
    spec = NODE_SPECS[ntype]
    keys = spec.identity_keys or tuple(sorted(props.keys()))
    parts = [_slug(props.get(k, "")) for k in keys]
    return f"{ntype.value}:" + "|".join(parts)


def fact_id(subject: str, predicate: str, valid_from: datetime | str) -> str:
    raw = f"{subject}|{predicate}|{valid_from}"
    return "fact:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def event_id(entity: str, etype: str, occurred_at: datetime | str) -> str:
    raw = f"{entity}|{etype}|{occurred_at}"
    return "evt:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def edge_id(etype: EdgeType, src: str, dst: str, origin: Origin) -> str:
    return f"edge:{etype.value}:{src}->{dst}:{origin.value}"


# ── validation predicates (reducer uses these) ────────────────────────────────
def edge_allowed(etype: EdgeType, src_type: NodeType, dst_type: NodeType) -> bool:
    return (src_type, dst_type) in EDGE_SPECS[etype].allowed


def predicate_allowed(ntype: NodeType, predicate: str) -> bool:
    preds = NODE_SPECS[ntype].fact_predicates
    return True if not preds else predicate in preds   # empty spec = unconstrained (peripheral types)


def event_allowed(ntype: NodeType, etype: str) -> bool:
    evs = NODE_SPECS[ntype].event_types
    return True if not evs else etype in evs


def all_node_types() -> set[NodeType]:
    return set(NODE_SPECS.keys())
