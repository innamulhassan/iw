"""Scenario 1 — application CODE regression (DESIGN §2.5 R-K3 layer 1).

payments-api throws 5xx after the v4.12.0 deploy. Differential diagnosis rules OUT the
database (pool healthy) and confirms a NullPointerException introduced by commit abc123,
traced via error signature → deploy → commit. Discriminator: pods Ready but throwing.
The scripted planner drives the real engine through the 5-phase algebra (6 steps — the
investigate loop runs twice: hypothesize⇄evidence).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import edge, event, fact, fid, hid, nid, node, phase, propose, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 13, 47, tzinfo=UTC) + timedelta(minutes=minutes)


T_CHANGE = _t(0)     # 13:47 deploy v4.12.0
T_ONSET = _t(13)     # 14:00 5xx onset
T_INV = _t(25)       # investigation
T_FIX = _t(40)       # rollback + recovery

SVC = nid(NT.SERVICE, service_name="payments-api", env="prod")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-1")
DB = nid(NT.DATABASE, db_id="payments-ora")
COMMIT = nid(NT.CODE_COMMIT, sha="abc123")
ERRSIG = nid(NT.ERROR_SIGNATURE, signature_hash="npe-taxcalc")
INC = nid(NT.INCIDENT, incident_id="INC-4821")
# co-firing siblings: two other services that filed the SAME NPE after picking up the shared
# tax-calc library in v4.12.0 — a related prior that reinforces the code-fault hypothesis.
INC_R1 = nid(NT.INCIDENT, incident_id="INC-4788")
INC_R2 = nid(NT.INCIDENT, incident_id="INC-4790")
H1, H2 = hid("h1"), hid("h2")


def build(refuted_variant: bool = False):
    """Returns (subject, script). refuted_variant flips the leading hypothesis so the engine
    must backtrack — proving the reasoning path, not just the answer."""
    subject = SubjectRef(domain="app-incident", id="INC-4821", kind="incident")

    frame = phase("frame", [
        node(NT.SERVICE, service_name="payments-api", env="prod"),
        node(NT.ANOMALY, anomaly_id="ANOM-1"),
        node(NT.ALERT, alert_id="ALT-1"),
        node(NT.CHANGE_EVENT, change_id="CHG-1"),
        fact(SVC, "red_errors", 0.40, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
        fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
        # the full onset RED snapshot: throughput holding but 40% of calls 5xx-ing, and the
        # error tail dragging p99 to 4.2s while p50 stays sane — a code-fault shape, not a
        # saturation one (the USE metrics on its DB stay clean, ruled out in INVESTIGATE).
        fact(SVC, "red_rate", 820, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
        fact(SVC, "red_latency_p50", 58, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
        fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
        fact(SVC, "slo_target", 0.999, T_ONSET, source=S.SERVICENOW),
        fact(ANOM, "onset_value", 0.40, T_ONSET, source=S.PROMETHEUS),
        fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
        event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
        event(ALERT, "fired", T_ONSET, source=S.PROMETHEUS),
        event(CHG, "implemented", T_CHANGE, source=S.SERVICENOW, change="deploy payments-api v4.12.0"),
        edge(ET.AFFECTS, ANOM, SVC),
        edge(ET.FIRED_ON, ALERT, SVC),
        edge(ET.CHANGED_BY, SVC, CHG),
        edge(ET.CORRELATED_WITH, ANOM, CHG, level="med"),
    ], "payments-api 5xx spiked to 40% at 14:00, 13m after the v4.12.0 deploy at 13:47.")
    # scope/impact framing (the retired TRIAGE's real content — P7 5-phase algebra)
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-4821",
             title="payments-api elevated 5xx errors",
             short_description="payments-api 5xx spiked to 40% ~13m after the v4.12.0 deploy",
             work_notes="High5xxRate paged SRE; v4.12.0 shipped just before onset.",
             caller_id="monitoring.alerting"),
        node(NT.DATABASE, db_id="payments-ora"),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.DEPENDS_ON, SVC, DB, origin="declared"),
        fact(SVC, "red_latency_p99", 4200, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
    ], "narrative": frame.narrative + " Declared SEV2. Still bleeding; the only dependency "
       "is payments-ora. Investigate, don't blind-mitigate."})

    # INVESTIGATE opens the hypothesize⇄evidence loop (verdict=repeat keeps looping)
    investigate_open = phase("investigate", [
        node(NT.CODE_COMMIT, sha="abc123"),
        edge(ET.INTRODUCED_BY, CHG, COMMIT),
        # related priors (ServiceNow list_related_incidents): billing-api + invoicing-api filed
        # the same NPE in the same window after adopting the shared taxcalc lib — a hypothesis
        # prior that sharpens H1. SIMILAR_TO off the primary incident (additive; H1 still wins).
        node(NT.INCIDENT, incident_id="INC-4788", severity="3 - Moderate"),
        node(NT.INCIDENT, incident_id="INC-4790", severity="4 - Low"),
        event(INC_R1, "declared", T_ONSET, source=S.SERVICENOW, affected_ci="billing-api"),
        event(INC_R2, "declared", T_ONSET, source=S.SERVICENOW, affected_ci="invoicing-api"),
        edge(ET.SIMILAR_TO, INC, INC_R1, level="high"),
        edge(ET.SIMILAR_TO, INC, INC_R2, level="med"),
        propose("h1", "v4.12.0 (commit abc123) introduced a NullPointerException in TaxCalculator",
                "med", root=COMMIT),
        propose("h2", "payments-ora connection-pool exhaustion", "low", root=DB),
    ], "Change-first: the deploy is the prime suspect (H1); the DB is a weaker alternative (H2). "
       "2 sibling services (billing-api, invoicing-api) filed the same NPE after adopting the "
       "shared taxcalc lib in v4.12.0 — a related prior reinforcing H1.",
       status="repeat")

    # the loop's confirm/refute turn: rule out the DB, confirm the code path.
    db_fact = fid(DB, "conn_pool_util", T_INV)
    err_fact = fid(ERRSIG, "count", T_INV)
    if not refuted_variant:
        investigate_confirm = phase("investigate", [
            # the full DB USE pull that rules payments-ora OUT: pool a quarter full, connections
            # well under the ceiling, replication current, no slow-query surge — a healthy store.
            fact(DB, "conn_pool_util", 0.28, T_INV, source=S.PROMETHEUS, reliability=0.99),
            fact(DB, "active_connections", 56, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.99),
            fact(DB, "max_connections", 200, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.99),
            fact(DB, "replication_lag", 0.2, T_INV, unit="s", source=S.PROMETHEUS, reliability=0.98),
            fact(DB, "slow_query_rate", 3, T_INV, unit="per_min", source=S.PROMETHEUS, reliability=0.98),
            node(NT.ERROR_SIGNATURE, signature_hash="npe-taxcalc",
                 exception_class="NullPointerException", first_seen=T_ONSET,
                 file_line="TaxCalculator.java:88"),
            fact(ERRSIG, "count", 152, T_INV, source=S.SPLUNK, reliability=0.98),
            fact(ERRSIG, "last_seen", T_INV.isoformat(), T_INV, source=S.SPLUNK, reliability=0.98),
            edge(ET.EMITTED, SVC, ERRSIG),
            edge(ET.CAUSED_BY, H1, COMMIT, level="high"),
            update("h2", status="refuted", add_refuting=[db_fact],
                   basis="pool util 28%, 56/200 connections, replication current — DB healthy, "
                   "not the cause"),
            update("h1", status="supported", level="high", add_supporting=[err_fact],
                   basis="NPE stack traces to TaxCalculator.java:88, only present in v4.12.0; "
                   "152 occurrences since onset"),
        ], "Ruled out the DB (pool 28%, 56/200 conns, no slow-query surge). Traces pin an NPE "
           "in TaxCalculator introduced by abc123.")
    else:
        # the variant: the loop's evidence turn refutes H1 (NPE predates the deploy) ->
        # backtrack RE-ENTERS investigate (the hypothesize⇄evidence loop starts over)
        investigate_confirm = phase("investigate", [
            fact(DB, "conn_pool_util", 0.28, T_INV, source=S.PROMETHEUS, reliability=0.99),
            node(NT.ERROR_SIGNATURE, signature_hash="npe-taxcalc"),
            fact(ERRSIG, "count", 152, T_INV, source=S.SPLUNK, reliability=0.98),
            update("h1", status="refuted", add_refuting=[err_fact],
                   basis="the NPE signature predates v4.12.0 — deploy is not the cause"),
        ], "The NPE predates the deploy — H1 refuted. Backtrack: re-enter the loop and "
           "re-hypothesize.",
            status="backtrack")

    act = phase("act", [
        update("h1", level="high", basis="proposed fix: roll payments-api back to v4.11.3 (revert abc123)"),
    ], "Safest reversible fix: roll back to v4.11.3. Awaiting approval (gated).")

    verify = phase("verify", [
        fact(SVC, "red_errors", 0.01, T_FIX, source=S.PROMETHEUS, reliability=0.98),
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        update("h1", status="confirmed", level="high",
               basis="rollback cleared 5xx to 1% — recovery confirms the causal chain"),
    ], "Post-rollback: 5xx back to 1%, anomaly cleared. Root cause confirmed.")

    close = phase("close", [], "Postmortem: v4.12.0 (abc123) NPE in TaxCalculator → 5xx; "
                  "rollback to v4.11.3 resolved it. DB ruled out.", status="done")

    if refuted_variant:
        # after backtrack the SAME investigate phase re-enters: the loop re-anchors on the
        # surviving rival (a fresh hypothesize turn), then the run ends blocked (shortened path)
        reanchor = phase("investigate", [
            update("h2", level="high", basis="with the deploy exonerated, the pool is the leading candidate"),
        ], "Deploy exonerated; DB pool is now the leading hypothesis.", status="blocked")
        return subject, [frame, investigate_open, investigate_confirm, reanchor]

    return subject, [frame, investigate_open, investigate_confirm, act, verify, close]
