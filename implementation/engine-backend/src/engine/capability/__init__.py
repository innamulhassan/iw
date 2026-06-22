"""P3 · capability layer (Part C) — registry, govern(), intent resolver, per-kind adapters, and the
earned-autonomy ladder. The governed boundary between the engine and every source."""
from __future__ import annotations

from .adapters import AdapterRegistry, CapabilityAdapter, MockAdapter
from .autonomy import demote, promote
from .govern import Decision, govern
from .layer import CapabilityLayer, Denied, NeedsApproval
from .registry import CapabilityRegistry
from .resolver import compatible, resolve_intent

__all__ = [
    "CapabilityRegistry",
    "govern", "Decision",
    "resolve_intent", "compatible",
    "CapabilityAdapter", "MockAdapter", "AdapterRegistry",
    "CapabilityLayer", "Denied", "NeedsApproval",
    "promote", "demote",
]
