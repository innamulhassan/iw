"""P2 · graph runtime (B9) — the engine-owned in-memory graph, its governed tool surface, the
bounded render-slice, and the per-source fold-adapters."""
from __future__ import annotations

from .fold import AlertFold, FoldAdapter, FoldRegistry, MetricsFold, TopologyFold, default_registry
from .graph import UNKNOWN, IncidentGraph
from .render import render_slice

__all__ = [
    "IncidentGraph", "UNKNOWN",
    "render_slice",
    "FoldAdapter", "FoldRegistry", "TopologyFold", "MetricsFold", "AlertFold", "default_registry",
]
