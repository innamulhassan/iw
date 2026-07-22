"""capability/registry.py - the capability registry as DATA (part4-capability §1).

The domain dictionary (Part I) governs the *vocabulary of the graph*; this is the same
closed-core + governed-onboarding grammar applied to the *vocabulary of the tools*. One
`CapabilitySpec` per (provider, intent) carries:

  - `effect`  - read | write, PER-INTENT (not per-adapter): this is where the split-adapter
                workaround (`OcpRestartAdapter`) is retired - one adapter can host both a read
                and a write intent and the registry says which is which.
  - `policy`  - allow | ask | deny (v2's `CapabilityPolicy`, revived from design-ware). `ask`
                is the human gate; a NEW / unknown capability lands `pending_review` → deny.

`CapabilityRegistry.from_adapters(...)` builds the registry as data mirrored off the wired
adapters (default policy `allow`), exactly as the domain dictionary is data mirrored off the
registered node/edge types. A partial hand-authored registry treats any intent it does not
list as `pending_review` (deny) - the governance property that makes an unknown tool call
provably refused at resolution, rather than silently executed.

SCOPE NOTE: enforcement here is at the `CapabilityLayer` boundary (opt-in: a layer built
without a registry behaves exactly as before). Per-call approval-TOKEN binding (a `gate_id`
linking one approval to one write Invocation) and playbook `allowed_intents` enforcement at
resolve-time live engine-side and are deferred to convergence - this module is the substrate
they will consume.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..domain.enums import Effect


class Policy(StrEnum):
    """Governance disposition for a capability. `ASK` is the human gate; `DENY` refuses;
    a capability the registry has never seen is treated as `DENY` (pending review)."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class CapabilitySpec:
    """One registry row - the self-describing governance record for a (provider, intent)."""

    provider: str
    intent: str
    effect: Effect = Effect.READ
    policy: Policy = Policy.ALLOW
    # true when this row was NOT declared by a governed source (an unknown intent surfaced at
    # resolution): it exists only to carry the deny disposition, and names the review to do.
    pending_review: bool = False


@dataclass
class CapabilityRegistry:
    """A closed set of `CapabilitySpec` rows keyed by intent, with the domain-dictionary's
    governed-onboarding grammar: a known intent resolves to its spec; an unknown intent
    resolves to a synthesised `pending_review` DENY spec (never an implicit allow)."""

    specs: dict[str, CapabilitySpec] = field(default_factory=dict)

    @classmethod
    def from_adapters(cls, adapters: list, *, policy: Policy = Policy.ALLOW) -> CapabilityRegistry:
        """Mirror the registry off the wired adapters (registry-as-data). Each adapter intent
        becomes an `allow` row carrying the adapter's effect - or its PER-INTENT effect when the
        adapter declares an `effects` override (so `ocp__restart` lands `write` on the same
        adapter that serves `pod_status` as `read`)."""
        specs: dict[str, CapabilitySpec] = {}
        for a in adapters:
            effects = getattr(a, "effects", None) or {}
            for intent in a.intents:
                eff = effects.get(intent, a.effect) if isinstance(effects, dict) else a.effect
                specs[intent] = CapabilitySpec(provider=a.provider, intent=intent,
                                               effect=eff, policy=policy)
        return cls(specs=specs)

    def spec_for(self, intent: str) -> CapabilitySpec:
        """Resolve an intent to its spec. An unknown intent yields a synthesised
        `pending_review` DENY spec - the governance default (never a silent allow)."""
        spec = self.specs.get(intent)
        if spec is not None:
            return spec
        return CapabilitySpec(provider="?", intent=intent, effect=Effect.READ,
                              policy=Policy.DENY, pending_review=True)

    def policy_for(self, intent: str) -> Policy:
        return self.spec_for(intent).policy

    def effect_for(self, intent: str) -> Effect | None:
        """The declared per-intent effect, or None when the intent is unknown (the caller then
        falls back to the adapter's own effect)."""
        spec = self.specs.get(intent)
        return spec.effect if spec is not None else None

    def set_policy(self, intent: str, policy: Policy) -> None:
        """Override one intent's policy (e.g. mark a write `ask`, quarantine a provider `deny`).
        Governance mutation is explicit and auditable - never implicit."""
        cur = self.spec_for(intent)
        self.specs[intent] = CapabilitySpec(provider=cur.provider, intent=intent,
                                             effect=cur.effect, policy=policy,
                                             pending_review=cur.pending_review)
