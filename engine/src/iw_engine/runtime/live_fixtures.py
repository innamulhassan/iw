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
            "incident": {"number": "INC-7731", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-api"}, "env": "prod"},
            "changes": [{"number": "CHG-DEP-99", "type": "deployment",
                         "cmdb_ci": {"display_value": "checkout-api"},
                         "requested_by": "svc-deploy-bot", "start_date": t_dep, "env": "prod",
                         "u_release_tag": "checkout-api-rev43", "u_commit_sha": "9f2a1e0"}]}},
        "cmdb": {"*": {"env": "prod", "dependencies": [
            {"parent": "checkout-api", "parent_type": "cmdb_ci_service",
             "child": "checkout-db", "child_type": "cmdb_ci_database",
             "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            # onset RED: 0 ready replicas -> availability collapsed, near-100% of the trickle 5xx.
            "*": {"service": {"name": "checkout-api", "env": "prod"},
                  "alerts": [{"id": "ALT-1", "alertname": "KubePodCrashLooping", "at": t_on,
                              "state": "firing"}],
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
                             "-> CrashLoopBackOff", "at": t_on}]},
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
                       "parent_sha": "7a1c220", "authored_at": t_dep},
            "pr": {"pr_id": "482", "repo": "checkout-api", "author": "jdoe",
                   "merged_sha": "9f2a1e0", "event": "merged", "at": t_dep},
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
    return subject, fx, "change_event:chg-dep-99"


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
            "incident": {"number": "INC-7702", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-api"}, "env": "prod"},
            "changes": [{"number": "CHG-3311", "type": "network",
                         "cmdb_ci": {"display_value": "checkout-api"},
                         "requested_by": "netops-automation", "start_date": t_chg, "env": "prod"}],
            # a true RECURRENCE: same checkout-api + same egress vendor path went down before
            # (INC-7699), reverted then too — a strong hypothesis prior sharpening the FW rule.
            "primary_incident": "INC-7702",
            "related_incidents": [
                {"number": "INC-7699", "priority": "2 - High", "opened_at": t_prior,
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
                              "at": t_on, "state": "firing"}],
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
                                 "unit": "pct", "at": t_inv, "reliability": 0.97},
                                {"subject": SEG_FRAUD, "predicate": "retrans_segs", "value": 0,
                                 "unit": "count", "at": t_inv, "reliability": 0.97},
                                {"subject": SEG_GEO, "predicate": "probe_success", "value": 1,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.99},
                                {"subject": SEG_GEO, "predicate": "packet_loss", "value": 0.0,
                                 "unit": "pct", "at": t_inv, "reliability": 0.97},
                                {"subject": SEG_PAY, "predicate": "probe_success", "value": 1,
                                 "unit": "ratio", "at": t_inv, "reliability": 0.99},
                                {"subject": SEG_PAY, "predicate": "packet_loss", "value": 0.0,
                                 "unit": "pct", "at": t_inv, "reliability": 0.97},
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
                      {"predicate": "epm", "value": 3, "unit": "cpm", "at": t_on,
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
                "reliability": 0.97}]},
            "verify": {"fw_denies": []}},
    }
    return subject, fx, "firewall_rule:fw-egr-118"


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
            "incident": {"number": "INC-9100", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-api"}, "env": "prod"},
            # the no-change class: an EMPTY change list is first-class (not an error) — it is
            # the REFUTING evidence for the 'invisible change' rival (h2): no deploy/config
            # event exists anywhere in the incident window to blame.
            "changes": []}},
        "cmdb": {"*": {"env": "prod",
                       "ci_attrs": {"checkout-db": {"engine": "postgresql"}},
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
                              "at": t_on, "state": "firing"}],
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
            "incident": {"number": "INC-8801", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "order-processor"}, "env": "prod"},
            "changes": [{"number": "CHG-55", "type": "deployment",
                         "cmdb_ci": {"display_value": "order-processor"},
                         "requested_by": "dev-mq", "start_date": t_chg}]}},
        "cmdb": {"*": {"env": "prod",
                       "ci_attrs": {"orders.events": {"broker": "kafka-prod-1", "partitions": 12}},
                       "dependencies": [
                           {"parent": "order-processor", "parent_type": "cmdb_ci_service",
                            "child": "orders.events", "child_type": "cmdb_ci_message_queue",
                            "rel_type": "Depends on::Used by"}]}},
        "prometheus": {
            "*": {"service": {"name": "order-processor", "env": "prod"},
                  "alerts": [{"id": "ALT-7", "alertname": "HighConsumerLag", "at": t_on,
                              "state": "firing"}],
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
            "diff": {"at": t_chg, "files_changed": 1, "lines_added": 22, "lines_deleted": 4,
                     "reliability": 0.99,
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
            "incident": {"number": "INC-8900", "opened_at": t_on, "priority": "2 - High",
                         "assigned_to": "sre-oncall", "state": "in_progress",
                         "cmdb_ci": {"display_value": "checkout-svc"}, "env": "prod"},
            # no-change class: a resource/co-tenancy fault, NOT a deploy — rules out CHG cause
            "changes": []}},
        "cmdb": {"*": {
            "env": "prod",
            "ci_attrs": {
                "etl-nightly": {"schedule_id": "sched-3", "schedule": "0 2 * * *"},
                "node-prod-17": {"asset_id": "AST-4417", "cpu_cores": 16, "mem_gb": 64,
                                 "region": "us-east-1"}},
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
                              "state": "firing"}],
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
}
