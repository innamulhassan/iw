"""The capability layer — one contract: resolve a need → govern → invoke.

Ties the registry (C2), govern() (C3), the resolver (C4), and the per-kind adapters (C1) together.
The gate (`ask`) and the effect boundary (read-only ⇏ write) both live here, so the engine never
touches a tool directly.
"""
from __future__ import annotations

from engine.domain import Access, DeclaredCapability, PhaseEffect

from .adapters import AdapterRegistry
from .govern import Decision, govern
from .registry import CapabilityRegistry
from .resolver import resolve_intent


class Denied(Exception):
    """govern() refused the call (deny)."""


class NeedsApproval(Exception):
    """govern() requires an operator decision (ask). The engine pauses at the gate."""

    def __init__(self, decision: Decision) -> None:
        super().__init__(f"{decision.capability_id} requires approval (ask): {decision.reason}")
        self.decision = decision


class CapabilityLayer:
    def __init__(self, registry: CapabilityRegistry, adapters: AdapterRegistry, *,
                 unknown_access: Access = Access.ask) -> None:
        self.registry = registry
        self.adapters = adapters
        self.unknown_access = unknown_access

    def resolve(self, need: str, phase_effect: PhaseEffect) -> list[DeclaredCapability]:
        return resolve_intent(need, phase_effect, self.registry)

    def govern(self, cap_id: str) -> Decision:
        return govern(cap_id, self.registry, unknown_access=self.unknown_access)

    def invoke(self, cap_id: str, input: dict, *, approved: bool = False) -> dict:
        decision = self.govern(cap_id)
        if decision.access is Access.deny:
            raise Denied(f"{cap_id} denied — {decision.reason}")
        if decision.access is Access.ask and not approved:
            raise NeedsApproval(decision)
        cap = self.registry.capability(cap_id)
        adapter = self.adapters.adapter_for(cap.provider)
        if adapter is None:
            raise Denied(f"{cap_id}: no adapter bound for provider {cap.provider!r}")
        return adapter.invoke(cap_id, input)
