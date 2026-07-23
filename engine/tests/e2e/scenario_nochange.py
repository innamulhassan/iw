"""Scenario 2 — the NO-CHANGE incident (DESIGN §2.1 R-G8's "no-change (Host/BatchJob /
Database saturation)" class; the class the critic flagged).

checkout-api's request rate climbs 3.4x baseline and p99 latency spikes to 6.8s at 09:00.
ServiceNow's change log for the incident window is EMPTY — no deploy, no config change,
nothing to blame. INVESTIGATE falls back from change-first to the USE-saturation signal:
AppD's snapshot exit-calls discover checkout-api's only backend dependency (checkout-db,
JDBC) and Prometheus shows its connection-pool utilization climbing in lockstep with the
onset — an organic traffic surge saturating the pool, not a code/deploy regression.

INVESTIGATE rules OUT the rival "an unreported change caused this" hypothesis (the
find_recent_changes null-result IS the refuting evidence, R-P2) and confirms the pool
hypothesis to high confidence via the differential-diagnosis gate — but there is no
change/commit to revert, so ACT only scales capacity (a gated mitigation, not a fix) and
VERIFY never promotes the hypothesis to CONFIRMED: the pool trend correlates with the
symptom but was never causally isolated (no revert experiment is possible for an organic
no-change incident). The scripted planner drives the real engine through the 5-phase
algebra (6 steps — the investigate loop runs twice) to "mitigated" with
confirmed=None.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import (
    call,
    edge,
    event,
    fact,
    fid,
    hid,
    nid,
    no_evidence,
    node,
    phase,
    propose,
    update,
)


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


T_ONSET = _t(0)     # 09:00 traffic surge crosses threshold, HighConnPoolUtilization fires
T_TRIAGE = _t(8)    # 09:08 dependency discovery / narrowing
T_INV = _t(20)      # 09:20 investigation
T_FIX = _t(45)      # 09:45 pool scaled up + recovery confirmed

SVC = nid(NT.SERVICE, service_name="checkout-api", env="prod")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
DB = nid(NT.DATABASE, db_id="checkout-db")
INC = nid(NT.INCIDENT, incident_id="INC-9001")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures). No-change class: prometheus (active_alerts +
    range_query) surfaces the organic traffic surge + climbing pool util; appd
    (get_snapshots) discovers the DB dependency at the boundary where the pool times out;
    servicenow (find_recent_changes) comes back with an EMPTY change list — the fallback
    trigger. INVESTIGATE seeds the leading candidate from the saturation signal (root=DB),
    never a ChangeEvent. Closes MITIGATED: pool scaled up, symptom clears, but the leading
    hypothesis is never promoted past 'supported' — no confirmed root cause."""
    subject = SubjectRef(domain="app-incident", id="INC-9001", kind="incident")

    frame = phase("frame",
        calls=[call("active_alerts"), call("range_query")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 6800, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            # under the surge even p50 climbs (everything queues behind the pool) and a slice of
            # requests time out — a saturation shape (whole latency distribution shifts up),
            # distinct from a code fault's clean p50 + concentrated error spike.
            fact(SVC, "red_errors", 0.08, T_ONSET, source=S.PROMETHEUS, reliability=0.96),
            fact(SVC, "red_latency_p50", 780, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 500, T_ONSET, unit="ms", source=S.SERVICENOW),
            event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
            edge(ET.AFFECTS, ANOM, SVC),
        ],
        narrative="checkout-api p99 latency spiked to 6.8s and request rate jumped 3.4x "
                  "baseline at 09:00; HighConnPoolUtilization fired on the service.")
    # scope/impact framing (the retired TRIAGE's real content — P7 5-phase algebra)
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-9001",
             title="checkout-api latency under traffic surge",
             short_description="checkout-api p99 hit 6.8s under a 3.4x surge; no change logged",
             work_notes="HighConnPoolUtilization; empty change log. Organic surge.",
             caller_id="monitoring.alerting"),
        edge(ET.AFFECTS, INC, SVC),
        event(INC, "declared", T_TRIAGE, source=S.SERVICENOW),
        fact(DB, "conn_pool_util", 0.86, T_TRIAGE, source=S.PROMETHEUS, reliability=0.98),
        event(DB, "connection_storm", T_TRIAGE, source=S.PROMETHEUS),
    ], "calls": [*frame.calls, call("get_snapshots")],
       "narrative": frame.narrative + " Declared SEV2, still bleeding. AppD snapshot "
       "exit-calls show checkout-api's only backend dependency is checkout-db (JDBC) — pool "
       "util already at 86% and climbing. Investigate, don't blind-mitigate."})

    investigate_open = phase("investigate",
        calls=[call("find_recent_changes")],
        status="repeat",
        ops=[
            propose("h1", "Organic traffic surge saturated checkout-db's connection pool "
                    "(USE-saturation, no code/deploy change)", "med", root=DB),
            propose("h2", "An unreported deploy or config change to checkout-api caused "
                    "the regression", "low", root=None),
        ],
        narrative="ServiceNow's change log for the incident window is EMPTY — change-"
                  "first comes up dry. Falling back to the onset-correlated saturation "
                  "signal: H1 (the pool) leads on the USE trend; H2 (a change nobody "
                  "logged) stays alive as the rival until we can rule it out.")

    no_change_fact = fid(SVC, "no_evidence:find_recent_changes", T_INV)
    db_fact = fid(DB, "conn_pool_util", T_INV)
    investigate_confirm = phase("investigate", [
        fact(DB, "conn_pool_util", 0.97, T_INV, source=S.PROMETHEUS, reliability=0.99),
        # the pool internals that make the saturation concrete: connections at the ceiling and a
        # slow-query surge as everything contends — the USE picture with no change behind it.
        fact(DB, "active_connections", 194, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.99),
        fact(DB, "max_connections", 200, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.99),
        fact(DB, "slow_query_rate", 88, T_INV, unit="per_min", source=S.PROMETHEUS, reliability=0.98),
        fact(SVC, "red_rate", 4.1, T_INV, unit="x_baseline", source=S.PROMETHEUS, reliability=0.97),
        no_evidence("find_recent_changes", SVC, T_INV,
                    basis="ServiceNow change log re-checked at the DB boundary — still "
                    "clean, no deploy/config change in the incident window"),
        edge(ET.CAUSED_BY, H1, DB, level="high"),
        update("h2", status="refuted", add_refuting=[no_change_fact],
               basis="no change/config event exists anywhere in the window — the "
               "'invisible change' rival has nothing to point at"),
        update("h1", status="supported", level="high", add_supporting=[db_fact],
               basis="pool util 97% tracks the onset 1:1; the only backend dependency "
               "is checkout-db — the causal path narrows to pool saturation"),
    ], "Ruled out the phantom change (clean change log). Pool util at 97% tracks the "
       "onset exactly, and checkout-db is the only dependency in play.")

    act = phase("act", [
        update("h1", level="high", basis="mitigation: scale checkout-db's connection "
               "pool and add read capacity — a reversible capacity fix, not a revert "
               "(there's no change to revert)"),
    ], "No change to roll back, so the safest reversible action is capacity: scale the "
       "pool and add a read replica. Awaiting approval (gated).")

    verify = phase("verify", [
        fact(DB, "conn_pool_util", 0.52, T_FIX, source=S.PROMETHEUS, reliability=0.98),
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        event(INC, "mitigated", T_FIX, source=S.SERVICENOW),
    ], "Post-scale-up: pool util back to 52%, symptom cleared. The saturation trend "
       "still only correlates with the onset — there is no revert experiment to run for "
       "an organic surge, so the hypothesis stays 'supported', not confirmed.")

    close = phase("close", [], "Postmortem: checkout-api degraded under an organic 3.4x "
                  "traffic surge that saturated checkout-db's connection pool. No change "
                  "or deploy was found in the window (ruled out). Scaling the pool "
                  "resolved the symptom; root cause is the leading, evidence-supported "
                  "but unconfirmed hypothesis — closed MITIGATED.", status="done")

    script = [frame, investigate_open, investigate_confirm, act, verify, close]

    fixtures = {
        "active_alerts": {
            "service": {"name": "checkout-api", "env": "prod"},
            "alerts": [
                {"id": "ALT-1", "alertname": "HighConnPoolUtilization", "at": T_ONSET,
                 "state": "firing"},
            ],
        },
        "range_query": {
            "service": {"name": "checkout-api", "env": "prod"},
            "metrics": [
                {"predicate": "red_rate", "value": 3.4, "unit": "x_baseline", "at": T_ONSET,
                 "reliability": 0.97},
                {"predicate": "red_latency_p99", "value": 6800, "unit": "ms", "at": T_ONSET,
                 "reliability": 0.96},
            ],
        },
        "get_snapshots": {
            "service": {"name": "checkout-api", "env": "prod"},
            "snapshots": [
                {"exit_calls": [
                    {"type": "JDBC", "db_id": "checkout-db", "engine": "postgres"},
                ]},
            ],
        },
        "find_recent_changes": {
            "changes": [],   # the no-change class: an EMPTY change list is first-class, not an error
        },
    }

    return subject, script, fixtures
