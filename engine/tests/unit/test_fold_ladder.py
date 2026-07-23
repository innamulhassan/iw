"""The per-source FOLD ladder (NODE-EDGE-PRIMITIVES 2026-07-23 §3/§8.1 — the SpanFold phase).

Each adapter's normalize() folds its raw stream into the RIGHT category + the unifying handle
ladder: NEVER inline a raw stream — author the judgment-granularity UNIT plus a HANDLE to re-fetch
it. One rule, three instances:
  * metrics -> a summary READING + a `metric_query` handle (prometheus, appd);
  * logs    -> the deduped cluster as an ErrorSignature node + a `log_link` handle (splunk);
  * spans/traces -> a SPAN datum + the `trace_id` correlation (appd fetch_traces).

The handles ride evidence[] (graph-internal, not bundle-serialized) and no golden scenario feeds
appd `traces`, so every rung here is golden-neutral — asserted separately by the byte-identical
golden suite. These tests inspect the adapter fold output directly.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.capability.adapters.appd import AppDAdapter
from iw_engine.capability.adapters.prometheus import PrometheusAdapter
from iw_engine.capability.adapters.splunk import SplunkAdapter
from iw_engine.domain.enums import Species
from iw_engine.domain.operations import AddAssertion

T0 = datetime(2026, 7, 19, 10, 0, 0, tzinfo=UTC)


def _readings(ops, name):
    return [o for o in ops if isinstance(o, AddAssertion) and o.name == name]


def _kinds(op) -> set[str]:
    return {e.kind for e in op.evidence}


# ── metrics = summary READING + metric_query handle ───────────────────────────────────────
def test_prometheus_metric_reading_carries_a_metric_query_handle():
    ops = PrometheusAdapter().normalize({
        "service": {"name": "payments-api", "env": "prod"},
        "metrics": [{"predicate": "error_rate", "value": 0.4, "unit": "ratio", "at": T0}],
    })
    (r,) = _readings(ops, "error_rate")
    assert r.species is Species.READING
    assert "metric_query" in _kinds(r)                         # the handle is authored beside the unit
    handle = next(e for e in r.evidence if e.kind == "metric_query")
    assert handle.label == "error_rate" and "error_rate" in handle.ref


def test_appd_bt_metric_reading_carries_a_metric_query_handle():
    ops = AppDAdapter().normalize({
        "service": {"name": "checkout-api", "env": "prod"},
        "bt": {"name": "Checkout"},
        "bt_metrics": [{"predicate": "art_p95", "value": 812, "unit": "ms", "at": T0}],
    })
    (r,) = _readings(ops, "art_p95")
    assert "metric_query" in _kinds(r)


# ── spans/traces = a SPAN datum + the trace_id correlation (the rungs) ─────────────────────
def test_appd_trace_folds_to_a_closed_span_not_an_event():
    ops = AppDAdapter().normalize({
        "service": {"name": "checkout-api", "env": "prod"},
        "bt": {"name": "Checkout"},
        "traces": [{"at": T0, "trace_id": "trace-abc", "duration_ms": 800, "error": False}],
    })
    # a trace is a bounded happening -> a SPAN, never an EVENT
    assert not [o for o in ops if isinstance(o, AddAssertion) and o.name == "trace_captured"]
    (sp,) = _readings(ops, "trace")
    assert sp.species is Species.SPAN
    assert sp.correlation_id == "trace-abc"                     # §4.4 join key = trace_id
    assert sp.valid_from == T0 and sp.valid_to == T0 + timedelta(milliseconds=800)
    assert sp.value == {"error": False}                         # outcome on value
    assert sp.source_native_name == "trace_captured"           # the vendor's own name survives


def test_appd_in_flight_trace_has_no_end_so_the_engine_derives_open():
    ops = AppDAdapter().normalize({
        "service": {"name": "checkout-api", "env": "prod"},
        "bt": {"name": "Checkout"},
        "traces": [{"at": T0, "trace_id": "trace-live"}],       # no duration_ms -> in-flight
    })
    (sp,) = _readings(ops, "trace")
    assert sp.species is Species.SPAN and sp.valid_to is None   # -> reducer stamps span_phase=OPEN


# ── logs = ErrorSignature cluster + log_link handle ───────────────────────────────────────
def test_splunk_error_signature_count_carries_a_log_link_handle():
    ops = SplunkAdapter().normalize({
        "service": {"name": "checkout-api", "env": "prod"},
        "errors": [{"signature_hash": "npe-1", "exception_class": "NullPointerException",
                    "count": 5, "_time": T0, "first_seen": T0, "trace_id": "t-9"}],
    })
    (c,) = _readings(ops, "count")
    kinds = _kinds(c)
    assert "log_link" in kinds                                  # the log-stream handle
    assert "trace_id" in kinds                                  # + the trace join when present
    link = next(e for e in c.evidence if e.kind == "log_link")
    assert "npe-1" in link.ref and link.label == "NullPointerException"


def test_splunk_log_link_present_even_without_a_trace_id():
    ops = SplunkAdapter().normalize({
        "service": {"name": "checkout-api", "env": "prod"},
        "errors": [{"signature_hash": "oom-2", "exception_class": "OOM", "count": 3, "_time": T0}],
    })
    (c,) = _readings(ops, "count")
    kinds = _kinds(c)
    assert "log_link" in kinds and "trace_id" not in kinds
