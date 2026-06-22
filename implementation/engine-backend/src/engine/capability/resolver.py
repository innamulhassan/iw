"""Intent resolution — a playbook `need` becomes a concrete, allowed capability. C4.

    resolve_intent(need, phase_effect) =
        rank([c for c in declared_capabilities
              if need in c.intents and compatible(c.effect_hint, phase_effect)])

The effect boundary is enforced HERE, at resolution — not only at the gate — so a read-only phase
**provably cannot select a write** (the write/unknown candidates never even enter the ranking).
"""
from __future__ import annotations

from engine.domain import DeclaredCapability, Effect, PhaseEffect


def compatible(effect_hint: Effect, phase_effect: PhaseEffect) -> bool:
    if phase_effect is PhaseEffect.write:
        return True                          # a write phase may use read, write, or unknown caps
    return effect_hint is Effect.read        # a read-only phase: only provably-read caps


def resolve_intent(need: str, phase_effect: PhaseEffect, registry) -> list[DeclaredCapability]:
    candidates = [c for c in registry.by_intent(need)
                  if compatible(c.effect_hint, phase_effect)]
    return _rank(candidates, registry)


def _rank(caps: list[DeclaredCapability], registry) -> list[DeclaredCapability]:
    def key(c: DeclaredCapability) -> tuple:
        prov = registry.providers.get(c.provider)
        trusted = bool(prov and prov.trusted)
        read_first = 0 if c.effect_hint is Effect.read else 1
        return (0 if trusted else 1, read_first, c.id)

    return sorted(caps, key=key)
