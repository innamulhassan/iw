"""EDGE_SPECS — the closed EdgeType -> EdgeSpec catalog, assembled from the group
modules (structural spine / ownership+supply-chain / signal+causal, DESIGN-INPUT
§B.3). `registry.py` asserts this dict covers every EdgeType member (the closure
guarantee, R-G1); we also assert it here so an incomplete group module fails loudly at
the source rather than downstream.
"""
from __future__ import annotations

from ..enums import EdgeType
from ..spec import EdgeSpec
from . import causal, structural, supply

EDGE_SPECS: dict[EdgeType, EdgeSpec] = {
    spec.type: spec
    for module in (structural, supply, causal)
    for spec in module.SPECS
}

# The structural spine's edge types (P3 type airlock, DOMAIN-v3 §2.4 row 2): the ONLY layer on
# which `generic_ci` may substitute for an endpoint (registry.edge_airlocked). Derived from the
# structural module so the set can never drift from the catalog.
STRUCTURAL_EDGE_TYPES: frozenset[EdgeType] = frozenset(s.type for s in structural.SPECS)

_missing = [t.value for t in EdgeType if t not in EDGE_SPECS]
if _missing:
    raise RuntimeError(f"edges/__init__ incomplete — EdgeTypes without a spec: {_missing}")

__all__ = ["EDGE_SPECS", "STRUCTURAL_EDGE_TYPES"]
