"""The capability layer — one contract: resolve a need → govern → invoke.

Ties the registry (C2), govern() (C3), the resolver (C4), and the per-kind adapters (C1) together.
The gate (`ask`) and the effect boundary (read-only ⇏ write) both live here, so the engine never
touches a tool directly.
"""
from __future__ import annotations

from engine.domain import Access, DeclaredCapability, Effect, PhaseEffect

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
        self._seen: dict[str, dict] = {}    # idempotency_key → cached write result (exactly-once, FR5)

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
        # exactly-once (FR5): a write carrying an idempotency_key is dispatched at most once — a retry
        # or crash-replay with the same key returns the cached result, never double-applies the write.
        key = input.get("idempotency_key") if isinstance(input, dict) else None
        if key is not None and key in self._seen:
            return self._seen[key]
        cap = self.registry.capability(cap_id)
        adapter = self.adapters.adapter_for(cap.provider)
        if adapter is None:
            raise Denied(f"{cap_id}: no adapter bound for provider {cap.provider!r}")
        # NICE-6: the bound adapter's kind must match its provider's declared kind (C1 "binds by kind")
        provider = self.registry.provider_of(cap_id)
        if provider is not None and adapter.kind is not provider.kind:
            raise Denied(f"{cap_id}: adapter kind {adapter.kind} != provider kind {provider.kind}")
        result = adapter.invoke(cap_id, input)
        if key is not None and decision.effect is Effect.write:
            self._seen[key] = result        # record only a successful WRITE dispatch
        return result
