"""Capability-layer tests — intent resolution, normalize->ops folding cleanly through the
reducer (zero rejections = the adapter emits only registry-valid types), and the
write-boundary (a write cannot execute outside an approved gate)."""
from __future__ import annotations

from typing import ClassVar

from iw_engine.capability import CapabilityCall, CapabilityLayer, MockSource
from iw_engine.capability.adapters.prometheus import PrometheusAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import Binding, EdgeType, Effect, NodeType
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "service": {"name": "payments-api", "env": "prod"},
    "alerts": [{"id": "ALT-1", "alertname": "HighErrorRate",
                "at": "2026-07-19T14:00:00Z", "state": "firing"}],
    "metrics": [{"predicate": "red_errors", "value": 0.4,
                 "at": "2026-07-19T14:00:00Z", "reliability": 0.97}],
}


def test_prometheus_normalize_folds_cleanly():
    layer = CapabilityLayer([PrometheusAdapter()])
    ops, inv = layer.invoke("fetch_metrics", RAW, allow_write=False)
    assert inv.provider == "prometheus" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections            # adapter emits only registry-valid types
    svc = registry.node_id(NodeType.SERVICE, {"service_name": "payments-api", "env": "prod"})
    assert any(n.id == svc for n in mat.nodes)
    # P2: the reducer canonicalizes the vendor spelling red_errors -> error_rate, keeping the
    # native name for provenance.
    assert any(f.predicate == "error_rate" and f.value == 0.4
               and f.source_native_name == "red_errors" for f in mat.facts)
    assert any(e.type == EdgeType.FIRED_ON for e in mat.edges)


def test_unknown_intent_is_recorded_not_crashing():
    layer = CapabilityLayer([PrometheusAdapter()])
    ops, inv = layer.invoke("nonexistent_intent", {}, allow_write=False)
    assert ops == [] and inv.blocked and "no capability" in inv.reason


def test_write_blocked_outside_gate():
    class _Fix:
        provider = "ocp"
        intents = frozenset({"apply_mitigation"})
        effect = Effect.WRITE

        def normalize(self, raw):
            return []

    layer = CapabilityLayer([_Fix()])
    _, blocked = layer.invoke("apply_mitigation", {}, allow_write=False)
    assert blocked.blocked and "write blocked" in blocked.reason
    assert blocked.outcome == "blocked"
    _, ok = layer.invoke("apply_mitigation", {}, allow_write=True)
    assert not ok.blocked


# ── error honesty: data / clean-empty / error are three distinguishable outcomes ──
def test_outcome_data_vs_clean_empty():
    """A read that folds facts is `data`; a read the provider answered with nothing is
    `clean-empty` — an HONEST no-data result (not an error), which downstream may treat as
    NoEvidence. The distinction is on the Invocation, not inferred from op_count alone."""
    layer = CapabilityLayer([PrometheusAdapter()],
                            source=MockSource({"fetch_metrics": RAW}))
    ops, inv = layer.serve(CapabilityCall(intent="fetch_metrics"), allow_write=False)
    assert ops and inv.outcome == "data" and not inv.blocked

    # the provider answered, but with an empty/no-fact payload -> clean-empty, NOT error
    empty = CapabilityLayer([PrometheusAdapter()], source=MockSource({"fetch_metrics": {}}))
    ops2, inv2 = empty.serve(CapabilityCall(intent="fetch_metrics"), allow_write=False)
    assert ops2 == [] and inv2.outcome == "empty" and not inv2.blocked and inv2.reason is None


def test_serve_transport_failure_is_recorded_error_not_a_crash():
    """A vendor 4xx/5xx/timeout — modeled here as the transport raising — must NOT raise through
    serve(). It becomes a recorded `error` Invocation carrying NO evidentiary weight (so it can
    never be misread as refuting evidence — the fabricated-negative-evidence fix)."""
    class _BoomSource:
        def fetch(self, binding, intent, params):
            raise RuntimeError("HTTP 503 Service Unavailable")

    layer = CapabilityLayer([PrometheusAdapter()], source=_BoomSource())
    ops, inv = layer.serve(CapabilityCall(intent="fetch_metrics", params={"q": "up"}),
                           allow_write=False)
    assert ops == []                          # no ops fold from a failed call
    assert inv.outcome == "error"             # NOT "empty" — this is the poison-killer
    assert not inv.blocked                    # error is a distinct axis from gate-blocked
    assert "503" in inv.reason                # the failure is captured for the audit trail
    assert inv.provider == "prometheus"       # attributed to the tool that failed
    assert inv.params == {"q": "up"}          # the query that was attempted survives


def test_serve_normalize_failure_is_recorded_error_not_a_crash():
    """A malformed vendor payload that blows up normalize() is caught the same way — an `error`
    outcome, never a session crash."""
    class _BadAdapter:
        provider = "prometheus"
        intents = frozenset({"fetch_metrics"})
        effect = Effect.READ
        binding = Binding.REST

        def normalize(self, raw):
            raise KeyError("expected 'service' in payload")

    layer = CapabilityLayer([_BadAdapter()], source=MockSource({"fetch_metrics": {"junk": 1}}))
    ops, inv = layer.serve(CapabilityCall(intent="fetch_metrics"), allow_write=False)
    assert ops == [] and inv.outcome == "error" and not inv.blocked
    assert "KeyError" in inv.reason


def test_per_intent_effect_one_adapter_hosts_read_and_write():
    """PER-INTENT effect (retiring the split-adapter workaround): one adapter can carry a read
    intent AND a write intent via an `effects` override; the gate resolves each independently."""
    class _DualAdapter:
        provider = "ocp"
        intents = frozenset({"pod_status", "ocp__restart"})
        effect = Effect.READ                       # default for the set
        effects: ClassVar = {"ocp__restart": Effect.WRITE}   # per-intent override
        binding = Binding.MCP

        def normalize(self, raw):
            return []

    layer = CapabilityLayer([_DualAdapter()])
    # the read intent is servable without a gate
    _, read_inv = layer.invoke("pod_status", {}, allow_write=False)
    assert not read_inv.blocked and read_inv.effect == Effect.READ
    # the write intent on the SAME adapter is gated
    _, w_blocked = layer.invoke("ocp__restart", {}, allow_write=False)
    assert w_blocked.blocked and w_blocked.effect == Effect.WRITE
    _, w_ok = layer.invoke("ocp__restart", {}, allow_write=True)
    assert not w_ok.blocked and w_ok.effect == Effect.WRITE
