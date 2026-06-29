"""Autonomy ladder — earned, reversible. C5 / FR13.

Operator feedback (kept separate from the run) moves a proven, low-risk, reversible action
`ask → allow`; a declared failure / correction moves it back. The same CapabilityPolicy row is what
changes. (The full per-phase × node-type × severity scoping is a refinement on top of this — here
the unit that changes is the policy row, per C5.)
"""
from __future__ import annotations

from engine.domain import Access, PolicyStatus


def promote(registry, cap_id: str) -> bool:
    """ask → allow, only for a reviewed (active) policy. Returns True if it changed."""
    pol = registry.policy(cap_id)
    if pol is None or pol.status is not PolicyStatus.active or pol.access is not Access.ask:
        return False
    pol.access = Access.allow
    return True


def demote(registry, cap_id: str) -> bool:
    """allow → ask, on a declared failure / correction. Returns True if it changed."""
    pol = registry.policy(cap_id)
    if pol is None or pol.access is not Access.allow:
        return False
    pol.access = Access.ask
    return True
