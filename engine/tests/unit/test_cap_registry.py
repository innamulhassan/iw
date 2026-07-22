"""Capability registry-as-data + allow/ask/deny policy (part4-capability §1-2).

The registry mirrors the wired adapters as data (like the domain dictionary), carries a
PER-INTENT effect, and is enforced as an OPT-IN governance gate at the CapabilityLayer
boundary: a layer built WITHOUT a registry behaves exactly as before (proven by the whole
rest of the suite still passing); a layer built WITH one gets allow/ask/deny + pending-review
governance.
"""
from __future__ import annotations

from typing import ClassVar

from iw_engine.capability import (
    CapabilityCall,
    CapabilityLayer,
    CapabilityRegistry,
    MockSource,
    Policy,
)
from iw_engine.capability.adapters import default_adapters
from iw_engine.domain.enums import Effect


class _DualAdapter:
    provider = "ocp"
    intents = frozenset({"pod_status", "ocp__restart"})
    effect = Effect.READ
    effects: ClassVar = {"ocp__restart": Effect.WRITE}
    binding = None

    def normalize(self, raw):
        return []


# ── registry as data ───────────────────────────────────────────────────────────
def test_registry_from_adapters_carries_per_intent_effect():
    reg = CapabilityRegistry.from_adapters([_DualAdapter()])
    # the SAME adapter yields a read row AND a write row - the split-adapter workaround retired
    assert reg.effect_for("pod_status") is Effect.READ
    assert reg.effect_for("ocp__restart") is Effect.WRITE
    assert reg.policy_for("pod_status") is Policy.ALLOW


def test_registry_unknown_intent_is_pending_review_deny():
    reg = CapabilityRegistry.from_adapters(default_adapters())
    spec = reg.spec_for("some_intent_no_tool_declares")
    assert spec.policy is Policy.DENY and spec.pending_review
    assert reg.effect_for("some_intent_no_tool_declares") is None  # caller falls back to adapter


def test_registry_mirrors_every_default_adapter_intent():
    adapters = default_adapters()
    reg = CapabilityRegistry.from_adapters(adapters)
    for a in adapters:
        for intent in a.intents:
            assert reg.policy_for(intent) is Policy.ALLOW
            assert reg.spec_for(intent).provider == a.provider


# ── policy enforced at the layer (opt-in) ───────────────────────────────────────
def test_layer_without_registry_is_unchanged():
    # a read is served with no policy machinery at all (baseline behaviour preserved)
    layer = CapabilityLayer([_DualAdapter()], source=MockSource({"pod_status": {}}))
    _, inv = layer.serve(CapabilityCall(intent="pod_status"), allow_write=False)
    assert not inv.blocked


def test_layer_registry_effect_gates_write_per_intent():
    reg = CapabilityRegistry.from_adapters([_DualAdapter()])
    layer = CapabilityLayer([_DualAdapter()], registry=reg)
    # read intent flows; write intent on the same adapter is gated by the registry's effect
    _, r = layer.invoke("pod_status", {}, allow_write=False)
    assert not r.blocked
    _, w = layer.invoke("ocp__restart", {}, allow_write=False)
    assert w.blocked and w.effect is Effect.WRITE


def test_layer_policy_deny_blocks():
    reg = CapabilityRegistry.from_adapters([_DualAdapter()])
    reg.set_policy("pod_status", Policy.DENY)
    layer = CapabilityLayer([_DualAdapter()], registry=reg)
    _, inv = layer.invoke("pod_status", {}, allow_write=False)
    assert inv.blocked and inv.outcome == "blocked" and "deny" in inv.reason


def test_layer_policy_ask_is_the_human_gate():
    reg = CapabilityRegistry.from_adapters([_DualAdapter()])
    reg.set_policy("pod_status", Policy.ASK)
    layer = CapabilityLayer([_DualAdapter()], registry=reg)
    # ask blocks a read outside an approved gate...
    _, blocked = layer.invoke("pod_status", {}, allow_write=False)
    assert blocked.blocked and "ask" in blocked.reason
    # ...and releases inside one (allow_write signals the approved gate)
    _, ok = layer.invoke("pod_status", {}, allow_write=True)
    assert not ok.blocked


def test_layer_pending_review_intent_is_denied():
    # a registry that doesn't list an adapter's intent treats it as pending_review -> deny,
    # so an un-governed capability call is provably refused, not silently executed
    reg = CapabilityRegistry(specs={})   # empty registry: nothing is governed
    layer = CapabilityLayer([_DualAdapter()], registry=reg)
    _, inv = layer.invoke("pod_status", {}, allow_write=False)
    assert inv.blocked and "pending review" in inv.reason
