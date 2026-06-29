"""Per-kind capability adapters — one uniform surface over every source. C1.

A capability binds by its provider's `kind` (skill | mcp_local | mcp_remote | a2a_agent | api). Here
the adapters are MOCKs that return canned toy data, so the whole system is testable with NO real
source; the real MCP / dynamic-MCP / A2A adapters are swapped in at P9 behind the same interface.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from engine.domain import ProviderKind


@runtime_checkable
class CapabilityAdapter(Protocol):
    kind: ProviderKind

    def invoke(self, capability_id: str, input: dict) -> dict: ...


class MockAdapter:
    """Returns canned responses per capability id (else an echo). Stands in for a real source."""

    def __init__(self, kind: ProviderKind, responses: Optional[dict[str, dict]] = None) -> None:
        self.kind = kind
        self._responses = responses or {}

    def invoke(self, capability_id: str, input: dict) -> dict:
        if capability_id in self._responses:
            return self._responses[capability_id]
        return {"capability": capability_id, "input": input, "mock": True}


class AdapterRegistry:
    """provider id → the adapter that talks to it."""

    def __init__(self) -> None:
        self._by_provider: dict[str, CapabilityAdapter] = {}

    def bind(self, provider_id: str, adapter: CapabilityAdapter) -> None:
        self._by_provider[provider_id] = adapter

    def adapter_for(self, provider_id: str) -> Optional[CapabilityAdapter]:
        return self._by_provider.get(provider_id)
