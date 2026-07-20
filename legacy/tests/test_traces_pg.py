"""Tests for the mock_traces + mock_pg MCP servers (Phase 2, one-shot tools).

These call the tool functions directly (no MCP transport, no LLM)."""

from __future__ import annotations

from lunasre.mcp_servers.mock_pg.server import connection_pool_status, query_metric
from lunasre.mcp_servers.mock_traces.server import find_slow_spans, get_trace


def test_get_trace_cross_region_has_timeout_spans():
    trace = get_trace("user-service-cross-region")
    assert trace["service"] == "user-service-cross-region"
    statuses = {s["status"] for s in trace["spans"]}
    assert "timeout" in statuses
    azs = {s.get("az") for s in trace["spans"]}
    assert {"us-east-1a", "us-east-1b"} <= azs


def test_find_slow_spans_ranks_by_duration():
    out = find_slow_spans("user-service-cross-region", threshold_ms=1000)
    assert out["slow_span_count"] >= 2
    durations = [s["duration_ms"] for s in out["slow_spans"]]
    assert durations == sorted(durations, reverse=True)


def test_get_trace_unknown_service_errors():
    out = get_trace("no-such-service")
    assert "error" in out


def test_pg_connection_pool_saturated():
    out = connection_pool_status("payments-api")
    assert out["saturated"] is True
    assert out["utilization_pct"] == 100.0
    assert out["replica_lag_s"] == 55


def test_pg_query_metric_series():
    out = query_metric("payments-api", "replica_lag_s")
    assert out["series"][-1] == 55


def test_pg_unknown_metric_errors():
    out = query_metric("payments-api", "nonexistent")
    assert "error" in out
