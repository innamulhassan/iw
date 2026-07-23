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
            "incident": {"number": "INC-4821",
                         "title": "payments-api elevated 5xx errors",
                         "short_description": "payments-api 5xx spiked to 40% ~13m after the v4.12.0 deploy",
                         "description": (
                             "PagerDuty routed High5xxRate to SRE at 14:00 UTC. payments-api "
                             "(prod, tier-1) is returning HTTP 5xx on ~40% of requests; throughput "
                             "is holding but the error tail is dragging p99 to 4.2s while p50 stays "
                             "flat. Onset is 13 minutes after release v4.12.0 rolled out at 13:47. "
                             "Card auth + checkout capture are impacted; revenue-affecting. "
                             "Investigating the deploy as the prime suspect."),
                         "work_notes": (
                             "[14:02] sre-oncall: High5xxRate paged; errors at 40% on payments-api. "
                             "v4.12.0 shipped 13:47, ~13m before onset — change-first prime suspect.\n"
                             "[14:26] sre-oncall: Splunk shows a NullPointerException in "
                             "TaxCalculator, first seen at onset; payments-ora pool healthy (28%). "
                             "Staging rollback to v4.11.3."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Payments Platform",
                         "business_service": "Payments", "impact": "2 - Medium",
                         "urgency": "1 - High", "category": "Software",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "payments-api",
                                     "app_id": "APM-PAYMEN",
                                     "sys_id": "sn_paymentsapi01",
                                     "repo": "payments-api",
                                     "k8s_workload": "prod/payments-api",
                                     "owner": "payments-platform@corp.example",
                                     "support_group": "SRE - Payments Platform",
                                     "version": "v4.12.0", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"
                                    }, "env": "prod"},
            "changes": [{"number": "CHG-1", "type": "deployment",
                         "short_description": "Deploy payments-api v4.12.0 to prod (intl tax-calc)",
                         "description": (
                             "Standard release of payments-api v4.12.0 via Argo Rollouts. Bumps the "
                             "shared taxcalc library to add intl VAT regions. Blue/green with a 10% "
                             "canary, auto-promoted after the 5-minute analysis gate passed."),
                         "cmdb_ci": {"display_value": "payments-api",
                                     "app_id": "APM-PAYMEN",
                                     "sys_id": "sn_paymentsapi01",
                                     "repo": "payments-api",
                                     "k8s_workload": "prod/payments-api",
                                     "owner": "payments-platform@corp.example",
                                     "version": "v4.12.0"
                                    },
                         "requested_by": "dev-kco", "start_date": t_chg,
                         "assignment_group": "Payments Engineering",
                         "risk": "Moderate", "impact": "3 - Low",
                         "state": "Implemented", "close_code": "successful",
                         "implementation_plan": "argocd app sync payments-api --revision v4.12.0",
                         "backout_plan": "argocd app rollback payments-api v4.11.3",
                         "u_commit_sha": "abc123"}],
        }},
        "cmdb": {"*": {"env": "prod", "dependencies": [
            {"parent": "payments-api", "parent_type": "cmdb_ci_service",
             "child": "payments-ora", "child_type": "cmdb_ci_database",
             "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "payments-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "High5xxRate", "at": t_on,
                              "state": "firing", "severity": "critical", "for": "5m",
                              "runbook_url": "https://runbooks.corp.example/High5xxRate",
                              "labels": {"service": "payments-api", "env": "prod",
                                         "team": "payments", "namespace": "payments-prod"},
                              "expr": "sum(rate(http_requests_total{code=~\"5..\","
                                      "service=\"payments-api\"}[5m])) / "
                                      "sum(rate(http_requests_total{service=\"payments-api\"}[5m])) "
                                      "> 0.05"}],
                  "metrics": [{"predicate": "red_errors", "value": 0.40, "at": t_on,
                               "reliability": 0.97, "unit": "ratio",
                               "metric": "http_requests_error_ratio",
                               "labels": {"service": "payments-api", "env": "prod",
                                          "instance": "10.42.7.13:9102"}}]},
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
                        "trace_id": "tr-9f2a1", "level": "ERROR", "host": "payments-api-7d9c-k2x9",
                        "sourcetype": "log4j2", "logger": "com.corp.payments.tax.TaxCalculator",
                        "index": "prod_payments", "span_id": "b7ad6b70",
                        "message": ("Cannot invoke \"com.corp.geo.Region.getCode()\" because the "
                                    "return value of \"com.corp.order.Order.getRegion()\" is null"),
                        "stack": [
                            "java.lang.NullPointerException: Cannot invoke "
                            "\"com.corp.geo.Region.getCode()\" because \"order.getRegion()\" is null",
                            "\tat com.corp.payments.tax.TaxCalculator.rate(TaxCalculator.java:88)",
                            "\tat com.corp.payments.tax.TaxCalculator.compute(TaxCalculator.java:54)",
                            "\tat com.corp.payments.charge.CaptureService.capture(CaptureService.java:210)",
                            "\tat com.corp.payments.api.ChargeController.post(ChargeController.java:96)"]}]}},
        "git": {"*": {
            "commit": {"sha": "abc123", "repo": "payments-api", "author": "dev-kco",
                       "parent_sha": "9f8e7d6", "authored_at": t_chg, "branch": "main",
                       "message": ("feat(tax): add intl VAT regions via shared taxcalc v4.12.0\n\n"
                                   "Route international orders through Region-aware rate lookup. "
                                   "PR #1487, reviewed by dev-lho."),
                       "files_changed": 6, "tag": "v4.12.0"},
            "blame": {"sha": "abc123", "repo": "payments-api", "file": "TaxCalculator.java",
                      "line": 88, "at": t_inv, "reliability": 0.98, "author": "dev-kco",
                      "committed_at": t_chg.isoformat(),
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
            "incident": {"number": "INC-7734",
                         "title": "orders-api p99 latency spike",
                         "short_description": "orders-api p99 hit 5.2s ~8m after CHG-9 dropped an index",
                         "description": (
                             "HighLatencyP99 fired for orders-api (prod, tier-1) at 14:05 UTC. p99 "
                             "response time jumped from ~90ms to 5.2s while p50 stayed flat at ~42ms "
                             "— a tail-only regression, classic of a query that lost its index. "
                             "Order submission is slow but not erroring. Change CHG-9 (a DBA index "
                             "drop on orders.order_items) landed at 13:57, ~8m before onset."),
                         "work_notes": (
                             "[14:07] sre-oncall: HighLatencyP99 fired; p99 5.2s, p50 flat at 42ms. "
                             "Tail-only shape points at a query plan change, not a code path.\n"
                             "[14:22] sre-oncall: AppD confirms JDBC exit-call to orders-pg is the "
                             "hot span. CHG-9 dropped idx_order_items_order_id at 13:57 — full scans. "
                             "Requesting index re-create."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Orders Platform",
                         "business_service": "Order Management", "impact": "2 - Medium",
                         "urgency": "2 - Medium", "category": "Database",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "orders-api",
                                     "app_id": "APM-ORDERS",
                                     "sys_id": "sn_ordersapi01",
                                     "repo": "orders-api",
                                     "k8s_workload": "prod/orders-api",
                                     "owner": "orders-platform@corp.example",
                                     "support_group": "SRE - Orders Platform",
                                     "version": "v7.8.1", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"
                                    }, "env": "prod"},
            "changes": [{"number": "CHG-9", "type": "database",
                         "short_description": "Drop unused index idx_order_items_order_id (reclaim write IOPS)",
                         "description": (
                             "DBA-approved schema change on orders-pg. Flyway migration V812 drops "
                             "idx_order_items_order_id, flagged 'unused' by pg_stat_user_indexes over "
                             "the prior 7 days, to reclaim write throughput on the hot order_items "
                             "table. Reviewed at CAB; no application read-path analysis attached."),
                         "cmdb_ci": {"display_value": "orders-api",
                                     "app_id": "APM-ORDERS",
                                     "sys_id": "sn_ordersapi01",
                                     "repo": "orders-api",
                                     "k8s_workload": "prod/orders-api",
                                     "owner": "orders-platform@corp.example",
                                     "version": "v7.8.1"
                                    },
                         "requested_by": "dba-jsmith", "start_date": t_chg,
                         "assignment_group": "Database Administration",
                         "risk": "Low", "impact": "3 - Low", "state": "Implemented",
                         "close_code": "successful",
                         "implementation_plan": "flyway migrate -target 812  # DROP INDEX idx_order_items_order_id",
                         "backout_plan": ("CREATE INDEX CONCURRENTLY idx_order_items_order_id "
                                          "ON orders.order_items(order_id)")}]}},
        "cmdb": {"*": {"env": "prod", "dependencies": [
            {"parent": "orders-api", "parent_type": "cmdb_ci_service",
             "child": "orders-pg", "child_type": "cmdb_ci_database",
             "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "orders-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "HighLatencyP99", "at": t_on,
                              "state": "firing", "severity": "critical", "for": "5m",
                              "runbook_url": "https://runbooks.corp.example/HighLatencyP99",
                              "labels": {"service": "orders-api", "env": "prod", "team": "orders",
                                         "namespace": "orders-prod"},
                              "expr": "histogram_quantile(0.99, sum(rate("
                                      "http_request_duration_seconds_bucket{service=\"orders-api\"}"
                                      "[5m])) by (le)) > 1"}],
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
            # a pure schema migration — no application commit, so the diff attaches to the
            # CHANGE (the actionable root per the rooting doctrine), never a CodeCommit.
            "diff": {"at": t_chg, "files_changed": 1, "lines_added": 0, "lines_deleted": 1,
                     "reliability": 0.99, "author": "dba-jsmith", "repo": "orders-db-migrations",
                     "migration_id": "V812__drop_unused_order_items_index.sql", "tool": "flyway",
                     "path": "db/migrations/V812__drop_unused_order_items_index.sql",
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
            "incident": {"number": "INC-9001",
                         "title": "checkout-svc -> pricing-svc timeouts",
                         "short_description": "checkout-svc -> pricing-svc timeouts after an MTU change",
                         "description": (
                             "HighRetransSegs fired for the SEG-EDGE-12 uplink at 13:40 UTC. "
                             "checkout-svc calls to pricing-svc are timing out (~4.8s p95 on "
                             "CheckoutFlow); synthetic probes across SEG-EDGE-12 are failing ~58% "
                             "with heavy TCP retransmits. No application deploy in the window — "
                             "smells like an L2/L3 path change (MTU/uplink) on the edge segment."),
                         "work_notes": (
                             "[13:42] sre-oncall: HighRetransSegs on SEG-EDGE-12; checkout->pricing "
                             "timing out. No app change logged — network path suspected.\n"
                             "[13:56] sre-oncall: retrans_segs 245, probe_success 42% on SEG-EDGE-12; "
                             "pricing-db pool healthy (24%). Paging netops re: recent MTU/uplink work."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Checkout",
                         "business_service": "Checkout", "impact": "1 - High",
                         "urgency": "1 - High", "category": "Network",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-svc",
                                     "app_id": "APM-CHECKO",
                                     "sys_id": "sn_checkoutsvc01",
                                     "repo": "checkout-svc",
                                     "k8s_workload": "prod/checkout-svc",
                                     "owner": "checkout-platform@corp.example",
                                     "support_group": "SRE - Checkout",
                                     "version": "v9.2.0", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"
                                    }, "env": "prod"},
            "changes": []}},
        "cmdb": {"*": {"env": "prod",
                       "ci_attrs": {"SEG-EDGE-12": {
                           "cidr": "10.20.4.0/24", "vlan": 204, "mtu": 1400,
                           "device": "edge-agg-2.dc1", "interface": "Ethernet1/14",
                           "managed_by": "netops-team", "owner": "netops@corp.example",
                           "provider": "internal-fabric"}},
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
                              "state": "firing", "severity": "warning", "for": "3m",
                              "runbook_url": "https://runbooks.corp.example/HighRetransSegs",
                              "labels": {"service": "checkout-svc", "env": "prod", "team": "checkout",
                                         "segment": "SEG-EDGE-12", "namespace": "checkout-prod"},
                              "expr": "rate(node_netstat_Tcp_RetransSegs{segment=\"SEG-EDGE-12\"}"
                                      "[5m]) > 100"}],
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


def deployment() -> tuple[SubjectRef, dict, str]:
    """checkout-api rev43 removed the required ConfigMap key DB_HOST -> the container panics on
    boot, so the pod NEVER reaches Ready (CrashLoopBackOff) and the rollout stalls
    (ProgressDeadlineExceeded). Golden root = the deploy CHANGE_EVENT (the actionable, rollback-
    able unit; rev43/PR #482/commit 9f2a1e0 are its introduced-by mechanism). Discriminator: the
    pod never reaches Ready (config panic BEFORE any DB dial; pod CPU/mem negligible -> not OOM)
    and checkout-db's pool is healthy at 24% -> refutes the declared-DB-dependency rival. Recovery
    (rollback to rev42) lands in the verify-phase blobs."""
    t_dep, t_on, t_inv, t_fix = _dt(9, 0), _dt(9, 4), _dt(9, 18), _dt(9, 40)
    subject = SubjectRef(domain="app-incident", id="INC-7731", kind="incident")
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-7731",
                         "title": "checkout-api pods CrashLoopBackOff",
                         "short_description": "checkout-api rev43 rollout stuck 0/3 ready; pods crash-loop",
                         "description": (
                             "KubePodCrashLooping + ProgressDeadlineExceeded fired for checkout-api "
                             "(prod, tier-1) at 09:04 UTC. The rev43 rollout is stuck at 0/3 "
                             "available replicas; pods enter CrashLoopBackOff with 14 restarts and "
                             "never reach Ready. Availability has collapsed and checkout is down. "
                             "Deploy CHG-DEP-99 (checkout-api-rev43, commit 9f2a1e0) began at 09:00."),
                         "work_notes": (
                             "[09:05] svc-deploy-bot: rev43 rollout stuck 0/3; ProgressDeadlineExceeded. "
                             "Pods CrashLoopBackOff, restart_count climbing.\n"
                             "[09:19] sre-oncall: pod logs show a boot panic on a missing DB_HOST "
                             "config key — dies before any DB dial (CPU/mem negligible, not OOM). "
                             "checkout-db pool healthy. Rolling back to rev42."),
                         "caller_id": "svc-deploy-bot",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Checkout",
                         "business_service": "Checkout", "impact": "1 - High",
                         "urgency": "1 - High", "category": "Deployment",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-api",
                                     "app_id": "APM-CHECKO",
                                     "sys_id": "sn_checkoutapi01",
                                     "repo": "checkout-api",
                                     "k8s_workload": "prod/checkout-api",
                                     "owner": "checkout-platform@corp.example",
                                     "support_group": "SRE - Checkout",
                                     "version": "rev43", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"
                                    }, "env": "prod"},
            "changes": [{"number": "CHG-DEP-99", "type": "deployment",
                         "short_description": "Deploy checkout-api rev43 (PR #482 — config cleanup)",
                         "description": (
                             "Automated deploy of checkout-api rev43 via the delivery pipeline. "
                             "PR #482 'chore(config): prune unused ConfigMap keys' merged as commit "
                             "9f2a1e0. Standard rolling update, maxUnavailable=0, "
                             "progressDeadlineSeconds=120."),
                         "cmdb_ci": {"display_value": "checkout-api",
                                     "app_id": "APM-CHECKO",
                                     "sys_id": "sn_checkoutapi01",
                                     "repo": "checkout-api",
                                     "k8s_workload": "prod/checkout-api",
                                     "owner": "checkout-platform@corp.example",
                                     "version": "rev43"
                                    },
                         "requested_by": "svc-deploy-bot", "start_date": t_dep, "env": "prod",
                         "assignment_group": "Checkout Engineering",
                         "risk": "Moderate", "impact": "2 - Medium", "state": "Implemented",
                         "close_code": "unsuccessful",
                         "implementation_plan": "kubectl -n checkout-prod set image deploy/checkout-api ...:rev43",
                         "backout_plan": "kubectl -n checkout-prod rollout undo deploy/checkout-api",
                         "u_release_tag": "checkout-api-rev43", "u_commit_sha": "9f2a1e0"}]}},
        "cmdb": {"*": {"env": "prod", "dependencies": [
            {"parent": "checkout-api", "parent_type": "cmdb_ci_service",
             "child": "checkout-db", "child_type": "cmdb_ci_database",
             "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            # onset RED: 0 ready replicas -> availability collapsed, near-100% of the trickle 5xx.
            "*": {"service": {"name": "checkout-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "KubePodCrashLooping", "at": t_on,
                              "state": "firing", "severity": "critical", "for": "2m",
                              "runbook_url": "https://runbooks.corp.example/KubePodCrashLooping",
                              "labels": {"service": "checkout-api", "env": "prod", "team": "checkout",
                                         "namespace": "checkout-prod", "deployment": "checkout-api"},
                              "expr": "max_over_time(kube_pod_container_status_restarts_total{"
                                      "namespace=\"checkout-prod\"}[10m]) > 5"}],
                  "metrics": [{"predicate": "degraded", "value": True, "at": t_on,
                               "reliability": 0.98},
                              {"predicate": "red_errors", "value": 1.0, "at": t_on,
                               "reliability": 0.98},
                              {"predicate": "red_rate", "value": 38, "unit": "rpm", "at": t_on,
                               "reliability": 0.97}]},
            # investigate: checkout-db's full USE pull — healthy on every axis (the pod never even
            # opened a connection), so the declared-DB-dependency rival is ruled out on evidence.
            "investigate": {"service": {"name": "checkout-api", "env": "prod"},
                            "metrics": [
                                {"subject": "database:checkout-db", "predicate": "conn_pool_util",
                                 "value": 0.24, "at": t_inv, "reliability": 0.99},
                                {"subject": "database:checkout-db", "predicate": "active_connections",
                                 "value": 41, "unit": "conn", "at": t_inv, "reliability": 0.99},
                                {"subject": "database:checkout-db", "predicate": "max_connections",
                                 "value": 200, "unit": "conn", "at": t_inv, "reliability": 0.99},
                                {"subject": "database:checkout-db", "predicate": "slow_query_rate",
                                 "value": 1, "unit": "per_min", "at": t_inv, "reliability": 0.98},
                                {"subject": "database:checkout-db", "predicate": "replication_lag",
                                 "value": 0.1, "unit": "s", "at": t_inv, "reliability": 0.98}]},
            "verify": {"service": {"name": "checkout-api", "env": "prod"},
                       "metrics": [{"predicate": "degraded", "value": False, "at": t_fix,
                                    "reliability": 0.98},
                                   {"predicate": "red_errors", "value": 0.0, "at": t_fix,
                                    "reliability": 0.98}]}},
        "ocp": {
            # the whole rollout+pod+events+logs picture in one blob (ScenarioSource routes every
            # ocp intent to this phase blob, and OcpAdapter folds every key present): rollout stuck
            # at 0/3 available, pod CrashLoopBackOff + ready=False (THE discriminator — never Ready),
            # negligible CPU/mem (dies in boot/config, not OOM), and the boot panic in the logs.
            "*": {
                "deployment": {"uid": "dep-checkout-api-7f9d8", "name": "checkout-api",
                               "namespace": "checkout-prod", "at": t_on,
                               "image": "registry/checkout-api:rev43", "available_replicas": 0,
                               "desired_replicas": 3, "rollout_progress": 0},
                "rollout": {"status": "rollback", "reason": "ProgressDeadlineExceeded",
                            "previous_image": "registry/checkout-api:rev42", "at": t_on},
                "release": {"release_id": "checkout-api-rev43", "version": "43", "at": t_dep},
                "pods": [{"uid": "pod-checkout-api-7f9d8-x1", "name": "checkout-api-7f9d8-x1",
                          "namespace": "checkout-prod", "at": t_on, "phase": "CrashLoopBackOff",
                          "ready": False, "node_name": "k8s-node-17.prod.internal",
                          "restart_count": 14, "cpu_utilization": 0.02, "mem_utilization": 0.05}],
                "events": [
                    {"involved_object": {"kind": "Deployment", "uid": "dep-checkout-api-7f9d8",
                                         "name": "checkout-api", "namespace": "checkout-prod"},
                     "reason": "ProgressDeadlineExceeded", "at": t_on,
                     "message": "Deployment does not have minimum availability."},
                    {"involved_object": {"kind": "Pod", "uid": "pod-checkout-api-7f9d8-x1",
                                         "name": "checkout-api-7f9d8-x1",
                                         "namespace": "checkout-prod"},
                     "reason": "BackOff", "at": t_on,
                     "message": "Back-off restarting failed container"}],
                "pod": {"uid": "pod-checkout-api-7f9d8-x1", "name": "checkout-api-7f9d8-x1",
                        "namespace": "checkout-prod"},
                "logs": [
                    {"line": 'panic: config.MustGet("DB_HOST"): required key missing '
                             "-> CrashLoopBackOff", "at": t_on, "level": "fatal",
                     "container": "checkout-api", "stream": "stderr",
                     "stack": [
                         'panic: config.MustGet("DB_HOST"): required key missing',
                         "\tgithub.com/corp/checkout-api/config.(*Config).MustGet(loader.go:57)",
                         "\tgithub.com/corp/checkout-api/config.Load(loader.go:31)",
                         "\tmain.main(main.go:24)",
                         "\truntime.main(proc.go:271)"]}]},
            # verify: rollback to rev42 -> available 3/3, rollout complete, pod Running + Ready.
            "verify": {
                "deployment": {"uid": "dep-checkout-api-7f9d8", "name": "checkout-api",
                               "namespace": "checkout-prod", "at": t_fix,
                               "image": "registry/checkout-api:rev42", "available_replicas": 3,
                               "desired_replicas": 3, "rollout_progress": 100},
                "rollout": {"status": "complete", "at": t_fix},
                "pods": [{"uid": "pod-checkout-api-7f9d8-x1", "name": "checkout-api-7f9d8-x1",
                          "namespace": "checkout-prod", "at": t_fix, "phase": "Running",
                          "ready": True, "node_name": "k8s-node-17.prod.internal",
                          "restart_count": 14, "cpu_utilization": 0.18, "mem_utilization": 0.30}]}},
        "git": {"*": {
            "commit": {"sha": "9f2a1e0", "repo": "checkout-api", "author": "jdoe",
                       "parent_sha": "7a1c220", "authored_at": t_dep, "branch": "main",
                       "message": ("chore(config): prune unused ConfigMap keys\n\n"
                                   "Drop DB_HOST/CACHE_TTL from checkout-config; assumed dead. "
                                   "PR #482."),
                       "files_changed": 1, "tag": "rev43"},
            "pr": {"pr_id": "482", "repo": "checkout-api", "author": "jdoe",
                   "merged_sha": "9f2a1e0", "event": "merged", "at": t_dep,
                   "title": "chore(config): prune unused ConfigMap keys",
                   "reviewers": ["dev-sre"], "approved_by": "dev-sre"},
            "change": {"change_id": "CHG-DEP-99", "change_type": "deployment"},
            # the diff is a DEPLOY-MANIFEST edit: PR #482 deleted the DB_HOST ConfigMap key (3
            # lines) — a deployment-config change, so the actionable root is the deploy CHANGE.
            "diff": {"at": t_inv, "files_changed": 1, "lines_added": 0, "lines_deleted": 3,
                     "reliability": 0.98,
                     "changed_lines": [
                         "- - name: DB_HOST",
                         "-   valueFrom:",
                         "-     configMapKeyRef: {name: checkout-config, key: db.host}"]},
            # blame pins WHERE it panics: the pre-existing loader still MustGet's the now-missing key.
            "blame": {"sha": "9f2a1e0", "repo": "checkout-api", "file": "config/loader.go",
                      "line": 57, "at": t_inv, "reliability": 0.98,
                      "snippet": ('cfg.MustGet("DB_HOST")  // rev43 (PR #482) removed the DB_HOST '
                                  "ConfigMap key -> MustGet panics on boot, before any DB dial")},
            "error_signature": {"signature_hash": "cfg-missing-dbhost",
                                "exception_class": "ConfigMissingError", "first_seen": t_on},
            "error_signature_hash": "cfg-missing-dbhost"}},
    }
    return subject, fx, "code_commit:9f2a1e0"


def firewall() -> tuple[SubjectRef, dict, str]:
    """checkout-api's egress calls to a third-party fraud-scoring vendor start failing 7 min
    after CHG-3311 ('tighten egress ACL on prod-vpc') tightened FirewallRule FW-EGR-118.
    Golden root = the FIREWALL_RULE. Discriminators the live model must reason over:
    Splunk shows CLEAN policy denies (action=blocked, 214 hits) on FW-EGR-118; Prometheus
    blackbox probe_success=0 to egress-fraud-score ALONE (siblings egress-geoip/egress-
    payment-gw stay 1) with packet_loss=0.0% and retrans_segs=0 -> refutes a physical link
    flap; the vendor endpoint's availability=1.0 (probed directly) with error_rate=0.98 and a
    collapsed call_rate -> refutes a vendor outage (the block is on OUR side). AppD's flowmap
    discovers the vendor exit-call (DEPENDS_ON) and its BusinessTransaction is RED. ServiceNow
    carries the change-first prime suspect (CHG-3311, a network change to checkout-api) plus a
    RECURRENCE_OF prior (INC-7699, same rule last time). Recovery in verify: post-revert
    probe_success returns to 1, denies stop, red_errors back to 1%.

    NOTE: the scripted twin attributes the vendor availability/error facts to AppD, but the
    live AppD adapter only folds facts onto the BT/Service (never an arbitrary external-service
    node), so those external-service facts are carried by Prometheus (a blackbox probe of the
    vendor endpoint, explicit subject) while AppD still discovers the external service via its
    flowmap exit-call and carries the BT-level RED."""
    t_chg, t_on, t_inv, t_fix, t_prior = _dt(9, 5), _dt(9, 12), _dt(9, 25), _dt(9, 50), _dt(8, 0)
    subject = SubjectRef(domain="app-incident", id="INC-7702", kind="incident")
    SEG_FRAUD = "network_segment:egress-fraud-score"
    SEG_GEO = "network_segment:egress-geoip"
    SEG_PAY = "network_segment:egress-payment-gw"
    EXT = "external_service:fraud-score-vendor"
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-7702",
                         "title": "fraud-scoring egress calls failing",
                         "short_description": "fraud-scoring egress failing ~7m after CHG-3311 ACL change",
                         "description": (
                             "ExternalDependencyErrorRateHigh fired for checkout-api at 09:12 UTC. "
                             "Outbound calls to the third-party fraud-scoring vendor "
                             "(fraud-score.vendor.com:443) are failing ~98%, and the FraudScoreCheck "
                             "business transaction is RED (p95 9.8s). Errors are scoped to that one "
                             "egress path — geoip + payment-gw egress are clean. CHG-3311 tightened "
                             "the prod-vpc egress ACL at 09:05. This recurs INC-7699 (same rule)."),
                         "work_notes": (
                             "[09:13] sre-oncall: ExternalDependencyErrorRateHigh; fraud-scoring "
                             "egress failing. Recurrence of INC-7699 (same FW rule reverted last time).\n"
                             "[09:26] sre-oncall: Splunk shows 214 CLEAN policy denies on FW-EGR-118 "
                             "(action=blocked), packet_loss 0% — a deny, not a link flap. Vendor "
                             "endpoint is UP. Requesting revert of CHG-3311."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Checkout",
                         "business_service": "Checkout", "impact": "2 - Medium",
                         "urgency": "1 - High", "category": "Security / Firewall",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-api",
                                     "app_id": "APM-CHECKO",
                                     "sys_id": "sn_checkoutapi01",
                                     "repo": "checkout-api",
                                     "k8s_workload": "prod/checkout-api",
                                     "owner": "checkout-platform@corp.example",
                                     "support_group": "SRE - Checkout",
                                     "version": "v9.2.0", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"
                                    }, "env": "prod"},
            "changes": [{"number": "CHG-3311", "type": "network",
                         "short_description": "Tighten egress ACL on prod-vpc (FW-EGR-118)",
                         "description": (
                             "Automated NetOps change tightening the prod-vpc egress ACL as part of "
                             "the quarterly least-privilege sweep. Narrows FW-EGR-118's allowed "
                             "destinations; the fraud-score vendor CIDR was dropped from the allow "
                             "list in error. Pushed via Terraform, no canary."),
                         "cmdb_ci": {"display_value": "checkout-api",
                                     "app_id": "APM-CHECKO",
                                     "sys_id": "sn_checkoutapi01",
                                     "repo": "checkout-api",
                                     "k8s_workload": "prod/checkout-api",
                                     "owner": "netops@corp.example"
                                    },
                         "requested_by": "netops-automation", "start_date": t_chg, "env": "prod",
                         "assignment_group": "Network Security",
                         "risk": "High", "impact": "2 - Medium", "state": "Implemented",
                         "close_code": "unsuccessful",
                         "implementation_plan": "terraform apply -target=aws_network_acl.prod_vpc_egress",
                         "backout_plan": "terraform apply with the prior FW-EGR-118 rule set (revert)"}],
            # a true RECURRENCE: same checkout-api + same egress vendor path went down before
            # (INC-7699), reverted then too — a strong hypothesis prior sharpening the FW rule.
            "primary_incident": "INC-7702",
            "related_incidents": [
                {"number": "INC-7699", "priority": "2 - High", "opened_at": t_prior,
                 "title": "fraud-scoring egress blocked by ACL (prior)",
                 "short_description": "Same FW-EGR-118 egress deny to the fraud vendor; reverted",
                 "cmdb_ci": "checkout-api", "relation": "recurrence", "confidence": "high"}]}},
        # declared topology: checkout-api CONNECTS_TO its three egress segments (the sibling
        # segments make the "scoped to ONE target" discriminator legible + give the probe facts
        # known subjects). egress-fraud-score's cidr == the FW rule's src (10.20.0.0/24).
        "cmdb": {"*": {
            "env": "prod",
            "ci_attrs": {"egress-fraud-score": {"cidr": "10.20.0.0/24", "vlan": 200},
                         "egress-geoip": {"cidr": "10.20.1.0/24", "vlan": 201},
                         "egress-payment-gw": {"cidr": "10.20.2.0/24", "vlan": 202}},
            "dependencies": [
                {"parent": "checkout-api", "parent_type": "cmdb_ci_service",
                 "child": "egress-fraud-score", "child_type": "cmdb_ci_network_segment",
                 "rel_type": "Connects to::Connected by"},
                {"parent": "checkout-api", "parent_type": "cmdb_ci_service",
                 "child": "egress-geoip", "child_type": "cmdb_ci_network_segment",
                 "rel_type": "Connects to::Connected by"},
                {"parent": "checkout-api", "parent_type": "cmdb_ci_service",
                 "child": "egress-payment-gw", "child_type": "cmdb_ci_network_segment",
                 "rel_type": "Connects to::Connected by"}]}},
        "prometheus": {
            "*": {"service": {"name": "checkout-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "ExternalDependencyErrorRateHigh",
                              "at": t_on, "state": "firing", "severity": "critical", "for": "5m",
                              "runbook_url": "https://runbooks.corp.example/ExternalDependencyErrorRateHigh",
                              "labels": {"service": "checkout-api", "env": "prod", "team": "checkout",
                                         "dependency": "fraud-score-vendor", "namespace": "checkout-prod"},
                              "expr": "sum(rate(external_call_errors_total{service=\"checkout-api\","
                                      "target=\"fraud-score-vendor\"}[5m])) / sum(rate("
                                      "external_call_total{target=\"fraud-score-vendor\"}[5m])) > 0.1"}],
                  # scoped onset: ~18% of calls (the fraud-scoring dependency) error out.
                  "metrics": [{"predicate": "degraded", "value": True, "at": t_on,
                               "reliability": 0.97},
                              {"predicate": "red_errors", "value": 0.18, "at": t_on,
                               "reliability": 0.97},
                              {"predicate": "red_rate", "value": 640, "unit": "rpm", "at": t_on,
                               "reliability": 0.97}]},
            # the discriminating deep-dive. probe fails to egress-fraud-score ONLY, and it's a
            # CLEAN failure (packet_loss 0.0 / retrans 0) -> policy deny, not a link flap. The
            # vendor endpoint itself is UP (availability 1.0) with our call_rate collapsed and
            # error_rate ~1 -> a block on OUR side, not a vendor outage.
            "investigate": {"service": {"name": "checkout-api", "env": "prod"},
                            "metrics": [
                                {"subject": SEG_FRAUD, "predicate": "probe_success", "value": 0,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.99},
                                {"subject": SEG_FRAUD, "predicate": "packet_loss", "value": 0.0,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.97},
                                {"subject": SEG_FRAUD, "predicate": "retrans_segs", "value": 0,
                                 "unit": "count", "at": t_inv, "reliability": 0.97},
                                {"subject": SEG_GEO, "predicate": "probe_success", "value": 1,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.99},
                                {"subject": SEG_GEO, "predicate": "packet_loss", "value": 0.0,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.97},
                                {"subject": SEG_PAY, "predicate": "probe_success", "value": 1,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.99},
                                {"subject": SEG_PAY, "predicate": "packet_loss", "value": 0.0,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.97},
                                {"subject": EXT, "predicate": "availability", "value": 1.0,
                                 "at": t_inv, "reliability": 0.95},
                                {"subject": EXT, "predicate": "error_rate", "value": 0.98,
                                 "at": t_inv, "reliability": 0.95},
                                {"subject": EXT, "predicate": "call_rate", "value": 3,
                                 "unit": "rpm", "at": t_inv, "reliability": 0.95},
                                {"subject": EXT, "predicate": "latency_p99", "value": 10000,
                                 "unit": "ms", "at": t_inv, "reliability": 0.9}]},
            # recovery after the ACL revert: probe + calls back, service healthy.
            "verify": {"service": {"name": "checkout-api", "env": "prod"},
                       "metrics": [
                           {"predicate": "red_errors", "value": 0.01, "at": t_fix,
                            "reliability": 0.98},
                           {"predicate": "degraded", "value": False, "at": t_fix,
                            "reliability": 0.98},
                           {"subject": SEG_FRAUD, "predicate": "probe_success", "value": 1,
                            "unit": "ratio", "at": t_fix, "reliability": 0.98},
                           {"subject": EXT, "predicate": "error_rate", "value": 0.0, "at": t_fix,
                            "reliability": 0.98},
                           {"subject": EXT, "predicate": "call_rate", "value": 615, "unit": "rpm",
                            "at": t_fix, "reliability": 0.98}]}},
        # AppD: flowmap discovers the fraud-score-vendor exit-call (DEPENDS_ON, discovered) and
        # the BusinessTransaction that calls it is RED (p95 blown, throughput collapsed).
        "appd": {
            "*": {"service": {"name": "checkout-api", "env": "prod"},
                  "bt": {"name": "FraudScoreCheck"},
                  "bt_metrics": [
                      {"predicate": "art_p95", "value": 9800, "unit": "ms", "at": t_on,
                       "reliability": 0.95},
                      {"predicate": "delta_vs_baseline", "value": 41.0, "at": t_on,
                       "reliability": 0.9},
                      {"predicate": "epm", "value": 3, "unit": "calls_per_min", "at": t_on,
                       "reliability": 0.92}],
                  "flowmap": [{"exit_calls": [
                      {"type": "HTTP", "target_external": "fraud-score-vendor",
                       "vendor": "FraudScoreCo"}]}]},
            "verify": {"service": {"name": "checkout-api", "env": "prod"},
                       "bt": {"name": "FraudScoreCheck"},
                       "bt_metrics": [
                           {"predicate": "art_p95", "value": 240, "unit": "ms", "at": t_fix,
                            "reliability": 0.95},
                           {"predicate": "delta_vs_baseline", "value": 1.0, "at": t_fix,
                            "reliability": 0.9}]}},
        # Splunk: the smoking gun — 214 CLEAN policy denies (action=blocked) on FW-EGR-118, one
        # egress target only. This creates the golden-root FIREWALL_RULE node + deny_count fact.
        "splunk": {
            "*": {"fw_denies": [{
                "rule_id": "FW-EGR-118", "action": "blocked", "direction": "egress",
                "proto": "tcp", "port_range": "443", "src": "10.20.0.0/24",
                "dst": "fraud-score.vendor.com/32", "_time": t_inv, "deny_count": 214,
                "reliability": 0.97, "rule_name": "prod-vpc-egress-allowlist",
                "firewall": "pafw-prod-01", "zone": "prod-vpc->internet", "policy": "egress-default",
                "index": "prod_firewall", "sourcetype": "pan:traffic",
                "sample_log": ("2026-07-19T09:25:04Z pafw-prod-01 DENY egress tcp "
                               "10.20.0.31:51442 -> 34.120.88.4:443 rule=FW-EGR-118 "
                               "app=fraud-score-vendor action=blocked")}]},
            "verify": {"fw_denies": []}},
    }
    # both are the SAME root cause (the ACL tightening): the CHANGE you revert, or the RULE it
    # tightened — accept either (a genuine domain equivalence, not a test crutch).
    return subject, fx, ("change_event:chg-3311", "firewall_rule:fw-egr-118")


def nochange() -> tuple[SubjectRef, dict, str]:
    """checkout-api p99 spikes to 6.8s under an organic 3.4x traffic surge that saturates
    checkout-db's connection pool; ServiceNow's change log for the window is EMPTY. Golden
    root = the DATABASE (pool saturation) — it LEADS on the USE-saturation trend, while the
    rival 'an unlogged deploy/config change caused this' is REFUTED by the empty change log
    (nothing to point at). No change exists to revert, so the pool trend only ever correlates
    with the onset (never causally isolated): the leading hypothesis stays 'supported', is
    never promoted to CONFIRMED, and the incident closes MITIGATED after the pool is scaled."""
    t_on, t_inv, t_fix = _dt(9, 0), _dt(9, 20), _dt(9, 45)
    subject = SubjectRef(domain="app-incident", id="INC-9100", kind="incident")
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-9100",
                         "title": "checkout-api latency under traffic surge",
                         "short_description": "checkout-api p99 hit 6.8s under a 3.4x surge; no change logged",
                         "description": (
                             "HighConnPoolUtilization fired for checkout-db at 09:00 UTC. "
                             "checkout-api p99 climbed to 6.8s as request volume surged to 3.4x "
                             "baseline (a marketing push); the whole latency distribution shifted up "
                             "(p50 780ms) rather than a concentrated error spike. checkout-db's "
                             "connection pool is pinned at 194/200. The change log for the window is "
                             "EMPTY — no deploy or config event to blame; this reads as organic "
                             "saturation, not a regression."),
                         "work_notes": (
                             "[09:02] sre-oncall: HighConnPoolUtilization; p99 6.8s under a 3.4x "
                             "traffic surge. No change in the window — organic load suspected.\n"
                             "[09:22] sre-oncall: pool 97%, 194/200 conns, slow-query surge to 88/min. "
                             "No revert available; scaling the pool + adding a read replica to mitigate."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Checkout",
                         "business_service": "Checkout", "impact": "2 - Medium",
                         "urgency": "2 - Medium", "category": "Capacity / Saturation",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-api",
                                     "app_id": "APM-CHECKO",
                                     "sys_id": "sn_checkoutapi01",
                                     "repo": "checkout-api",
                                     "k8s_workload": "prod/checkout-api",
                                     "owner": "checkout-platform@corp.example",
                                     "support_group": "SRE - Checkout",
                                     "version": "v9.2.0", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"
                                    }, "env": "prod"},
            # the no-change class: an EMPTY change list is first-class (not an error) — it is
            # the REFUTING evidence for the 'invisible change' rival (h2): no deploy/config
            # event exists anywhere in the incident window to blame.
            "changes": []}},
        "cmdb": {"*": {"env": "prod",
                       "ci_attrs": {"checkout-db": {
                           "engine": "postgresql", "version": "15.4", "ha_role": "primary",
                           "endpoint": "checkout-db.prod.rds.internal:5432",
                           "instance_class": "db.r6g.2xlarge", "max_connections": 200,
                           "owner": "checkout-platform@corp.example",
                           "managed_by": "dba-team"}},
                       "dependencies": [
                           {"parent": "checkout-api", "parent_type": "cmdb_ci_service",
                            "child": "checkout-db", "child_type": "cmdb_ci_database",
                            "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            # frame/triage: the surge + saturation SHAPE (whole latency distribution shifts up,
            # errors bleed) and the backend pool already climbing (86% and rising) in lockstep
            # with the onset — a USE trend with no change behind it.
            "*": {"service": {"name": "checkout-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "HighConnPoolUtilization",
                              "at": t_on, "state": "firing", "severity": "warning", "for": "5m",
                              "runbook_url": "https://runbooks.corp.example/HighConnPoolUtilization",
                              "labels": {"service": "checkout-api", "env": "prod", "team": "checkout",
                                         "database": "checkout-db", "namespace": "checkout-prod"},
                              "expr": "pg_pool_connections_in_use{db=\"checkout-db\"} / "
                                      "pg_pool_connections_max{db=\"checkout-db\"} > 0.85"}],
                  "metrics": [
                      {"predicate": "red_latency_p99", "value": 6800, "unit": "ms",
                       "at": t_on, "reliability": 0.96},
                      {"predicate": "red_rate", "value": 3.4, "unit": "x_baseline",
                       "at": t_on, "reliability": 0.97},
                      {"predicate": "red_errors", "value": 0.08, "at": t_on,
                       "reliability": 0.96},
                      {"predicate": "degraded", "value": True, "at": t_on,
                       "reliability": 0.95},
                      {"subject": "database:checkout-db", "predicate": "conn_pool_util",
                       "value": 0.86, "at": t_on, "reliability": 0.98}]},
            # investigate: the pool internals that make the saturation concrete — connections
            # pinned at the ceiling (194/200), a slow-query surge as everything contends, and
            # the request rate still climbing. conn_pool_util 0.86 -> 0.97 tracks the onset 1:1.
            "investigate": {"service": {"name": "checkout-api", "env": "prod"},
                            "metrics": [
                                {"subject": "database:checkout-db",
                                 "predicate": "conn_pool_util", "value": 0.97,
                                 "at": t_inv, "reliability": 0.99},
                                {"subject": "database:checkout-db",
                                 "predicate": "active_connections", "value": 194,
                                 "unit": "conn", "at": t_inv, "reliability": 0.99},
                                {"subject": "database:checkout-db",
                                 "predicate": "max_connections", "value": 200,
                                 "unit": "conn", "at": t_inv, "reliability": 0.99},
                                {"subject": "database:checkout-db",
                                 "predicate": "slow_query_rate", "value": 88,
                                 "unit": "per_min", "at": t_inv, "reliability": 0.98},
                                {"predicate": "red_rate", "value": 4.1,
                                 "unit": "x_baseline", "at": t_inv, "reliability": 0.97}]},
            # verify: post-scale-up recovery — pool util falls back to 52%, symptom clears.
            "verify": {"service": {"name": "checkout-api", "env": "prod"},
                       "metrics": [
                           {"subject": "database:checkout-db", "predicate": "conn_pool_util",
                            "value": 0.52, "at": t_fix, "reliability": 0.98},
                           {"predicate": "degraded", "value": False, "at": t_fix,
                            "reliability": 0.98}]}},
        "appd": {
            # snapshot exit-calls discover checkout-api's ONLY backend dependency (checkout-db,
            # JDBC) at the boundary where the pool times out; p50 climbs too (780ms >> 500ms
            # SLO) — a whole-distribution saturation shape, distinct from a code fault's clean
            # p50 + concentrated error spike.
            "*": {"service": {"name": "checkout-api", "env": "prod"},
                  "bt_metrics": [{"predicate": "red_latency_p50", "value": 780, "unit": "ms",
                                  "at": t_on, "reliability": 0.95}],
                  "snapshots": [{"exit_calls": [{"type": "JDBC", "db_id": "checkout-db",
                                                 "engine": "postgres"}]}]},
            "investigate": {"service": {"name": "checkout-api", "env": "prod"},
                            "bt_metrics": [{"predicate": "red_latency_p50", "value": 820,
                                            "unit": "ms", "at": t_inv, "reliability": 0.95}],
                            "snapshots": [{"exit_calls": [
                                {"type": "JDBC", "db_id": "checkout-db",
                                 "engine": "postgres"}]}]}},
    }
    return subject, fx, "database:checkout-db"


def messaging() -> tuple[SubjectRef, dict, str]:
    """order-processor consumer group falls behind after CHG-55 (a consumer deploy) shipped a
    slower per-message handler: consumer_lag on the orders.events topic climbs while the DLQ
    stays empty and producer throughput holds steady — the backlog is purely consumer-side, so a
    broker rebalance / producer surge is ruled out. Golden root = the CHANGE_EVENT (CHG-55)."""
    t_chg, t_on, t_inv, t_fix = _dt(9, 30), _dt(9, 42), _dt(9, 56), _dt(10, 20)
    subject = SubjectRef(domain="app-incident", id="INC-8801", kind="incident")
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-8801",
                         "title": "order-processor consumer lag climbing",
                         "short_description": "order-processor lag on orders.events up after CHG-55",
                         "description": (
                             "HighConsumerLag fired for the order-processor consumer group on the "
                             "orders.events Kafka topic at 09:42 UTC. Lag is climbing steadily "
                             "(42k -> 61k messages) while the producer ingress rate holds flat at "
                             "~1,470 msg/min and the DLQ stays empty — so the backlog is purely "
                             "consumer-side throughput, not a producer surge or a poison message. "
                             "Consumer deploy CHG-55 shipped at 09:30, ~12m before onset."),
                         "work_notes": (
                             "[09:43] sre-oncall: HighConsumerLag on orders.events; DLQ empty, "
                             "producer steady. Deploy CHG-55 (09:30) is the change-first suspect.\n"
                             "[09:57] sre-oncall: diff shows CHG-55 replaced a batched pricing "
                             "lookup with a per-message blocking RPC — handler ~2x slower. Rolling "
                             "the consumer deploy back."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Order Fulfillment",
                         "business_service": "Order Fulfillment", "impact": "2 - Medium",
                         "urgency": "2 - Medium", "category": "Messaging",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "order-processor",
                                     "app_id": "APM-ORDERP",
                                     "sys_id": "sn_orderprocessor01",
                                     "repo": "order-processor",
                                     "k8s_workload": "prod/order-processor",
                                     "owner": "fulfillment-platform@corp.example",
                                     "support_group": "SRE - Order Fulfillment",
                                     "version": "v3.2.0", "environment": "production",
                                     "business_criticality": "2 - Significant"
                                    }, "env": "prod"},
            "changes": [{"number": "CHG-55", "type": "deployment",
                         "short_description": "Deploy order-processor v3.2.0 (per-item price enrichment)",
                         "description": (
                             "Deploy of order-processor v3.2.0. Replaces the batched pricing lookup "
                             "in the message handler with a per-item synchronous enrichment call for "
                             "'fresher' prices — a blocking RPC per message that roughly doubled "
                             "handler latency, so the consumer group can no longer keep up."),
                         "cmdb_ci": {"display_value": "order-processor",
                                     "app_id": "APM-ORDERP",
                                     "sys_id": "sn_orderprocessor01",
                                     "repo": "order-processor",
                                     "k8s_workload": "prod/order-processor",
                                     "owner": "fulfillment-platform@corp.example",
                                     "version": "v3.2.0"
                                    },
                         "requested_by": "dev-mq", "start_date": t_chg,
                         "assignment_group": "Fulfillment Engineering",
                         "risk": "Moderate", "impact": "2 - Medium", "state": "Implemented",
                         "close_code": "unsuccessful",
                         "implementation_plan": "kubectl -n orders-prod set image deploy/order-processor ...:v3.2.0",
                         "backout_plan": "kubectl -n orders-prod rollout undo deploy/order-processor"}]}},
        "cmdb": {"*": {"env": "prod",
                       "ci_attrs": {"orders.events": {
                           "broker": "kafka-prod-1", "partitions": 12, "replication_factor": 3,
                           "cluster": "kafka-prod", "retention_ms": 604800000,
                           "consumer_group": "order-processor", "owner": "fulfillment-platform@corp.example"}},
                       "dependencies": [
                           {"parent": "order-processor", "parent_type": "cmdb_ci_service",
                            "child": "orders.events", "child_type": "cmdb_ci_message_queue",
                            "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "order-processor", "env": "prod"},
                  "alerts": [{"id": "ALT-7", "alertname": "HighConsumerLag", "at": t_on,
                              "state": "firing", "severity": "warning", "for": "5m",
                              "runbook_url": "https://runbooks.corp.example/HighConsumerLag",
                              "labels": {"service": "order-processor", "env": "prod",
                                         "team": "fulfillment", "topic": "orders.events",
                                         "consumer_group": "order-processor"},
                              "expr": "sum(kafka_consumergroup_lag{group=\"order-processor\","
                                      "topic=\"orders.events\"}) > 20000"}],
                  "metrics": [
                      {"predicate": "degraded", "value": True, "at": t_on, "reliability": 0.96},
                      {"predicate": "red_rate", "value": 320, "unit": "rpm", "at": t_on,
                       "reliability": 0.96},
                      {"subject": "message_queue:orders.events", "predicate": "consumer_lag",
                       "value": 42000, "unit": "msgs", "at": t_on, "reliability": 0.97},
                      {"subject": "message_queue:orders.events", "predicate": "throughput",
                       "value": 1470, "unit": "msgs_per_min", "at": t_on, "reliability": 0.96}]},
            "investigate": {"service": {"name": "order-processor", "env": "prod"},
                            "metrics": [
                                # lag climbing while ingress throughput is flat and the DLQ is
                                # empty -> the deficit is purely consumer-side processing rate.
                                {"subject": "message_queue:orders.events", "predicate": "consumer_lag",
                                 "value": 61000, "unit": "msgs", "at": t_inv, "reliability": 0.97},
                                {"subject": "message_queue:orders.events", "predicate": "dlq_depth",
                                 "value": 0, "unit": "msgs", "at": t_inv, "reliability": 0.97},
                                {"subject": "message_queue:orders.events", "predicate": "throughput",
                                 "value": 1450, "unit": "msgs_per_min", "at": t_inv,
                                 "reliability": 0.96},
                                {"predicate": "red_latency_p99", "value": 210, "unit": "ms",
                                 "at": t_inv, "reliability": 0.94}]},
            "verify": {"service": {"name": "order-processor", "env": "prod"},
                       "metrics": [
                           {"subject": "message_queue:orders.events", "predicate": "consumer_lag",
                            "value": 300, "unit": "msgs", "at": t_fix, "reliability": 0.97},
                           {"subject": "message_queue:orders.events", "predicate": "throughput",
                            "value": 1460, "unit": "msgs_per_min", "at": t_fix, "reliability": 0.96},
                           {"predicate": "degraded", "value": False, "at": t_fix,
                            "reliability": 0.97}]}},
        "git": {"*": {
            "change": {"change_id": "CHG-55", "change_type": "deployment"},
            # the consumer deploy is the actionable root — the diff attaches to the CHANGE, and
            # the offending hunk is a batched lookup turned into a per-message blocking RPC.
            "diff": {"at": t_chg, "files_changed": 1, "lines_added": 22, "lines_deleted": 4,
                     "reliability": 0.99, "author": "dev-mq", "repo": "order-processor",
                     "path": "order_processor/handler.py", "pr_id": "310",
                     "changed_lines": [
                         "- prices = pricing_client.batch_get([i.sku for i in event.items])",
                         "+ for item in event.items:  # CHG-55: per-item synchronous enrichment",
                         "+     price = pricing_client.get(item.sku)  # blocking RPC per message "
                         "-> handler ~2x slower, consumer group falls behind"]}}},
    }
    return subject, fx, "change_event:chg-55"


def infra() -> tuple[SubjectRef, dict, str]:
    """checkout-svc's tier-1 pod evicted after an unbounded etl-nightly batch job (co-scheduled
    on node-prod-17) starved the node's memory -> the kubelet evicted the lowest-priority
    tenant. Golden root = the BATCH_JOB. Discriminator: the HOST's mem_utilization is pegged at
    0.98 while the victim POD's OWN mem is only 0.55 — a platform (co-tenancy) fault, NOT a
    checkout-svc application memory leak. cmdb declares the batch_job RUNS_ON node-prod-17 (the
    co-tenant), ocp surfaces the eviction + pod's own moderate memory, prometheus carries the
    host-vs-pod split and the batch job's overrun; recovery lands in verify."""
    t_on, t_inv, t_fix = _dt(2, 18), _dt(2, 30), _dt(2, 55)
    subject = SubjectRef(domain="app-incident", id="INC-8900", kind="incident")
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-8900",
                         "title": "checkout-svc pod evicted (memory pressure)",
                         "short_description": "checkout-svc pod evicted after node-prod-17 ran low on memory",
                         "description": (
                             "PodEvicted fired for checkout-svc at 02:18 UTC. A tier-1 checkout-svc "
                             "pod on node-prod-17 was evicted by the kubelet under node memory "
                             "pressure (threshold 100Mi, available 84Mi). The host's memory is "
                             "pegged at 98% while the evicted pod's OWN working set is only 55% — "
                             "so this is a node co-tenancy problem, not a checkout-svc leak. The "
                             "etl-nightly batch job (co-scheduled on the same node) overran its "
                             "window ~4x and is holding a huge working set."),
                         "work_notes": (
                             "[02:19] sre-oncall: PodEvicted on checkout-svc; node-prod-17 low on "
                             "memory. Host mem pegged, pod mem moderate — noisy-neighbor suspected.\n"
                             "[02:31] sre-oncall: etl-nightly (co-tenant on node-prod-17) ran 4200s "
                             "(4x its window), backlog 8.4M rows. Rescheduling it off the tier-1 node "
                             "+ setting a memory limit."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Platform",
                         "business_service": "Checkout", "impact": "2 - Medium",
                         "urgency": "2 - Medium", "category": "Infrastructure / Kubernetes",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-svc",
                                     "app_id": "APM-CHECKO",
                                     "sys_id": "sn_checkoutsvc01",
                                     "repo": "checkout-svc",
                                     "k8s_workload": "prod/checkout-svc",
                                     "owner": "checkout-platform@corp.example",
                                     "support_group": "SRE - Checkout",
                                     "version": "v9.2.0", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"
                                    }, "env": "prod"},
            # no-change class: a resource/co-tenancy fault, NOT a deploy — rules out CHG cause
            "changes": []}},
        "cmdb": {"*": {
            "env": "prod",
            "ci_attrs": {
                "etl-nightly": {"schedule_id": "sched-3", "schedule": "0 2 * * *",
                                "owner": "data-eng@corp.example", "managed_by": "data-engineering",
                                "runtime": "spark-3.5", "namespace": "batch",
                                "priority_class": "best-effort"},
                "node-prod-17": {"asset_id": "AST-4417", "cpu_cores": 16, "mem_gb": 64,
                                 "region": "us-east-1", "availability_zone": "us-east-1b",
                                 "instance_type": "m6i.4xlarge", "kernel": "5.15.0-1053-aws",
                                 "kubelet_version": "v1.28.6", "rack": "r14"}},
            # the batch job is co-scheduled on the SAME node as the evicted tier-1 pod
            "dependencies": [
                {"parent": "etl-nightly", "parent_type": "cmdb_ci_batch_job",
                 "child": "node-prod-17", "child_type": "cmdb_ci_server",
                 "rel_type": "Runs on::Hosts"}]}},
        "ocp": {
            "*": {
                "pods": [{"uid": "checkout-7c9f-abc12", "name": "checkout-7c9f-abc12",
                          "namespace": "checkout", "phase": "Failed", "ready": False,
                          "node_name": "node-prod-17", "mem_utilization": 0.55, "at": t_on}],
                "events": [{"involved_object": {"kind": "Pod", "uid": "checkout-7c9f-abc12",
                                                "name": "checkout-7c9f-abc12",
                                                "namespace": "checkout"},
                            "reason": "Evicted", "at": t_on,
                            "message": "The node was low on resource: memory. "
                                       "Threshold quantity: 100Mi, available: 84Mi."}]},
            "verify": {
                "pods": [{"uid": "checkout-7c9f-abc12", "name": "checkout-7c9f-abc12",
                          "namespace": "checkout", "phase": "Running", "ready": True,
                          "node_name": "node-prod-17", "mem_utilization": 0.52, "at": t_fix}],
                "events": [{"involved_object": {"kind": "Pod", "uid": "checkout-7c9f-abc12",
                                                "name": "checkout-7c9f-abc12",
                                                "namespace": "checkout"},
                            "reason": "Started", "at": t_fix,
                            "message": "Started container checkout"}]}},
        "prometheus": {
            "*": {"service": {"name": "checkout-svc", "env": "prod"},
                  "alerts": [{"id": "ALT-9", "alertname": "PodEvicted", "at": t_on,
                              "state": "firing", "severity": "critical", "for": "0m",
                              "runbook_url": "https://runbooks.corp.example/PodEvicted",
                              "labels": {"service": "checkout-svc", "env": "prod", "team": "checkout",
                                         "node": "node-prod-17", "namespace": "checkout",
                                         "reason": "MemoryPressure"},
                              "expr": "kube_pod_status_reason{reason=\"Evicted\","
                                      "namespace=\"checkout\"} == 1"}],
                  "metrics": [{"predicate": "degraded", "value": True, "at": t_on,
                               "reliability": 0.96},
                              {"predicate": "tier", "value": "tier-1", "at": t_on,
                               "reliability": 0.99},
                              {"predicate": "red_errors", "value": 0.09, "unit": "ratio",
                               "at": t_on, "reliability": 0.96}]},
            "investigate": {"service": {"name": "checkout-svc", "env": "prod"},
                            "metrics": [
                                # HOST memory is pegged -> node-level pressure (USE metric)
                                {"subject": "host:node-prod-17", "predicate": "mem_utilization",
                                 "value": 0.98, "unit": "ratio", "at": t_inv, "reliability": 0.97},
                                # host CPU is fine -> starvation is memory-specific, not overload
                                {"subject": "host:node-prod-17", "predicate": "cpu_utilization",
                                 "value": 0.41, "unit": "ratio", "at": t_inv, "reliability": 0.97},
                                # the victim POD's OWN memory is only moderate -> NOT an app leak
                                {"subject": "pod:checkout-7c9f-abc12", "predicate": "mem_utilization",
                                 "value": 0.55, "unit": "ratio", "at": t_inv, "reliability": 0.96},
                                # the batch job overran ~4x its window, holding a huge working set
                                {"subject": "batch_job:etl-nightly|sched-3",
                                 "predicate": "last_duration", "value": 4200, "unit": "s",
                                 "at": t_inv, "reliability": 0.95},
                                {"subject": "batch_job:etl-nightly|sched-3",
                                 "predicate": "backlog_size", "value": 8400000, "unit": "rows",
                                 "at": t_inv, "reliability": 0.95}]},
            "verify": {"service": {"name": "checkout-svc", "env": "prod"},
                       "metrics": [
                           {"subject": "host:node-prod-17", "predicate": "mem_utilization",
                            "value": 0.61, "unit": "ratio", "at": t_fix, "reliability": 0.97},
                           {"predicate": "degraded", "value": False, "at": t_fix,
                            "reliability": 0.97},
                           {"predicate": "red_errors", "value": 0.002, "unit": "ratio",
                            "at": t_fix, "reliability": 0.97},
                           {"subject": "batch_job:etl-nightly|sched-3", "predicate": "last_duration",
                            "value": 1080, "unit": "s", "at": t_fix, "reliability": 0.95}]}},
    }
    return subject, fx, "batch_job:etl-nightly|sched-3"


def cache() -> tuple[SubjectRef, dict, str]:
    """product-api latency after a cache-client deploy (INC-5500). Golden root = the CODE_COMMIT
    that disabled singleflight (9f8e7d6). The cache tier's collapse (hit-rate, evictions, memory)
    surfaces as prometheus metrics on the cache node; appd shows the service's p50 flat (rules
    out a code regression); git carries the disabling diff."""
    t_chg, t_on, t_inv, t_fix = _dt(14, 10), _dt(14, 16), _dt(14, 31), _dt(14, 56)
    subject = SubjectRef(domain="app-incident", id="INC-5500", kind="incident")
    cache = "cache:product-redis"
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-5500",
                         "title": "product-api latency after cache deploy",
                         "short_description": "product-api p99 up after cache deploy; hit-rate collapsed",
                         "description": (
                             "HighLatencyP99 fired for product-api (prod, tier-1) at 14:16 UTC, "
                             "~6m after the cache-client deploy CHG-22 (commit 9f8e7d6). The "
                             "product-redis hit-rate collapsed from ~96% to 41%, eviction rate "
                             "surged to 420/min and cache memory is at 94% — a cache-stampede shape. "
                             "The service's own p50 stays flat (67ms), so the app compute path is "
                             "fine; the latency is all cache-miss backend load."),
                         "work_notes": (
                             "[14:18] sre-oncall: HighLatencyP99 on product-api; redis hit-rate 41%, "
                             "evictions surging. Cache-client deploy CHG-22 at 14:10 is the suspect.\n"
                             "[14:32] sre-oncall: blame shows CHG-22 disabled singleflight — every "
                             "request now issues its own cache read (stampede). p50 flat rules out a "
                             "code regression. Rolling back to v3.3.2."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Product Catalog",
                         "business_service": "Product Catalog", "impact": "2 - Medium",
                         "urgency": "2 - Medium", "category": "Caching",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "product-api", "app_id": "APM-PRODUCT",
                                     "sys_id": "sn_productapi01", "repo": "product-api",
                                     "k8s_workload": "prod/product-api",
                                     "owner": "catalog-platform@corp.example",
                                     "support_group": "SRE - Product Catalog",
                                     "version": "v3.4.0", "environment": "production",
                                     "business_criticality": "2 - Significant"}, "env": "prod"},
            "changes": [{"number": "CHG-22", "type": "deployment",
                         "short_description": "Deploy product-api v3.4.0 (cache client refactor)",
                         "description": (
                             "Deploy of product-api v3.4.0. Refactors pkg/cache/client.go and "
                             "removes the singleflight de-duplication around cache reads, believing "
                             "it redundant. Without it, concurrent misses on a hot key each issue "
                             "their own read — a classic cache stampede under load."),
                         "cmdb_ci": {"display_value": "product-api", "app_id": "APM-PRODUCT",
                                     "sys_id": "sn_productapi01", "repo": "product-api",
                                     "k8s_workload": "prod/product-api",
                                     "owner": "catalog-platform@corp.example",
                                     "version": "v3.4.0"},
                         "requested_by": "platform-team", "start_date": t_chg,
                         "assignment_group": "Platform Engineering",
                         "risk": "Moderate", "impact": "2 - Medium", "state": "Implemented",
                         "close_code": "unsuccessful",
                         "implementation_plan": "argocd app sync product-api --revision v3.4.0",
                         "backout_plan": "argocd app rollback product-api v3.3.2",
                         "u_commit_sha": "9f8e7d6"}]}},
        "cmdb": {"*": {"env": "prod", "dependencies": [
            {"parent": "product-api", "parent_type": "cmdb_ci_service",
             "child": "product-redis", "child_type": "cmdb_ci_appl",
             "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "product-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "HighLatencyP99", "at": t_on,
                              "state": "firing", "severity": "critical", "for": "5m",
                              "runbook_url": "https://runbooks.corp.example/HighLatencyP99",
                              "labels": {"service": "product-api", "env": "prod", "team": "catalog",
                                         "cache": "product-redis", "namespace": "product-prod"},
                              "expr": "histogram_quantile(0.99, sum(rate("
                                      "http_request_duration_seconds_bucket{service=\"product-api\"}"
                                      "[5m])) by (le)) > 1"}],
                  "metrics": [{"subject": cache, "predicate": "hit_rate", "value": 0.41,
                               "at": t_inv, "reliability": 0.97, "unit": "ratio"},
                              {"subject": cache, "predicate": "eviction_rate", "value": 420,
                               "at": t_inv, "reliability": 0.95, "unit": "per_min"},
                              {"subject": cache, "predicate": "mem_utilization", "value": 0.94,
                               "at": t_inv, "reliability": 0.97, "unit": "ratio"}]},
            "verify": {"service": {"name": "product-api", "env": "prod"},
                       "metrics": [{"subject": cache, "predicate": "hit_rate", "value": 0.96,
                                    "at": t_fix, "reliability": 0.98, "unit": "ratio"},
                                      {"predicate": "degraded", "value": False, "at": t_fix,
                                       "reliability": 0.98},
                                      {"predicate": "red_latency_p99", "value": 140, "at": t_fix,
                                       "reliability": 0.98, "unit": "ms"}]}},
        "appd": {"*": {
            "service": {"name": "product-api", "env": "prod"},
            # no "bt" key: red_latency_p50 must fold onto the SERVICE (the docstring's
            # "service's p50 flat" discriminator) — under a BT subject the dictionary
            # rejects latency_p50 as not-allowed-on-business_transaction (live retest
            # 2026-07-22; the database scenario's blob has the same shape).
            "bt_metrics": [{"predicate": "red_latency_p50", "value": 67, "unit": "ms",
                            "at": t_inv, "reliability": 0.95}],
            "snapshots": [{"exit_calls": [{"type": "REDIS", "cache_id": "product-redis"}]}]}},
        "git": {"*": {
            "commit": {"sha": "9f8e7d6", "repo": "product-api", "author": "platform-team",
                       "parent_sha": "a1b2c3d", "authored_at": t_chg, "branch": "main",
                       "message": ("refactor(cache): drop singleflight around cache reads\n\n"
                                   "Believed redundant with client-side batching. PR #921."),
                       "files_changed": 2, "tag": "v3.4.0"},
            "blame": {"sha": "9f8e7d6", "repo": "product-api",
                      "file": "pkg/cache/client.go", "line": 88, "at": t_inv,
                      "reliability": 0.98, "author": "platform-team",
                      "committed_at": t_chg.isoformat(),
                      "snippet": ("// v3.4.0: singleflight disabled — every request now issues "
                                  "its own cache read instead of sharing one in-flight")}}},
    }
    return subject, fx, "code_commit:9f8e7d6"


def featureflag() -> tuple[SubjectRef, dict, str]:
    """cart-api 5xx after a feature-flag flip (INC-5600). Golden root = the FEATURE_FLAG (the
    flag is edge-isolated, so the causal link is via the model's hypothesis root_candidate, not
    a typed edge). The flag flip is a ServiceNow change (CHG-77); the error signature is new
    (first_seen at the flip); git blame shows the gated branch that throws on bulk carts."""
    t_flg, t_on, t_inv, t_fix = _dt(14, 5), _dt(14, 5), _dt(14, 21), _dt(14, 43)
    subject = SubjectRef(domain="app-incident", id="INC-5600", kind="incident")
    flag = "feature_flag:new-tax-engine|prod"
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-5600",
                         "title": "cart-api 5xx after feature-flag flip",
                         "short_description": "cart-api 5xx began at the new-tax-engine flag flip to 100%",
                         "description": (
                             "High5xxRate fired for cart-api (prod, tier-1) at 14:05 UTC, coincident "
                             "with the new-tax-engine LaunchDarkly flag ramping to 100% rollout. 5xx "
                             "jumped to 34%; a brand-new TaxEngineException first appears exactly at "
                             "the flip. No code deploy in the window (last deploy was 3 days ago). "
                             "The errors concentrate on bulk carts (>5 items), where the newly-"
                             "enabled tax-engine branch raises."),
                         "work_notes": (
                             "[14:06] sre-oncall: High5xxRate on cart-api at the new-tax-engine flag "
                             "flip to 100%. New TaxEngineException signature — flag is prime suspect.\n"
                             "[14:22] sre-oncall: 312 TaxEngineException, first_seen == flip time; "
                             "blame points at a gated branch that raises on carts >5 items. Recycling "
                             "the flag to 0%."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Cart",
                         "business_service": "Cart & Checkout", "impact": "2 - Medium",
                         "urgency": "1 - High", "category": "Configuration / Feature Flag",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "cart-api", "app_id": "APM-CART",
                                     "sys_id": "sn_cartapi01", "repo": "cart-api",
                                     "k8s_workload": "prod/cart-api",
                                     "owner": "cart-platform@corp.example",
                                     "support_group": "SRE - Cart",
                                     "version": "v6.1.4", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"}, "env": "prod"},
            "changes": [{"number": "CHG-77", "type": "feature-flag",
                         "short_description": "Ramp new-tax-engine flag to 100% in prod",
                         "description": (
                             "Progressive-delivery change: ramp the new-tax-engine LaunchDarkly flag "
                             "from 25% to 100% in prod. Enables the rewritten tax engine for all "
                             "cart-api traffic. The gated code path raises TaxEngineException on "
                             "carts larger than 5 items — surfaced only at full rollout."),
                         "cmdb_ci": {"display_value": "cart-api", "app_id": "APM-CART",
                                     "sys_id": "sn_cartapi01", "repo": "cart-api",
                                     "owner": "cart-platform@corp.example"},
                         "requested_by": "tax-platform", "start_date": t_flg,
                         "assignment_group": "Tax Platform",
                         "risk": "Moderate", "impact": "2 - Medium", "state": "Implemented",
                         "close_code": "unsuccessful",
                         "implementation_plan": "ldcli flags update new-tax-engine --env prod --rollout 100",
                         "backout_plan": "ldcli flags update new-tax-engine --env prod --rollout 0",
                         "u_release_tag": "new-tax-engine@100%"}],
            "related_incidents": [
                {"number": "INC-4988", "priority": "3 - Moderate", "opened_at": _dt(13, 0),
                 "title": "pricing-api elevated errors on bulk quotes",
                 "short_description": "pricing-api errors on large carts — same new-tax-engine flag",
                 "cmdb_ci": "pricing-api", "confidence": "high"}]}},
        "cmdb": {"*": {"env": "prod", "dependencies": []}},
        "prometheus": {
            "*": {"service": {"name": "cart-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "High5xxRate", "at": t_on,
                              "state": "firing", "severity": "critical", "for": "2m",
                              "runbook_url": "https://runbooks.corp.example/High5xxRate",
                              "labels": {"service": "cart-api", "env": "prod", "team": "cart",
                                         "flag": "new-tax-engine", "namespace": "cart-prod"},
                              "expr": "sum(rate(http_requests_total{code=~\"5..\","
                                      "service=\"cart-api\"}[5m])) / sum(rate(http_requests_total{"
                                      "service=\"cart-api\"}[5m])) > 0.05"}],
                  "metrics": [{"predicate": "red_errors", "value": 0.34, "at": t_on,
                               "reliability": 0.97},
                              {"subject": flag, "predicate": "rollout_percentage", "value": 100,
                               "at": t_on, "reliability": 0.99},
                              {"subject": flag, "predicate": "enabled", "value": True,
                               "at": t_on, "reliability": 0.99}]},
            "verify": {"service": {"name": "cart-api", "env": "prod"},
                       "metrics": [{"subject": flag, "predicate": "rollout_percentage",
                                    "value": 0, "at": t_fix, "reliability": 0.99},
                                      {"predicate": "red_errors", "value": 0.003, "at": t_fix,
                                       "reliability": 0.98},
                                      {"predicate": "degraded", "value": False, "at": t_fix,
                                       "reliability": 0.98}]}},
        "splunk": {"*": {
            "service": {"name": "cart-api", "env": "prod"},
            "errors": [{"signature_hash": "taxengine-bulk-cart",
                        "exception_class": "TaxEngineException",
                        "file_line": "services/cart-api/src/tax/engine.py:142",
                        "first_seen": t_flg.isoformat(), "_time": t_inv,
                        "count": 312, "last_seen": t_inv.isoformat(), "trace_id": "tr-7c3",
                        "level": "ERROR", "host": "cart-api-6f8d-9wq2", "sourcetype": "python",
                        "logger": "cart_api.tax.engine", "index": "prod_cart",
                        "flag": "new-tax-engine",
                        "message": ("TaxEngineException: new tax engine cannot price a cart with "
                                    ">5 line items (got 8)"),
                        "stack": [
                            "Traceback (most recent call last):",
                            "  File \"services/cart-api/src/api/checkout.py\", line 88, in submit",
                            "    totals = tax.compute(cart)",
                            "  File \"services/cart-api/src/tax/engine.py\", line 142, in compute",
                            "    raise TaxEngineException(f\"unsupported cart size {cart.size}\")",
                            "cart_api.tax.engine.TaxEngineException: unsupported cart size 8"]}]}},
        "git": {"*": {
            "commit": {"sha": "c3d4e5f", "repo": "cart-api", "author": "tax-platform",
                       "authored_at": _dt(11, 0), "branch": "main",   # 3 days ago — pre-flag deploy
                       "message": ("feat(tax): add new-tax-engine behind a flag (default off)\n\n"
                                   "Dark-launch of the rewritten tax engine. PR #1203."),
                       "files_changed": 9, "tag": "v6.1.4"},
            "blame": {"sha": "c3d4e5f", "repo": "cart-api",
                      "file": "services/cart-api/src/tax/engine.py", "line": 142, "at": t_inv,
                      "reliability": 0.98, "author": "tax-platform",
                      "committed_at": _dt(11, 0).isoformat(),
                      "snippet": "if items.size > 5: raise TaxEngineException()  // behind new-tax-engine flag"},
            "error_signature_hash": "taxengine-bulk-cart"}},
    }
    return subject, fx, flag


def certificate() -> tuple[SubjectRef, dict, str]:
    """auth-svc intermittent 503 from an expiring intermediate cert (INC-5700). Golden root =
    the CERTIFICATE (edge-isolated; the causal link is via the model's hypothesis). The cert's
    expiry surfaces via artifactory; the handshake errors surface via splunk; prometheus shows
    the PARTIAL (~40%) failure that discriminates a cert fault from a total outage."""
    t_exp, t_on, t_inv, t_fix = _dt(14, 0), _dt(14, 30), _dt(14, 45), _dt(15, 10)
    subject = SubjectRef(domain="app-incident", id="INC-5700", kind="incident")
    cert = "certificate:auth-tls-intermediate"
    fx = {
        "servicenow": {"*": {
            "incident": {"number": "INC-5700",
                         "title": "auth-svc intermittent 503s",
                         "short_description": "auth-svc 503s from TLS handshake fails; cert expiring",
                         "description": (
                             "High5xxRate fired for auth-svc (prod, tier-1) at 14:30 UTC. ~40% of "
                             "requests return 503 with PKIX path-building failures on the TLS "
                             "handshake — a partial failure that points at an expiring/expired cert "
                             "rather than a total outage. The auth-tls-intermediate certificate "
                             "(Corp Intermediate CA) reached not_after at 14:00; handshake errors "
                             "began exactly then. No code or config change in the window."),
                         "work_notes": (
                             "[14:31] sre-oncall: High5xxRate on auth-svc; ~40% 503s with PKIX "
                             "path-building failures — partial, smells like a cert.\n"
                             "[14:46] sre-oncall: artifactory shows auth-tls-intermediate not_after "
                             "= 14:00 (today); 8,840 SSLHandshakeException since. Renewing the Corp "
                             "Intermediate CA cert + re-pushing the auth-svc TLS secret."),
                         "caller_id": "monitoring.alerting",
                         "contact_type": "Automated Alert",
                         "assignment_group": "SRE - Identity",
                         "business_service": "Authentication", "impact": "1 - High",
                         "urgency": "1 - High", "category": "TLS / Certificate",
                         "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "auth-svc", "app_id": "APM-AUTH",
                                     "sys_id": "sn_authsvc01", "repo": "auth-svc",
                                     "k8s_workload": "prod/auth-svc",
                                     "owner": "identity-platform@corp.example",
                                     "support_group": "SRE - Identity",
                                     "version": "v5.0.2", "environment": "production",
                                     "business_criticality": "1 - Mission Critical"}, "env": "prod"},
            "changes": [],
            "related_incidents": [
                {"number": "INC-3344", "priority": "3 - Moderate",
                 "opened_at": _dt(0, 0), "cmdb_ci": "billing-svc", "confidence": "high",
                 "title": "billing-svc TLS handshake errors (prior)",
                 "short_description": "Same Corp Intermediate CA chain; caught before full expiry"}]}},
        "cmdb": {"*": {"env": "prod", "dependencies": []}},
        "prometheus": {
            "*": {"service": {"name": "auth-svc", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "High5xxRate", "at": t_on,
                              "state": "firing", "severity": "critical", "for": "2m",
                              "runbook_url": "https://runbooks.corp.example/High5xxRate",
                              "labels": {"service": "auth-svc", "env": "prod", "team": "identity",
                                         "endpoint": "/oauth/token", "namespace": "auth-prod"},
                              "expr": "sum(rate(http_requests_total{code=~\"5..\","
                                      "service=\"auth-svc\"}[5m])) / sum(rate(http_requests_total{"
                                      "service=\"auth-svc\"}[5m])) > 0.05"}],
                  "metrics": [{"predicate": "red_errors", "value": 0.40, "at": t_on,
                               "reliability": 0.97},
                              {"subject": cert, "predicate": "days_to_expiry", "value": 0,
                               "at": t_on, "reliability": 0.99}]},
            "verify": {"service": {"name": "auth-svc", "env": "prod"},
                       "metrics": [{"subject": cert, "predicate": "days_to_expiry", "value": 90,
                                    "at": t_fix, "reliability": 0.99},
                                      {"predicate": "red_errors", "value": 0.002, "at": t_fix,
                                       "reliability": 0.98},
                                      {"predicate": "degraded", "value": False, "at": t_fix,
                                       "reliability": 0.98}]}},
        "splunk": {"*": {
            "service": {"name": "auth-svc", "env": "prod"},
            "errors": [{"signature_hash": "pkix-path-building-failed",
                        "exception_class": "SSLHandshakeException",
                        "file_line": "sun.security.validator.Validator",
                        "first_seen": t_exp.isoformat(), "_time": t_inv,
                        "count": 8840, "last_seen": t_inv.isoformat(), "trace_id": "tr-1a2",
                        "level": "ERROR", "host": "auth-svc-5b7c-tt41", "sourcetype": "log4j2",
                        "logger": "javax.net.ssl", "index": "prod_identity",
                        "message": ("javax.net.ssl.SSLHandshakeException: PKIX path building "
                                    "failed: sun.security.provider.certpath."
                                    "SunCertPathBuilderException: unable to find valid "
                                    "certification path to requested target"),
                        "stack": [
                            "javax.net.ssl.SSLHandshakeException: PKIX path building failed",
                            "\tat sun.security.ssl.Alert.createSSLException(Alert.java:131)",
                            "\tat sun.security.validator.PKIXValidator.doBuild(PKIXValidator.java:434)",
                            "\tat sun.security.validator.Validator.validate(Validator.java:264)",
                            "Caused by: sun.security.provider.certpath.SunCertPathBuilderException: "
                            "unable to find valid certification path to requested target"]}]}},
        "artifactory": {"*": {
            "artifacts": [{"sha256": "cert-auth-tls-intermediate",
                           "repo": "tls-secrets", "created": t_exp,
                           "name": "auth-tls-intermediate.pem", "path": "certs/auth/",
                           "properties": {"cert_id": "auth-tls-intermediate",
                                          "cn": "auth-svc.internal",
                                          "issuer": "Corp Intermediate CA",
                                          "not_after": t_exp.isoformat(),
                                          "not_before": _dt(0, 0).isoformat(),
                                          "serial": "4A:9F:2C:11:88:0E:73:BD",
                                          "sha1_fingerprint": "9b:74:0a:2e:1f:cc:38:5a:6d:90",
                                          "key_algorithm": "RSA-2048", "sig_algorithm": "SHA256withRSA",
                                          "san": "auth-svc.internal,auth.corp.example",
                                          "chain": "auth-tls-intermediate -> Corp Intermediate CA -> Corp Root CA"}}]}},
    }
    return subject, fx, cert


# registry key = catalog `key`; extended per-layer for obs 11 (>=2 use cases per layer)
LIVE_SCENARIOS: dict[str, Callable[[], tuple[SubjectRef, dict, str]]] = {
    "code_regression": code_regression,
    "database": database,
    "network": network,
    "deployment": deployment,
    "firewall": firewall,
    "nochange": nochange,
    "messaging": messaging,
    "infra": infra,
    "cache": cache,
    "featureflag": featureflag,
    "certificate": certificate,
}
