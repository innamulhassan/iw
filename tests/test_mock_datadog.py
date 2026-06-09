"""Smoke tests for mock_datadog tool functions (Chunk 1)."""

from lunasre.mcp_servers.mock_datadog.server import drill_into_alert, tail_logs


def test_drill_into_alert_8472_db_failure():
    alert = drill_into_alert("8472")
    assert alert["alert_id"] == "8472"
    assert alert["type"] == "db-failure"
    assert alert["severity"] == "critical"
    assert alert["service"] == "payments-api"


def test_drill_into_alert_8473_network_partition():
    alert = drill_into_alert("8473")
    assert alert["type"] == "network-partition"
    assert alert["service"] == "user-service-cross-region"


def test_drill_into_alert_8474_deploy_regression():
    alert = drill_into_alert("8474")
    assert alert["type"] == "deploy-regression"
    assert alert["service"] == "search-api"


def test_drill_into_alert_missing_returns_error_dict():
    result = drill_into_alert("9999")
    assert "error" in result
    assert "8472" in result["available_ids"]


def test_tail_logs_payments_api_returns_oom():
    out = tail_logs("payments-api", n=10)
    assert out["service"] == "payments-api"
    assert len(out["lines"]) >= 1
    assert any("OOM" in line for line in out["lines"])


def test_tail_logs_unknown_service_returns_placeholder():
    out = tail_logs("unknown-service")
    assert out["service"] == "unknown-service"
    assert "no synthetic logs" in out["lines"][0]
