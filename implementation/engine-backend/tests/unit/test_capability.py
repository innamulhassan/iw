"""P3 · capability layer (Part C) — unit tests.

Covers: a new capability lands pending_review·deny (C2); resolve_intent enforces the effect boundary
so a read-only phase yields ZERO write candidates (C4 / AC1); govern()'s decision table (C3); the
gate + adapter dispatch (a never-named capability runs only via registry+govern, AC5); and the
earned-autonomy ladder (C5).
"""
from __future__ import annotations

import pytest

from engine.capability import (
    AdapterRegistry,
    CapabilityLayer,
    CapabilityRegistry,
    Denied,
    MockAdapter,
    NeedsApproval,
    demote,
    govern,
    promote,
    resolve_intent,
)
from engine.domain import (
    Access,
    CapabilityPolicy,
    DeclaredCapability,
    Effect,
    PhaseEffect,
    PolicyStatus,
    Provider,
    ProviderKind,
)


def build_registry() -> CapabilityRegistry:
    r = CapabilityRegistry()
    r.add_provider(Provider(id="appd", kind=ProviderKind.mcp_remote, trusted=True))
    r.add_provider(Provider(id="otel", kind=ProviderKind.mcp_local, trusted=True))
    r.add_provider(Provider(id="bladelogic", kind=ProviderKind.a2a_agent, trusted=True))
    r.add_provider(Provider(id="weird", kind=ProviderKind.api, trusted=False))
    # reviewed (active) policies
    r.register_capability(
        DeclaredCapability(id="appd__get_health", provider="appd", effect_hint=Effect.read,
                           intents=["telemetry", "metrics"]),
        policy=CapabilityPolicy(capability_id="appd__get_health", effect=Effect.read,
                                access=Access.allow, status=PolicyStatus.active),
    )
    r.register_capability(
        DeclaredCapability(id="bladelogic__restart", provider="bladelogic", effect_hint=Effect.write,
                           intents=["remediation-action"]),
        policy=CapabilityPolicy(capability_id="bladelogic__restart", effect=Effect.write,
                                access=Access.ask, status=PolicyStatus.active),
    )
    # synced-only declarations (no policy yet → exercise govern's DEFAULT / unknown_access path)
    r.sync_capability(DeclaredCapability(id="otel__traces", provider="otel", effect_hint=Effect.read,
                                         intents=["traces"]))
    r.sync_capability(DeclaredCapability(id="weird__thing", provider="weird", effect_hint=Effect.unknown,
                                         intents=["mystery"]))
    return r


def build_layer() -> CapabilityLayer:
    r = build_registry()
    a = AdapterRegistry()
    a.bind("appd", MockAdapter(ProviderKind.mcp_remote, {"appd__get_health": {"health": "degraded"}}))
    a.bind("otel", MockAdapter(ProviderKind.mcp_local))
    a.bind("bladelogic", MockAdapter(ProviderKind.a2a_agent, {"bladelogic__restart": {"result": "ok"}}))
    return CapabilityLayer(r, a)


# ── registry: a new capability lands pending_review · deny (C2) ─────────
def test_new_capability_lands_pending_review_deny():
    r = CapabilityRegistry()
    r.add_provider(Provider(id="p", kind=ProviderKind.api, trusted=True))
    r.register_capability(DeclaredCapability(id="p__act", provider="p", effect_hint=Effect.write,
                                             intents=["x"]))
    pol = r.policy("p__act")
    assert pol.status is PolicyStatus.pending_review
    assert pol.access is Access.deny


# ── resolve_intent: the effect boundary (C4 / AC1) ──────────────────────
def test_read_only_phase_yields_zero_write_candidates():
    r = build_registry()
    assert resolve_intent("remediation-action", PhaseEffect.read_only, r) == []   # AC1 — pre-gate
    got = resolve_intent("remediation-action", PhaseEffect.write, r)
    assert [c.id for c in got] == ["bladelogic__restart"]


def test_resolve_returns_capability_by_intent():
    r = build_registry()
    assert "appd__get_health" in [c.id for c in resolve_intent("metrics", PhaseEffect.read_only, r)]


def test_unknown_effect_excluded_from_read_only():
    r = build_registry()
    assert resolve_intent("mystery", PhaseEffect.read_only, r) == []   # not provably read


# ── govern() decision table (C3) ────────────────────────────────────────
def test_govern_active_policy_decides():
    r = build_registry()
    assert govern("appd__get_health", r).access is Access.allow
    assert govern("bladelogic__restart", r).access is Access.ask


def test_govern_default_trusted_read_allows():
    r = build_registry()
    d = govern("otel__traces", r)         # synced, no policy → DEFAULT[trusted][read]
    assert d.effect is Effect.read and d.access is Access.allow


def test_govern_unknown_effect_uses_unknown_access():
    r = build_registry()
    d = govern("weird__thing", r, unknown_access=Access.ask)
    assert d.effect is Effect.unknown and d.access is Access.ask


def test_govern_unknown_capability_hard_denies():
    r = build_registry()
    assert govern("ghost__cap", r).access is Access.deny


def test_govern_pending_review_denies():
    r = CapabilityRegistry()
    r.add_provider(Provider(id="p", kind=ProviderKind.api, trusted=True))
    r.register_capability(DeclaredCapability(id="p__act", provider="p", effect_hint=Effect.read,
                                             intents=["x"]))
    assert govern("p__act", r).access is Access.deny


# ── layer.invoke: gate + adapter dispatch (AC5) ─────────────────────────
def test_invoke_allow_returns_toy_data():
    assert build_layer().invoke("appd__get_health", {"app": "payments-api"}) == {"health": "degraded"}


def test_invoke_ask_raises_then_runs_on_approval():
    layer = build_layer()
    with pytest.raises(NeedsApproval):
        layer.invoke("bladelogic__restart", {"svc": "x"})
    assert layer.invoke("bladelogic__restart", {"svc": "x"}, approved=True) == {"result": "ok"}


def test_invoke_unknown_capability_is_denied():
    with pytest.raises(Denied):
        build_layer().invoke("ghost__cap", {})


def test_never_named_capability_runs_only_via_registry_and_govern():
    # AC5 — otel__traces is never named by the playbook; it is reachable only through the registry
    # + govern (DEFAULT allow here), then dispatched to its adapter.
    out = build_layer().invoke("otel__traces", {"trace": "checkout"})
    assert out["mock"] is True


# ── autonomy ladder (C5 / FR13) ─────────────────────────────────────────
def test_autonomy_promote_then_demote():
    r = build_registry()
    assert promote(r, "bladelogic__restart") is True          # ask → allow (earned)
    assert r.policy("bladelogic__restart").access is Access.allow
    assert demote(r, "bladelogic__restart") is True           # allow → ask (failure)
    assert r.policy("bladelogic__restart").access is Access.ask


def test_autonomy_does_not_promote_pending_review():
    r = build_registry()
    r.register_capability(DeclaredCapability(id="new__cap", provider="appd", effect_hint=Effect.read,
                                             intents=["z"]))
    assert promote(r, "new__cap") is False                    # unreviewed stays denied
    assert r.policy("new__cap").access is Access.deny
