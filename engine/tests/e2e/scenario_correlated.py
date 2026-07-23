"""Scenario — AIOps CORRELATED STORM + identity graduation (W7 demo-completeness M27/M28).

The demo surface's most sophisticated advertised behaviors were never SHOWN on mocks: the
AIOps correlation adapter (BigPanda) had no fixture, and the identity-graduation subsystem
(generic_ci → real type via Retype, provisional-twin → canonical via Merge) plus Retract were
unit-green but demo-invisible. This scenario exercises all of them end-to-end through the REAL
engine, converging on a confirmed root cause exactly like every other scenario.

Story: BigPanda correlates a latency alert STORM across payments-svc + orders-svc + ledger-svc
into ONE incident (M28: the correlation adapter's normalize() runs — member alerts FIRED_ON
their services, the primary incident, a SIMILAR_TO prior). ServiceNow's incident record names
payments-svc; its backing datastore CI comes back UNCLASSIFIED, so it lands as a `generic_ci`
(class_hint cmdb_ci_db_ora) placed in the topology through the P3 type airlock. A DB migration
CHG-DB-500 dropped an index 6 minutes before onset. The investigation:
  - RETYPES the generic_ci to its real DATABASE type once the class_hint is corroborated — its
    quarantined fact + airlocked edge re-home to the real entity (M27: Retype graduation);
  - MERGES an AppD-only observation (keyed only by app_id) into canonical payments-svc (M27:
    the provisional-twin → canonical lane);
  - RETRACTS a flaky APM p99 scrape as a wrong observation (M27: the tombstone).
Root = CHG-DB-500 (the migration); a code-regression rival is ruled out by the service's flat
p50. Re-creating the index clears the symptom → resolved.

Deterministic (fixed clock + ids), so it produces a stable golden like the other 11 scenarios.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.operations import AddNode, Merge, Retract, Retype
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, span, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


T_MIG = _t(0)        # 09:00 CHG-DB-500 (DB migration) lands, drops the index
T_ONSET = _t(6)      # 09:06 the correlated alert storm fires; latency onset
T_INV = _t(20)       # 09:20 investigation: graduate the datastore, pin the migration
T_FIX = _t(45)       # 09:45 index restored, recovery confirmed
T_PRIOR = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)   # a similar prior incident (SIMILAR_TO)

SVC = nid(NT.SERVICE, service_name="payments-svc", env="prod")
ORD = nid(NT.SERVICE, service_name="orders-svc", env="prod")
LED = nid(NT.SERVICE, service_name="ledger-svc", env="prod")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-6")
INC = nid(NT.INCIDENT, incident_id="INC-6001")
PRIOR = nid(NT.INCIDENT, incident_id="INC-5990")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-DB-500")
GCI = nid(NT.GENERIC_CI, ci_id="SYS-DB77")        # unclassified CMDB CI (escape hatch)
DB = nid(NT.DATABASE, db_id="payments-ora")       # what the generic_ci graduates INTO
TWIN = "service:~appd:apm-pay"                     # provisional AppD-only twin (merged into SVC)
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) for the AIOps correlated-storm + graduation scenario."""
    subject = SubjectRef(domain="app-incident", id="INC-6001", kind="incident")

    # ── FRAME: BigPanda correlates the storm; ServiceNow names the incident + an unclassified CI ──
    frame = phase("frame",
        calls=[call("get_correlated_incident", incident_id="INC-6001"),
               call("get_incident", incident_id="INC-6001"),
               call("get_ci", sys_id="SYS-DB77"),
               call("find_recent_changes", ci="payments-svc", window="30m")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-6"),
            fact(ANOM, "onset_value", 4200, T_ONSET, unit="ms", source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 1, T_ONSET, source=S.SERVICENOW),
            # onset RED for payments-svc: the tail is blown but the service's own compute (p50)
            # is normal — the shape that later discriminates a DB-boundary cause from code.
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_rate", 900, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_errors", 0.02, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            # a FLAKY APM p99 scrape during the storm — a wrong observation the investigation
            # later RETRACTS (the collection gap read 9999ms; the real tail is in the datastore).
            fact(SVC, "red_latency_p99", 9999, T_ONSET, unit="ms", source=S.APPD, reliability=0.6),
            # the unclassified datastore CI lands as a generic_ci and is placed in the topology
            # through the P3 type airlock (the DEPENDS_ON edge is admitted provisional/discovered).
            edge(ET.DEPENDS_ON, SVC, GCI),
            # a quarantined fact on the escape hatch: an unknown vendor name (`ora_apply_lag`)
            # lands under the airlock spelling `x.servicenow.ora_apply_lag`, provisional — it
            # re-homes onto the real DATABASE when the CI graduates (Retype), history intact.
            fact(GCI, "ora_apply_lag", 180, T_ONSET, source=S.SERVICENOW, reliability=0.85),
            # a captured distributed trace at onset — the SPAN species (§2.6): a bounded happening SVC is in
            span(SVC, "trace", T_ONSET, ended_at=T_ONSET + timedelta(milliseconds=4200),
                 correlation_id="trace-payments-5b30", value={"error": False}, reliability=0.95),
            edge(ET.AFFECTS, ANOM, SVC),
            edge(ET.AFFECTS, INC, SVC),
        ],
        narrative="BigPanda correlated a latency alert storm across payments-svc, orders-svc and "
                  "ledger-svc into ONE incident (INC-6001) at 09:06 — 6 minutes after CHG-DB-500 "
                  "(a DB migration) landed at 09:00. ServiceNow's CMDB returns the backing "
                  "datastore as an UNCLASSIFIED CI (class_hint cmdb_ci_db_ora), so it enters the "
                  "topology as a generic_ci. p50 stays flat — the tail alone is blown.")

    # ── INVESTIGATE opens the hypothesize⇄evidence loop (verdict=repeat keeps looping) ──
    investigate_open = phase("investigate",
        status="repeat",
        ops=[
            propose("h1", "CHG-DB-500 (DB migration) dropped an index on the payments datastore, "
                    "forcing full-table scans that saturate the pool and cascade latency across "
                    "the correlated services", "med", root=CHG),
            propose("h2", "a recent payments-svc application code deploy introduced an "
                    "inefficient/blocking hot path", "low", root=SVC),
        ],
        narrative="Change-first: CHG-DB-500 (DB migration) landed 6m before onset — prime suspect "
                  "(H1). The BigPanda-correlated cluster (3 services, one storm) all read through "
                  "the same datastore, a prior pointing at the shared migration, not any one app's "
                  "code. A code regression is a weaker alternative (H2) pending investigation.")

    # the confirm/refute turn — graduate the datastore, merge the AppD twin, retract the flaky
    # scrape, pin the migration, rule out code. Direct ops (pass 1 mints the twin; pass 2 applies
    # Retract → the twin's fact → Merge → Retype → the real-DATABASE facts, in list order).
    p50_fact = fid(SVC, "red_latency_p50", T_INV)
    lag_fact = fid(DB, "replication_lag", T_INV)
    pool_fact = fid(DB, "conn_pool_util", T_INV)
    flaky_p99 = fid(SVC, "red_latency_p99", T_ONSET)

    investigate_confirm = phase("investigate",
        ops=[
            # the service's own compute is fine (flat p50) — refutes the code rival
            fact(SVC, "red_latency_p50", 44, T_INV, unit="ms", source=S.APPD, reliability=0.95),
            # RETRACT the flaky APM p99 scrape — a wrong observation, tombstoned (never deleted)
            Retract(target=flaky_p99, invalidated_by=p50_fact,
                    reason="flaky APM scrape — the 9999ms p99 was a collection gap during the "
                    "storm; the real tail is JDBC-wait in the datastore, not the service"),
            # an AppD-only observation of the app, keyed ONLY by app_id (no service_name/env):
            # mints a PROVISIONAL twin; the operator confirms identity and MERGES it into canonical.
            AddNode(type=NT.SERVICE, props={"app_id": "APM-PAY"}, source=S.APPD),
            fact("appd:APM-PAY", "red_errors", 0.02, T_INV, source=S.APPD, reliability=0.9),
            Merge(provisional_id=TWIN, canonical_id=SVC,
                  reason="operator confirmed AppD app APM-PAY is payments-svc — folding the "
                  "AppD-only observation into the canonical entity"),
            # RETYPE the generic_ci to its real DATABASE type — its quarantined fact + airlocked
            # edge re-home to the real entity, opening the DATABASE vocabulary.
            Retype(target=GCI, new_type=NT.DATABASE,
                   props={"db_id": "payments-ora", "engine": "oracle", "version": "19c",
                          "owner": "payments-platform@corp.example"},
                   reason="class_hint cmdb_ci_db_ora corroborated by the DBA migration ticket + "
                   "the JDBC exit-call boundary — the CI is the payments datastore"),
            # the real DATABASE telemetry, now legal on the graduated entity: replication lag and
            # the pool pinned by the index-drop's full scans (support H1).
            fact(DB, "replication_lag", 8.5, T_INV, unit="s", source=S.PROMETHEUS, reliability=0.96),
            fact(DB, "conn_pool_util", 0.98, T_INV, unit="ratio", source=S.PROMETHEUS, reliability=0.97),
            edge(ET.CAUSED_BY, H1, CHG, level="high"),
            update("h2", status="refuted", add_refuting=[p50_fact],
                   basis="p50 latency stays flat at 44ms — the request-handling path is "
                   "unaffected; the tail is entirely datastore-bound, ruling out a code regression"),
            update("h1", status="supported", level="high", add_supporting=[lag_fact, pool_fact],
                   basis="the datastore (graduated from the unclassified CI) shows replication lag "
                   "at 8.5s and the pool pinned at 98% — the index-drop's full scans; CHG-DB-500 "
                   "is the migration that dropped it"),
        ],
        narrative="The unclassified CI GRADUATES to a DATABASE (Retype) once the class_hint is "
                  "corroborated — its quarantined onset fact + airlocked dependency re-home to the "
                  "real entity. An AppD-only observation is MERGED into payments-svc. The flaky "
                  "9999ms p99 is RETRACTED. On the real datastore: replication lag 8.5s, pool 98% "
                  "— the migration's dropped index. p50 flat (44ms) refutes H2; H1 confirmed high.")

    act = phase("act", [
        update("h1", level="high",
               basis="proposed fix: re-create the dropped index on the payments datastore "
               "(equivalently, roll back CHG-DB-500) — removes the full-scan load and drains "
               "the pool"),
    ], "Safest reversible fix: re-add the dropped index (or roll back CHG-DB-500). Awaiting "
       "approval (gated).")

    verify = phase("verify", [
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        fact(DB, "replication_lag", 0.2, T_FIX, unit="s", source=S.PROMETHEUS, reliability=0.96),
        fact(DB, "conn_pool_util", 0.31, T_FIX, unit="ratio", source=S.PROMETHEUS, reliability=0.97),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        update("h1", status="confirmed", level="high",
               basis="post-fix: replication lag and pool pressure gone, degraded cleared, anomaly "
               "cleared across the correlated services — confirms the migration/index chain"),
    ], "Post-fix: the datastore recovered (lag 0.2s, pool 31%); anomaly cleared. Root confirmed.")

    close = phase("close", [], "Postmortem: CHG-DB-500 (DB migration) dropped an index on the "
                  "payments datastore -> full-table scans -> pool exhaustion -> a latency storm "
                  "BigPanda correlated across payments/orders/ledger. Re-adding the index resolved "
                  "it. The datastore was discovered as an unclassified CI and graduated to a "
                  "DATABASE; a code regression was investigated and ruled out.",
                  status="done")

    script = [frame, investigate_open, investigate_confirm, act, verify, close]

    fixtures = {
        # M28 — the AIOps correlation adapter's normalize() runs end-to-end: the primary incident,
        # the affected-service blast radius, the member alerts (each FIRED_ON its service), and the
        # SIMILAR_TO prior. This is the advertised capability the mock world could never answer.
        "get_correlated_incident": {
            "primary_incident": "INC-6001",
            "severity": "P1",
            "affected_services": [
                {"name": "payments-svc", "env": "prod"},
                {"name": "orders-svc", "env": "prod"},
                {"name": "ledger-svc", "env": "prod"},
            ],
            "correlated_alerts": [
                {"id": "BP-1", "alertname": "HighLatencyP99", "at": T_ONSET, "state": "firing",
                 "service": "payments-svc"},
                {"id": "BP-2", "alertname": "HighLatencyP99", "at": T_ONSET, "state": "firing",
                 "service": "orders-svc"},
                {"id": "BP-3", "alertname": "ErrorRateHigh", "at": T_ONSET, "state": "firing",
                 "service": "ledger-svc"},
            ],
            "related_incidents": [
                {"number": "INC-5990", "priority": "P2", "opened_at": T_PRIOR,
                 "cmdb_ci": "payments-ora", "confidence": "high"},
            ],
        },
        # ServiceNow names the incident (M2 record fields) and resolves the affected service by its
        # CMDB sys_id ONLY (no app_id) — so the later AppD-only observation cannot auto-resolve and
        # must be graduated by an explicit Merge.
        "get_incident": {
            "incident": {
                "number": "INC-6001", "priority": "P1", "assigned_to": "sre-oncall",
                "opened_at": T_ONSET, "state": "in_progress", "env": "prod",
                "title": "payments platform P1 — correlated latency storm",
                "short_description": "payments/orders/ledger latency SEV1; AIOps rolled 3 alerts "
                                     "into one incident",
                "description": "BigPanda correlated a latency-alert storm across payments-svc, "
                               "orders-svc and ledger-svc into ONE P1 (INC-6001) at 09:06 UTC — 6 "
                               "minutes after CHG-DB-500 (a DB migration) landed at 09:00. All three "
                               "services read through the same payments datastore; p99 is blown "
                               "while p50 stays flat, pointing at the shared DB boundary, not any "
                               "one app's code. The backing CI returned unclassified from CMDB.",
                "work_notes": "BigPanda correlated 3 alerts across 3 services. Suspect the 09:00 "
                              "DB migration CHG-DB-500.",
                "caller_id": "bigpanda-correlation",
                "assignment_group": "SRE - Payments Platform", "business_service": "Payments",
                "impact": "1 - High", "urgency": "1 - High",
                "cmdb_ci": {"display_value": "payments-svc", "sys_id": "SYS-PAY-1",
                            "owner": "payments-platform@corp.example", "version": "v8.3.0"},
            },
        },
        # the backing datastore CI is UNCLASSIFIED (sys_class_name is not cmdb_ci_service) → the
        # ServiceNow adapter folds it as a generic_ci carrying its class_hint (the escape hatch).
        "get_ci": {
            "ci": {"sys_id": "SYS-DB77", "sys_class_name": "cmdb_ci_db_ora", "name": "payments-ora"},
        },
        # the DB migration — a pure schema change (no release tag / commit): the CHANGE_EVENT is
        # the actionable root, per the rooting doctrine (root a migration at the change, not the DB).
        "find_recent_changes": {
            "changes": [
                {"number": "CHG-DB-500", "type": "db-migration",
                 "short_description": "Payments datastore migration — drop legacy index",
                 "description": "Scheduled maintenance migration on the payments datastore. Drops "
                                "a legacy composite index believed superseded, to speed up writes "
                                "ahead of the quarter-end batch. No application read-path review — "
                                "the migration forces full-table scans that saturate the pool and "
                                "cascade latency across every service reading the datastore.",
                 "cmdb_ci": {"display_value": "payments-svc",
                             "owner": "payments-platform@corp.example"},
                 "requested_by": "dba-team",
                 "start_date": T_MIG, "env": "prod"},
            ],
        },
    }

    return subject, script, fixtures
