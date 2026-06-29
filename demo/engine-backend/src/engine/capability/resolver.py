"""Intent resolution — a playbook `need` becomes a concrete, allowed capability. C4.

    resolve_intent(need, phase_effect) =
        rank([c for c in declared_capabilities
              if need in c.intents and compatible(c.effect_hint, phase_effect)])

The effect boundary is enforced HERE, at resolution — not only at the gate — so a read-only phase
**provably cannot select a write** (the write/unknown candidates never even enter the ranking).
"""
from __future__ import annotations

from engine.domain import DeclaredCapability, Effect, PhaseEffect


def compatible(effect: Effect, phase_effect: PhaseEffect) -> bool:
    if phase_effect is PhaseEffect.write:
        return True                          # a write phase may use read, write, or unknown caps
    return effect is Effect.read             # a read-only phase: only provably-read caps


def _effect_of(c: DeclaredCapability, registry) -> Effect:
    """The AUTHORITATIVE effect govern() will use — a policy effect overrides a mis-declared hint
    (govern.py:39). The resolver must filter on this same effect, not the raw `effect_hint`: a
    capability whose hint says `read` but whose policy corrects it to `write` would otherwise pass
    the read-only filter and be invoked — silently breaking the FR12/AC1 invariant this module
    claims to prove. Mirroring govern() here keeps resolve-time and gate-time effects identical."""
    pol = registry.policy(c.id)
    return (pol.effect if pol else None) or c.effect_hint or Effect.unknown


def resolve_intent(need: str, phase_effect: PhaseEffect, registry) -> list[DeclaredCapability]:
    candidates = [c for c in registry.by_intent(need)
                  if compatible(_effect_of(c, registry), phase_effect)]
    return _rank(candidates, registry)


def _rank(caps: list[DeclaredCapability], registry) -> list[DeclaredCapability]:
    def key(c: DeclaredCapability) -> tuple:
        prov = registry.providers.get(c.provider)
        trusted = bool(prov and prov.trusted)
        read_first = 0 if _effect_of(c, registry) is Effect.read else 1
        return (0 if trusted else 1, read_first, c.id)

    return sorted(caps, key=key)
