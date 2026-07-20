"""NODE_SPECS — the closed NodeType -> NodeSpec catalog, assembled from the tier
modules (one file per tier, DESIGN §3). `registry.py` asserts this dict covers every
NodeType member (the closure guarantee, R-G1); we also assert it here, at import time,
so an incomplete tier module fails loudly at the source rather than downstream.
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec
from . import change, data, logical, network, platform, runtime, signals

NODE_SPECS: dict[NodeType, NodeSpec] = {
    spec.type: spec
    for module in (logical, runtime, platform, data, network, change, signals)
    for spec in module.SPECS
}

_missing = [t.value for t in NodeType if t not in NODE_SPECS]
if _missing:
    raise RuntimeError(f"nodes/__init__ incomplete — NodeTypes without a spec: {_missing}")

__all__ = ["NODE_SPECS"]
