"""Tests for the vendor->adapter response mapping (the live-tools seam).

Fixtures (MockSource/ScenarioSource) are already adapter-shaped and bypass mapping;
this layer exists so REAL tool responses — a Prometheus /api/v1/query envelope, a
ServiceNow result[] array, etc. — fold cleanly through the adapters instead of
crashing on the bracket-access the adapters do on required fields.
"""
from __future__ import annotations

from iw_engine.capability.mapping import MappingSource, map_response
from iw_engine.domain.enums import Binding


# ── map_response dispatch ──────────────────────────────────────────────────────
def test_unknown_provider_passes_through():
    """No translator registered -> vendor JSON returned unchanged. Correct when the
    vendor already emits the adapter shape (e.g. a custom MCP server)."""
    raw = {"anything": 1, "nested": {"a": 2}}
    assert map_response("mystery", "some_intent", raw) is raw


def test_prometheus_query_envelope_to_adapter_metrics():
    """The Prometheus /api/v1/query shape -> the adapter's {metrics:[...]} shape."""
    vendor = {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {"__name__": "red_errors", "service": "payments-api"},
         "value": [1689770000.0, "0.4"]},
        {"metric": {"__name__": "red_latency_p99", "service": "payments-api"},
         "value": [1689770000.0, "2.1e3"]},
    ]}}
    out = map_response("prometheus", "instant_query", vendor)
    assert "metrics" in out and len(out["metrics"]) == 2
    m0 = out["metrics"][0]
    assert m0["predicate"] == "red_errors"
    assert m0["value"] == 0.4                       # coerced from string
    assert m0["at"].endswith("Z")                   # unix -> ISO
    assert out["metrics"][1]["value"] == 2100.0     # "2.1e3" coerced


def test_prometheus_alerts_envelope_to_adapter_alerts():
    vendor = {"data": {"alerts": [
        {"labels": {"alertname": "HighErrorRate", "alert_id": "ALT-1"},
         "state": "firing", "activeAt": "2026-07-19T14:00:00Z"},
    ]}}
    out = map_response("prometheus", "active_alerts", vendor)
    assert len(out["alerts"]) == 1
    assert out["alerts"][0]["id"] == "ALT-1"
    assert out["alerts"][0]["state"] == "firing"


def test_servicenow_result_envelope_unwrapped():
    """ServiceNow REST wraps the payload as {"result": <obj>}; the adapter uses native
    field names so we just unwrap."""
    vendor = {"result": {"number": "INC-1", "opened_at": "2026-07-19T14:00:00Z",
                         "priority": "2 - High"}}
    out = map_response("servicenow", "get_incident", vendor)
    assert out["number"] == "INC-1"
    assert out["opened_at"] == "2026-07-19T14:00:00Z"


def test_servicenow_result_list_becomes_changes():
    """A bare result[] array (find_recent_changes) maps to the adapter's 'changes' key."""
    vendor = {"result": [{"number": "CHG-9", "start_date": "2026-07-19T13:57:00Z"}]}
    out = map_response("servicenow", "find_recent_changes", vendor)
    assert out["changes"][0]["number"] == "CHG-9"


def test_servicenow_already_adapter_shaped_passes_through():
    raw = {"incident": {"number": "INC-1"}, "changes": []}
    out = map_response("servicenow", "get_incident", raw)
    assert out["incident"]["number"] == "INC-1"


def test_splunk_results_list_becomes_errors():
    vendor = {"results": [{"signature_hash": "npe", "_time": "2026-07-19T14:00:00Z", "count": 5}]}
    out = map_response("splunk", "search_errors", vendor)
    assert out["errors"][0]["signature_hash"] == "npe"


def test_broken_translator_does_not_crash():
    """A translator that raises must fall back to the raw vendor JSON — a bad mapping
    can never crash a live investigation. We force a failure via a malformed input."""
    # prometheus translator accesses raw["data"]["result"]; a non-dict "data" would
    # raise inside — map_response must swallow it and return the input unchanged.
    vendor = {"data": "not-a-dict"}
    out = map_response("prometheus", "instant_query", vendor)
    assert out == vendor   # pass-through on translator failure


# ── MappingSource composition ──────────────────────────────────────────────────
class _FakeSource:
    """A Source stub returning a canned vendor payload per intent."""
    def __init__(self, payloads: dict[str, dict]):
        self._p = payloads

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        return self._p.get(intent, {})


def test_mapping_source_translates_inner_fetch():
    """MappingSource wraps a transport and maps its vendor JSON before returning."""
    inner = _FakeSource({"instant_query": {
        "data": {"result": [{"metric": {"__name__": "red_errors"},
                             "value": [1689770000.0, "0.4"]}]}}})
    ms = MappingSource(inner, intent_provider={"instant_query": "prometheus"})
    out = ms.fetch(Binding.REST, "instant_query", {})
    assert "metrics" in out and out["metrics"][0]["value"] == 0.4


def test_mapping_source_passthrough_for_unknown_provider():
    """An intent with no provider in intent_provider -> no translation (pass-through)."""
    inner = _FakeSource({"weird_intent": {"raw": 1}})
    ms = MappingSource(inner, intent_provider={})
    assert ms.fetch(Binding.REST, "weird_intent", {}) == {"raw": 1}


# ── S1.5 (P6 convergence): call params forwarded to translators ────────────────
def test_prometheus_query_builds_service_block_from_params():
    """A Prometheus vector carries labels, not the identity the adapter mints the node
    from — that identity lives in the CALL PARAMS, now forwarded (S1.5). With both
    service+env present the translator supplies the adapter's `service` block, so live
    REST metrics attach to the right entity instead of being dropped subject-less."""
    vendor = {"data": {"result": [
        {"metric": {"__name__": "red_errors"}, "value": [1689770000.0, "0.4"]}]}}
    out = map_response("prometheus", "fetch_metrics", vendor,
                       {"service": "payments-api", "env": "prod"})
    assert out["service"] == {"name": "payments-api", "env": "prod"}
    assert out["metrics"][0]["predicate"] == "red_errors"


def test_prometheus_query_omits_half_an_identity():
    """service without env (or neither) must OMIT the block — a half identity would mint
    a degenerate node id; omission is honest (per-sample `subject` still works)."""
    vendor = {"data": {"result": []}}
    assert "service" not in map_response("prometheus", "fetch_metrics", vendor,
                                         {"service": "payments-api"})
    assert "service" not in map_response("prometheus", "fetch_metrics", vendor, {})
    assert "service" not in map_response("prometheus", "fetch_metrics", vendor, None)


def test_single_arg_translators_still_work_with_params():
    """Back-compat: a (raw)-only translator is wrapped at registration — params flow
    through the dispatch without touching it."""
    out = map_response("servicenow", "get_incident",
                       {"result": {"number": "INC-1"}}, {"sys_id": "abc"})
    assert out["number"] == "INC-1"


def test_mapping_source_forwards_params():
    class _Inner:
        def fetch(self, binding, intent, params):
            return {"data": {"result": [
                {"metric": {"__name__": "red_errors"}, "value": [1689770000.0, "0.4"]}]}}

    src = MappingSource(_Inner(), {"fetch_metrics": "prometheus"})
    out = src.fetch(Binding.REST, "fetch_metrics",
                    {"service": "payments-api", "env": "prod"})
    assert out["service"] == {"name": "payments-api", "env": "prod"}
