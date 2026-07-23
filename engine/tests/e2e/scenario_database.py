"""Scenario 2 — DATABASE root cause (DESIGN §2.5 R-K3 layer: database).

orders-api's p99 latency spikes shortly after CHG-9, a DB migration that drops an index
on orders.order_items. Differential diagnosis rules OUT an application code regression
(the service's own compute stays flat) and confirms the migration/index as root cause,
traced via AppD's JDBC exit-call boundary -> Prometheus's maxed connection pool -> the
git diff that shows the index-drop line. Discriminator: the exit call is JDBC-classified
and slow (DB-boundary bound); the service's own request-handling (p50) is unaffected.
The scripted planner drives the REAL engine through the 5-phase algebra (6 steps — the
investigate loop runs twice: hypothesize⇄evidence), exercising appd, prometheus,
servicenow and git via CapabilityCall + fixtures for the headline evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 13, 57, tzinfo=UTC) + timedelta(minutes=minutes)


T_CHANGE = _t(0)     # 13:57 CHG-9 (DB migration) lands, drops the index
T_ONSET = _t(8)      # 14:05 latency anomaly onset, ALT-1 fires
T_INV = _t(23)       # 14:20 investigation: JDBC exit-call + maxed pool pinned
T_FIX = _t(53)       # 14:50 index restored, recovery confirmed

SVC = nid(NT.SERVICE, service_name="orders-api", env="prod")
EP = nid(NT.API_ENDPOINT, service_name="orders-api", env="prod", method="GET",
         route_template="/orders/{id}/items")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
INC = nid(NT.INCIDENT, incident_id="INC-7734")
DB = nid(NT.DATABASE, db_id="orders-pg")
SCHEMA = nid(NT.SCHEMA, db_id="orders-pg", schema_name="orders")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-9")
COMMIT = nid(NT.CODE_COMMIT, sha="a1b2c3d")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) for the DATABASE root-cause scenario."""
    subject = SubjectRef(domain="app-incident", id="INC-7734", kind="incident")

    frame = phase("frame",
        calls=[call("find_recent_changes", ci="orders-api", window="30m"),
               call("active_alerts", service="orders-api", env="prod")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 5200, T_ONSET, unit="ms", source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            # the onset RED snapshot a real AppD/Prometheus pull returns for orders-api: the
            # tail (p99) is blown but the service's own compute (p50) and error rate are normal
            # — the shape that will later discriminate a DB-boundary cause from a code regression.
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_rate", 1180, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_errors", 0.015, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_latency_p50", 46, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 300, T_ONSET, unit="ms", source=S.SERVICENOW),
            event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
            # per-route RED localises the blowout to exactly the endpoint that reads
            # order_items — its own p99 is the one on fire and its 5xx share is climbing,
            # while the service aggregate is diluted by healthier routes (AppD Service Endpoint
            # / OTel http.route granularity).
            node(NT.API_ENDPOINT, service_name="orders-api", env="prod", method="GET",
                 route_template="/orders/{id}/items"),
            fact(EP, "red_rate", 410, T_ONSET, unit="rpm", source=S.APPD, reliability=0.95),
            fact(EP, "red_errors", 0.06, T_ONSET, source=S.APPD, reliability=0.95),
            fact(EP, "red_latency_p99", 8100, T_ONSET, unit="ms", source=S.APPD, reliability=0.93),
            fact(EP, "status_code_dist", {"200": 0.94, "504": 0.06}, T_ONSET, source=S.APPD,
                 reliability=0.93),
            edge(ET.EXPOSES, SVC, EP),
            edge(ET.AFFECTS, ANOM, SVC),
        ],
        narrative="orders-api p99 latency spiked to 5.2s at 14:05, 8 minutes after CHG-9 "
                  "(a DB migration) landed at 13:57. ALT-1 (HighLatencyP99) fired. p50 stays "
                  "flat at 46ms and errors at 1.5% — the tail alone is blown.")
    # scope/impact framing (the retired TRIAGE's real content — P7 5-phase algebra): the
    # subject incident node, the declared topology, and the still-bleeding severity read.
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-7734",
             title="orders-api p99 latency spike",
             short_description="orders-api p99 hit 5.2s ~8m after CHG-9 dropped an index",
             work_notes="HighLatencyP99 fired; p50 flat, tail blown. Suspect CHG-9.",
             caller_id="monitoring.alerting"),
        node(NT.DATABASE, db_id="orders-pg"),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.DEPENDS_ON, SVC, DB, origin="declared"),
        fact(SVC, "red_latency_p99", 4800, T_ONSET, unit="ms", source=S.APPD, reliability=0.9),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
    ], "narrative": frame.narrative + " Declared SEV2. Still bleeding; CMDB shows "
       "orders-api's only dependency is orders-pg. Investigate, don't blind-mitigate."})

    # INVESTIGATE opens the hypothesize⇄evidence loop (verdict=repeat keeps looping):
    # change-first hypotheses + the related-incident prior.
    investigate_open = phase("investigate",
        calls=[call("diff_range", change="CHG-9", repo="db-migrations"),
                   call("list_related_incidents", shared_dependency="orders-pg", window="5m")],
        status="repeat",
        ops=[
            node(NT.SCHEMA, db_id="orders-pg", schema_name="orders"),
            fact(SCHEMA, "index_health", 0.4, T_ONSET, source=S.PROMETHEUS, reliability=0.9),
            fact(SCHEMA, "table_count", 37, T_ONSET, source=S.CMDB, reliability=0.99),
            event(SCHEMA, "index_dropped", T_CHANGE, source=S.GIT,
                  index_name="idx_order_items_order_id"),
            propose("h1", "CHG-9 (DB migration) dropped an index on orders.order_items, "
                    "forcing full-table scans that exhaust the JDBC connection pool and "
                    "spike orders-api latency", "med", root=CHG),
            propose("h2", "A recent orders-api application code deploy introduced an "
                    "inefficient/blocking code path", "low", root=SVC),
        ],
        narrative="Change-first: CHG-9 (DB migration) landed 8m before onset — prime "
                  "suspect (H1). ServiceNow surfaces 3 sibling apps (billing-api, "
                  "fulfillment-api, returns-api) that filed the SAME latency symptom within "
                  "the same 5-minute window — all share orders-pg. A co-firing cluster that "
                  "tight is a hypothesis prior pointing at the shared DB/migration (H1), not "
                  "any one app's code. No application deploy is on record; a code regression "
                  "remains a weaker alternative (H2) pending investigation.")

    # the loop's confirm/refute turn: pin the JDBC exit-call boundary + maxed pool (H1),
    # rule out code (H2) — the gate (promotion + refutation) passes, so the loop advances.
    p99_fact = fid(SVC, "red_latency_p99", T_INV)
    p50_fact = fid(SVC, "red_latency_p50", T_INV)
    conn_fact = fid(DB, "active_connections", T_INV)
    diff_fact = fid(COMMIT, "lines_added", T_CHANGE)

    investigate_confirm = phase("investigate",
        calls=[call("get_snapshots", service="orders-api", bt="GetOrderItems"),
                    call("instant_query", query="conn_pool_util{db='orders-pg'}")],
        ops=[
            edge(ET.CAUSED_BY, H1, CHG, level="high"),
            update("h2", status="refuted", add_refuting=[p50_fact],
                   basis="p50 latency stays flat at 42ms — the request-handling code path "
                   "itself is unaffected; the tail is entirely JDBC-wait-bound, ruling out "
                   "an application code regression"),
            update("h1", status="supported", level="high",
                   add_supporting=[p99_fact, conn_fact, diff_fact],
                   basis="get_snapshots pins the exit call as JDBC->orders-pg with p99 at "
                   "7900ms; instant_query shows the pool maxed at 200/200 active "
                   "connections; diff_range on CHG-9 shows the added DROP INDEX line — "
                   "the index removal forces full scans that exhaust the pool"),
        ],
        narrative="orders-api's own compute is fine (p50 flat at 42ms) — the tail is 100% "
                  "JDBC-bound: get_snapshots pins the exit call to orders-pg, whose "
                  "connection pool is maxed (200/200 via instant_query). git diff_range on "
                  "CHG-9 shows the migration dropped an index, forcing full-table scans. "
                  "H2 (code regression) refuted; H1 (migration/index) confirmed at high "
                  "confidence.")

    act = phase("act", [
        update("h1", level="high",
               basis="proposed fix: re-create the dropped index on orders.order_items "
               "(equivalently, roll back CHG-9) — removes the full-scan load and drains "
               "the JDBC pool"),
    ], "Safest reversible fix: re-add the dropped index (or roll back CHG-9). Awaiting "
       "approval (gated).")

    verify = phase("verify", [
        fact(SVC, "red_latency_p99", 95, T_FIX, unit="ms", source=S.APPD, reliability=0.95),
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        update("h1", status="confirmed", level="high",
               basis="post-fix: p99 back to ~95ms, JDBC exit-call latency and pool "
               "pressure gone, anomaly cleared — confirms the causal chain"),
    ], "Post-fix: p99 back to 95ms; anomaly cleared. Root cause confirmed.")

    close = phase("close", [], "Postmortem: CHG-9 (DB migration) dropped an index on "
                  "orders.order_items -> full-table scans -> JDBC pool exhaustion -> "
                  "orders-api latency spike; re-adding the index resolved it. An "
                  "application code regression was investigated and ruled out.",
                  status="done")

    script = [frame, investigate_open, investigate_confirm, act, verify, close]

    fixtures = {
        "find_recent_changes": {
            "changes": [
                {
                    "number": "CHG-9",
                    "type": "database",
                    "cmdb_ci": {"display_value": "orders-api"},
                    "requested_by": "dba-jsmith",
                    "start_date": T_CHANGE,
                },
            ],
        },
        "active_alerts": {
            "service": {"name": "orders-api", "env": "prod"},
            "alerts": [
                {"id": "ALT-1", "alertname": "HighLatencyP99", "at": T_ONSET, "state": "firing"},
            ],
        },
        "diff_range": {
            "commit": {"sha": "a1b2c3d", "repo": "db-migrations", "author": "dba-jsmith",
                      "parent_sha": "9f8e7d6", "authored_at": T_CHANGE},
            "diff": {"at": T_CHANGE, "files_changed": 1, "lines_added": 1, "lines_deleted": 0,
                    "reliability": 0.99},
            "change": {"change_id": "CHG-9", "change_type": "database"},
        },
        "get_snapshots": {
            "service": {"name": "orders-api", "env": "prod"},
            "bt_metrics": [
                {"predicate": "red_latency_p99", "value": 7900, "unit": "ms", "at": T_INV,
                 "reliability": 0.93},
                {"predicate": "red_latency_p50", "value": 42, "unit": "ms", "at": T_INV,
                 "reliability": 0.95},
            ],
            "snapshots": [
                {"exit_calls": [{"type": "JDBC", "db_id": "orders-pg", "engine": "postgres"}]},
            ],
        },
        "instant_query": {
            "metrics": [
                {"subject": DB, "predicate": "active_connections", "value": 200,
                 "unit": "conn", "at": T_INV, "reliability": 0.97},
                {"subject": DB, "predicate": "max_connections", "value": 200,
                 "unit": "conn", "at": T_INV, "reliability": 0.97},
                # the pool is pinned and the full scans show up as a slow-query surge — the
                # USE snapshot the migration/index-drop produced (replication_lag stays normal).
                {"subject": DB, "predicate": "conn_pool_util", "value": 1.0,
                 "unit": "ratio", "at": T_INV, "reliability": 0.97},
                {"subject": DB, "predicate": "slow_query_rate", "value": 342,
                 "unit": "per_min", "at": T_INV, "reliability": 0.95},
                {"subject": DB, "predicate": "replication_lag", "value": 0.3,
                 "unit": "s", "at": T_INV, "reliability": 0.96},
            ],
        },
        # co-firing siblings: 3 other apps on the same orders-pg filed the same latency
        # symptom in the same window — folded as related Incident nodes + SIMILAR_TO edges
        # off the primary incident (a hypothesis prior).
        "list_related_incidents": {
            "primary_incident": "INC-7734",
            "related_incidents": [
                {"number": "INC-7735", "priority": "3 - Moderate", "opened_at": _t(9),
                 "cmdb_ci": "billing-api", "confidence": "high"},
                {"number": "INC-7736", "priority": "3 - Moderate", "opened_at": _t(10),
                 "cmdb_ci": "fulfillment-api", "confidence": "high"},
                {"number": "INC-7737", "priority": "4 - Low", "opened_at": _t(11),
                 "cmdb_ci": "returns-api", "confidence": "med"},
            ],
        },
    }

    return subject, script, fixtures
