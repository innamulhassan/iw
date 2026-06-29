"""The capability registry — providers, declared capabilities, human-owned policies. C2 / 04 §6.2.

Declarations are machine-synced and refresh; the policy is human-owned and sticky across syncs. A
NEW capability lands `pending_review` · `deny` until a human reviews it.
"""
from __future__ import annotations

from typing import Optional

from engine.domain import (
    Access,
    CapabilityPolicy,
    DeclaredCapability,
    PolicyStatus,
    Provider,
)


class CapabilityRegistry:
    def __init__(self) -> None:
        self.providers: dict[str, Provider] = {}
        self.capabilities: dict[str, DeclaredCapability] = {}
        self.policies: dict[str, CapabilityPolicy] = {}

    # ── providers ──
    def add_provider(self, provider: Provider) -> None:
        self.providers[provider.id] = provider

    def provider_of(self, cap_id: str) -> Optional[Provider]:
        cap = self.capabilities.get(cap_id)
        return self.providers.get(cap.provider) if cap else None

    # ── declarations (machine-synced) ──
    def sync_capability(self, cap: DeclaredCapability) -> DeclaredCapability:
        """Machine sync of one declaration — refreshes the contract, never touches policy."""
        self.capabilities[cap.id] = cap
        return cap

    def register_capability(self, cap: DeclaredCapability, *,
                            policy: Optional[CapabilityPolicy] = None) -> CapabilityPolicy:
        """Onboard a capability: sync the declaration AND ensure a policy. With no explicit policy,
        a NEW capability lands `pending_review` · `deny` (C2) — denied until a human reviews it."""
        self.sync_capability(cap)
        if policy is not None:
            self.policies[cap.id] = policy
        elif cap.id not in self.policies:
            self.policies[cap.id] = CapabilityPolicy(
                capability_id=cap.id, effect=cap.effect_hint,
                access=Access.deny, status=PolicyStatus.pending_review,
            )
        return self.policies[cap.id]

    # ── policy (human-owned) ──
    def set_policy(self, policy: CapabilityPolicy) -> None:
        self.policies[policy.capability_id] = policy

    def policy(self, cap_id: str) -> Optional[CapabilityPolicy]:
        return self.policies.get(cap_id)

    def capability(self, cap_id: str) -> Optional[DeclaredCapability]:
        return self.capabilities.get(cap_id)

    # ── lookup ──
    def by_intent(self, need: str) -> list[DeclaredCapability]:
        return [c for c in self.capabilities.values() if need in c.intents]
