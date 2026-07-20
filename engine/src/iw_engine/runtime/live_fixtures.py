"""live_fixtures — provider-keyed fixture blobs for the LIVE (LLM-driven) path, shared by the
interactive session backend (`live_build_manager`) and the batch convergence CLI
(`scripts/run_live.py`). These are the counterpart to the hermetic `tests/e2e/scenario_*`
MockSource fixtures: here the transport is `ScenarioSource` (intent → provider routing, GAP 4)
and each blob returns REAL CONTENT (GAP 2) — the git diff carries the actual `DROP INDEX` line,
blame carries file:line, p50 stays flat — so the LLM has real evidence to reason over. The LLM
does ALL the judgment (obs 10: "you should not be in the execution"); these fixtures only stand
in for the live tool fetch.

Registry key = the scenario `key` used in the catalog (scenarios._CATALOG). Each builder returns
`(SubjectRef, fixtures, golden_root)` — golden_root is the expected converged root_candidate,
used only by the convergence check, never fed to the model.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ..domain.subject import SubjectRef


def _dt(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 19, hour, minute, tzinfo=UTC)


def code_regression() -> tuple[SubjectRef, dict, str]:
    """payments-api 5xx after v4.12.0. Golden root = the CODE_COMMIT the NPE blames to."""
    t_chg, t_on, t_inv, t_fix = _dt(13, 47), _dt(14, 0), _dt(14, 25), _dt(14, 40)
    subject = SubjectRef(domain="app-incident", id="INC-4821", kind="incident")
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-4821", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "payments-api"}, "env": "prod"},
            "changes": [{"number": "CHG-1", "type": "deployment",
                         "cmdb_ci": {"display_value": "payments-api"},
                         "requested_by": "dev-kco", "start_date": t_chg,
                         "u_commit_sha": "abc123"}],
        }},
        "cmdb": {"*": {"env": "prod", "dependencies": [
            {"parent": "payments-api", "parent_type": "cmdb_ci_service",
             "child": "payments-ora", "child_type": "cmdb_ci_database",
             "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "payments-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "High5xxRate", "at": t_on,
                              "state": "firing"}],
                  "metrics": [{"predicate": "red_errors", "value": 0.40, "at": t_on,
                               "reliability": 0.97}]},
            "verify": {"service": {"name": "payments-api", "env": "prod"},
                       "metrics": [{"predicate": "red_errors", "value": 0.01, "at": t_fix,
                                    "reliability": 0.98},
                                   {"predicate": "degraded", "value": False, "at": t_fix,
                                    "reliability": 0.98}]},
        },
        "splunk": {"*": {
            "service": {"name": "payments-api", "env": "prod"},
            "errors": [{"signature_hash": "npe-taxcalc",
                        "exception_class": "java.lang.NullPointerException",
                        "file_line": "TaxCalculator.java:88", "first_seen": t_on.isoformat(),
                        "_time": t_inv, "count": 152, "last_seen": t_inv.isoformat(),
                        "trace_id": "tr-9f2a1"}]}},
        "git": {"*": {
            "commit": {"sha": "abc123", "repo": "payments-api", "author": "dev-kco",
                       "parent_sha": "9f8e7d6", "authored_at": t_chg},
            "blame": {"sha": "abc123", "repo": "payments-api", "file": "TaxCalculator.java",
                      "line": 88, "at": t_inv, "reliability": 0.98,
                      "snippet": ("return calc.rate(order.getRegion().getCode());  "
                                  "// v4.12.0: getRegion() is null for intl orders -> NPE")},
            "error_signature_hash": "npe-taxcalc"}},
    }
    return subject, fx, "code_commit:abc123"


def database() -> tuple[SubjectRef, dict, str]:
    """orders-api p99 spike after CHG-9 dropped an index. Golden root = the CHANGE_EVENT."""
    t_chg, t_on, t_inv, t_fix = _dt(13, 57), _dt(14, 5), _dt(14, 20), _dt(14, 50)
    subject = SubjectRef(domain="app-incident", id="INC-7734", kind="incident")
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-7734", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "orders-api"}, "env": "prod"},
            "changes": [{"number": "CHG-9", "type": "database",
                         "cmdb_ci": {"display_value": "orders-api"},
                         "requested_by": "dba-jsmith", "start_date": t_chg}]}},
        "cmdb": {"*": {"env": "prod", "dependencies": [
            {"parent": "orders-api", "parent_type": "cmdb_ci_service",
             "child": "orders-pg", "child_type": "cmdb_ci_database",
             "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "orders-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "HighLatencyP99", "at": t_on,
                              "state": "firing"}],
                  "metrics": [{"predicate": "red_latency_p99", "value": 5200, "unit": "ms",
                               "at": t_on, "reliability": 0.95}]},
            "verify": {"service": {"name": "orders-api", "env": "prod"},
                       "metrics": [{"predicate": "red_latency_p99", "value": 95, "unit": "ms",
                                    "at": t_fix, "reliability": 0.95},
                                   {"predicate": "degraded", "value": False, "at": t_fix,
                                    "reliability": 0.97}]}},
        "appd": {
            "*": {"service": {"name": "orders-api", "env": "prod"},
                  "snapshots": [{"exit_calls": [{"type": "JDBC", "db_id": "orders-pg",
                                                 "engine": "postgres"}]}]},
            "investigate": {"service": {"name": "orders-api", "env": "prod"},
                            "bt_metrics": [{"predicate": "red_latency_p50", "value": 42,
                                            "unit": "ms", "at": t_inv, "reliability": 0.95},
                                           {"predicate": "red_latency_p99", "value": 7900,
                                            "unit": "ms", "at": t_inv, "reliability": 0.93}],
                            "snapshots": [{"exit_calls": [{"type": "JDBC", "db_id": "orders-pg",
                                                           "engine": "postgres"}]}]}},
        "git": {"*": {
            "change": {"change_id": "CHG-9", "change_type": "database"},
            "diff": {"at": t_chg, "files_changed": 1, "lines_added": 0, "lines_deleted": 1,
                     "reliability": 0.99,
                     "changed_lines": [
                         "- CREATE INDEX idx_order_items_order_id ON orders.order_items(order_id);",
                         "+ DROP INDEX idx_order_items_order_id;  -- reclaim write throughput"]}}},
    }
    return subject, fx, "change_event:chg-9"


def network() -> tuple[SubjectRef, dict, str]:
    """checkout->pricing timeouts after an MTU change. Golden root = the NETWORK_SEGMENT."""
    t_on, t_inv, t_fix = _dt(13, 40), _dt(13, 55), _dt(14, 20)
    subject = SubjectRef(domain="app-incident", id="INC-9001", kind="incident")
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-9001", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-svc"}, "env": "prod"},
            "changes": []}},
        "cmdb": {"*": {"env": "prod",
                       "ci_attrs": {"SEG-EDGE-12": {"cidr": "10.20.4.0/24", "vlan": 204}},
                       "dependencies": [
                           {"parent": "checkout-svc", "parent_type": "cmdb_ci_service",
                            "child": "SEG-EDGE-12", "child_type": "cmdb_ci_network_segment",
                            "rel_type": "Connects to::Connected by"},
                           {"parent": "pricing-svc", "parent_type": "cmdb_ci_service",
                            "child": "pricing-db", "child_type": "cmdb_ci_database",
                            "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "checkout-svc", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "HighRetransSegs", "at": t_on,
                              "state": "firing"}],
                  "metrics": [{"predicate": "degraded", "value": True, "at": t_on,
                               "reliability": 0.95}]},
            "investigate": {"service": {"name": "checkout-svc", "env": "prod"},
                            "metrics": [
                                {"subject": "network_segment:seg-edge-12",
                                 "predicate": "retrans_segs", "value": 245, "unit": "count",
                                 "at": t_inv, "reliability": 0.97},
                                {"subject": "network_segment:seg-edge-12",
                                 "predicate": "probe_success", "value": 0.42, "unit": "ratio",
                                 "at": t_inv, "reliability": 0.95},
                                {"predicate": "red_latency_p50", "value": 38, "unit": "ms",
                                 "at": t_inv, "reliability": 0.95},
                                {"subject": "database:pricing-db", "predicate": "conn_pool_util",
                                 "value": 0.24, "at": t_inv, "reliability": 0.98}]},
            "verify": {"service": {"name": "checkout-svc", "env": "prod"},
                       "metrics": [
                           {"subject": "network_segment:seg-edge-12", "predicate": "retrans_segs",
                            "value": 8, "unit": "count", "at": t_fix, "reliability": 0.97},
                           {"subject": "network_segment:seg-edge-12", "predicate": "probe_success",
                            "value": 0.99, "unit": "ratio", "at": t_fix, "reliability": 0.97},
                           {"predicate": "degraded", "value": False, "at": t_fix,
                            "reliability": 0.97}]}},
        "appd": {"*": {"service": {"name": "checkout-svc", "env": "prod"},
                       "bt": {"name": "CheckoutFlow"},
                       "bt_metrics": [{"predicate": "art_p95", "value": 4800, "unit": "ms",
                                       "at": t_on, "reliability": 0.95},
                                      {"predicate": "delta_vs_baseline", "value": 3.2,
                                       "at": t_on, "reliability": 0.9}],
                       "flowmap": [{"exit_calls": [{"type": "HTTP", "target_service": "pricing-svc",
                                                    "target_env": "prod"}]}]}},
    }
    return subject, fx, "network_segment:seg-edge-12"


# registry key = catalog `key`; extended per-layer for obs 11 (>=2 use cases per layer)
LIVE_SCENARIOS: dict[str, Callable[[], tuple[SubjectRef, dict, str]]] = {
    "code_regression": code_regression,
    "database": database,
    "network": network,
}
