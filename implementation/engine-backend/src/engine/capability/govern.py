"""govern() — resolved at call time, every time. C3.

    effect = policy(cap).effect ?? cap.effect_hint ?? "unknown"
    access = policy(cap).access ?? (playbook.unknown_access if effect=="unknown"
                                    else DEFAULT[trust(cap)][effect])

The DEFAULT[trust][effect] table is the engine's safe-by-default fallback (the design leaves the
exact table to the engine); operationally every onboarded capability has a policy, so DEFAULT only
fires for a declaration that is synced but not yet policy-reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.domain import Access, Effect

# DEFAULT[trusted][effect] — conservative: a trusted read runs; an untrusted write is refused.
_DEFAULT: dict[bool, dict[Effect, Access]] = {
    True:  {Effect.read: Access.allow, Effect.write: Access.ask,  Effect.unknown: Access.ask},
    False: {Effect.read: Access.ask,   Effect.write: Access.deny, Effect.unknown: Access.deny},
}


@dataclass(frozen=True)
class Decision:
    capability_id: str
    effect: Effect
    access: Access
    reason: str


def govern(cap_id: str, registry, *, unknown_access: Access = Access.ask) -> Decision:
    cap = registry.capability(cap_id)
    if cap is None:
        # a capability not in the registry is never invoked — hard deny, never silently allowed
        return Decision(cap_id, Effect.unknown, Access.deny, "capability not in registry")

    pol = registry.policy(cap_id)
    effect: Effect = (pol.effect if pol else None) or cap.effect_hint or Effect.unknown

    if pol is not None:
        # a policy decides access — including a `pending_review` policy, which lands `deny`
        return Decision(cap_id, effect, pol.access, f"policy:{pol.status.value}")

    if effect is Effect.unknown:
        return Decision(cap_id, effect, unknown_access, "no policy · unknown effect → unknown_access")

    prov = registry.provider_of(cap_id)
    trusted = bool(prov and prov.trusted)
    return Decision(cap_id, effect, _DEFAULT[trusted][effect],
                    f"no policy · DEFAULT[trusted={trusted}][{effect.value}]")
