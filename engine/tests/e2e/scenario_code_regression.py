"""Scenario 1 — application CODE regression (DESIGN §2.5 R-K3 layer 1).

payments-api throws 5xx after the v4.12.0 deploy. Differential diagnosis rules OUT the
database (pool healthy) and confirms a NullPointerException introduced by commit abc123,
traced via error signature → deploy → commit. Discriminator: pods Ready but throwing.
The scripted planner drives the real engine through the 5-phase algebra (6 steps — the
investigate loop runs twice: hypothesize⇄evidence).

FLAGSHIP STORY FIDELITY (JOURNAL story fidelity, 2026-07-23): every phase is authored as a
sequence of REASONED STEPS — one to-do per step = an objective + ONE capability call carrying
its per-call `rationale` (the WHY) + the ops that step produced + a human `observation` (what
came back). The graph ops are IDENTICAL to the pre-story scenario (same nodes/facts/edges/
hypotheses) — they are only RE-ORGANIZED under the reasoned to-dos, so the journal now records
the investigation tool-by-tool (called X because … → produced these facts → observed …). The
calls fold to zero mock ops (no fixtures wired), so the twin's AUTHORED ops remain the whole
graph delta and the root/outcome invariant holds trivially: root = code_commit:abc123, close =
resolved. ACT keeps its plain hypothesis-update op (the write is injected by the session gate),
and CLOSE is a narrative-only postmortem.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, span, todo, update


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

    # ── FRAME: pull the incident, quantify the blast radius, map the topology, find the change,
    #    capture the error shape — each a reasoned tool call (scope/impact framing folded in). ──
    frame = phase("frame", todos=[
        todo("pull the incident record to scope impact, tier and SLO",
             calls=[call("get_incident",
                         "start from the incident of record — who paged, what tier is at risk, "
                         "what SLO it burns", incident_id="INC-4821")],
             ops=[
                 node(NT.INCIDENT, incident_id="INC-4821",
                      title="payments-api elevated 5xx errors",
                      short_description="payments-api 5xx spiked to 40% ~13m after the v4.12.0 deploy",
                      description="PagerDuty routed High5xxRate to SRE at 14:00 UTC. payments-api (prod, "
                                  "tier-1) is 5xx-ing on ~40% of requests; throughput holds but the error "
                                  "tail drags p99 to 4.2s while p50 stays flat — a code-fault shape. Onset "
                                  "is 13 minutes after release v4.12.0 (13:47). Card auth + capture impacted.",
                      work_notes="High5xxRate paged SRE; v4.12.0 shipped just before onset.",
                      caller_id="monitoring.alerting"),
                 fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
                 fact(SVC, "slo_target", 0.999, T_ONSET, source=S.SERVICENOW),
                 edge(ET.AFFECTS, INC, SVC),
                 event(INC, "declared", T_ONSET, source=S.SERVICENOW),
             ],
             observation="INC-4821: payments-api (prod, tier-1, SLO 99.9%) declared SEV2 at 14:00 — "
                         "card auth + capture impacted, onset ~13m after v4.12.0."),
        todo("quantify the blast radius — how much is 5xx-ing, and what is the latency shape",
             calls=[call("range_query",
                         "measure the RED signals over the onset window — error ratio, throughput "
                         "and the p50/p99 latency spread", service_name="payments-api", window="15m")],
             ops=[
                 node(NT.ANOMALY, anomaly_id="ANOM-1"),
                 node(NT.ALERT, alert_id="ALT-1"),
                 fact(SVC, "red_errors", 0.40, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
                 fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
                 # the full onset RED snapshot: throughput holding but 40% of calls 5xx-ing, and the
                 # error tail dragging p99 to 4.2s while p50 stays sane — a code-fault shape, not a
                 # saturation one (the USE metrics on its DB stay clean, ruled out in INVESTIGATE).
                 fact(SVC, "red_rate", 820, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
                 fact(SVC, "red_latency_p50", 58, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
                 fact(SVC, "red_latency_p99", 4200, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
                 fact(ANOM, "onset_value", 0.40, T_ONSET, source=S.PROMETHEUS),
                 fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
                 event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
                 event(ALERT, "fired", T_ONSET, source=S.PROMETHEUS),
                 edge(ET.AFFECTS, ANOM, SVC),
                 edge(ET.FIRED_ON, ALERT, SVC),
             ],
             observation="40% of ~820 rpm are 5xx; p50 holds at 58ms but the error tail drags p99 to "
                         "4.2s — a code-fault shape, not saturation."),
        todo("map what payments-api depends on",
             calls=[call("get_dependencies",
                         "pull the declared topology — a failing dependency is the alternative to a "
                         "code fault", service_name="payments-api")],
             ops=[
                 node(NT.SERVICE, service_name="payments-api", env="prod",
                      owner="payments-platform@corp.example", version="v4.12.0"),
                 node(NT.DATABASE, db_id="payments-ora", engine="oracle", version="19c",
                      owner="payments-platform@corp.example"),
                 edge(ET.DEPENDS_ON, SVC, DB, origin="declared"),
             ],
             observation="payments-api's only declared dependency is payments-ora (Oracle 19c)."),
        todo("find the change that lines up with onset",
             calls=[call("find_recent_changes",
                         "change-first triage — a deploy minutes before onset is the prime suspect",
                         service_name="payments-api", window="2h")],
             ops=[
                 node(NT.CHANGE_EVENT, change_id="CHG-1",
                      short_description="Deploy payments-api v4.12.0 to prod (intl tax-calc)",
                      description="Standard release of payments-api v4.12.0 via Argo Rollouts; bumps the "
                                  "shared taxcalc library to add intl VAT regions. Blue/green with a 10% "
                                  "canary, auto-promoted after the analysis gate."),
                 event(CHG, "implemented", T_CHANGE, source=S.SERVICENOW, change="deploy payments-api v4.12.0"),
                 edge(ET.CHANGED_BY, SVC, CHG),
                 edge(ET.CORRELATED_WITH, ANOM, CHG, level="med"),
             ],
             observation="v4.12.0 (CHG-1) deployed at 13:47 — 13 minutes before onset; it bumps the "
                         "shared taxcalc library."),
        todo("capture the error shape at onset",
             calls=[call("fetch_traces",
                         "pull a trace from the onset window to see how the request actually fails",
                         service_name="payments-api", window="5m")],
             # a captured distributed trace at onset — the SPAN species (§2.6): a bounded happening SVC is in
             ops=[
                 span(SVC, "trace", T_ONSET, ended_at=T_ONSET + timedelta(milliseconds=920),
                      correlation_id="trace-payments-a17e", value={"error": True}, reliability=0.95),
             ],
             observation="a captured onset trace errored after ~920ms against payments-api."),
    ], narrative="payments-api 5xx spiked to 40% at 14:00, 13m after the v4.12.0 deploy at 13:47. "
       "Declared SEV2. Still bleeding; the only dependency is payments-ora. Investigate, don't "
       "blind-mitigate.")

    # INVESTIGATE opens the hypothesize⇄evidence loop (verdict=repeat keeps looping): read the
    # commit, check for co-firing siblings, then frame the rival hypotheses.
    investigate_open = phase("investigate", todos=[
        todo("identify exactly what code shipped in the change",
             calls=[call("get_commit",
                         "read the commit behind CHG-1 — what did the deploy actually change",
                         repo="payments-api", sha="abc123")],
             ops=[
                 node(NT.CODE_COMMIT, sha="abc123", repo="payments-api", author="dev-kco",
                      message="feat(tax): add intl VAT regions via shared taxcalc v4.12.0 (PR #1487)"),
                 edge(ET.INTRODUCED_BY, CHG, COMMIT),
             ],
             observation="abc123 (PR #1487) adds intl VAT regions via the shared taxcalc library — "
                         "the code v4.12.0 shipped."),
        # related priors (ServiceNow list_related_incidents): billing-api + invoicing-api filed
        # the same NPE in the same window after adopting the shared taxcalc lib — a hypothesis
        # prior that sharpens H1. SIMILAR_TO off the primary incident (additive; H1 still wins).
        todo("check whether any sibling service hit the same failure",
             calls=[call("list_related_incidents",
                         "co-firing incidents on the same shared library would sharpen the "
                         "code-fault hypothesis", incident_id="INC-4821", window="1h")],
             ops=[
                 node(NT.INCIDENT, incident_id="INC-4788", severity="3 - Moderate",
                      title="billing-api 5xx after v4.12.0",
                      short_description="billing-api NPE in TaxCalculator after adopting shared taxcalc"),
                 node(NT.INCIDENT, incident_id="INC-4790", severity="4 - Low",
                      title="invoicing-api intermittent 5xx",
                      short_description="invoicing-api same TaxCalculator NPE, lower volume"),
                 event(INC_R1, "declared", T_ONSET, source=S.SERVICENOW, affected_ci="billing-api"),
                 event(INC_R2, "declared", T_ONSET, source=S.SERVICENOW, affected_ci="invoicing-api"),
                 edge(ET.SIMILAR_TO, INC, INC_R1, level="high"),
                 edge(ET.SIMILAR_TO, INC, INC_R2, level="med"),
             ],
             observation="billing-api and invoicing-api both filed the same TaxCalculator NPE after "
                         "adopting the shared taxcalc lib in v4.12.0."),
        todo("frame the rival hypotheses to test",
             ops=[
                 propose("h1", "v4.12.0 (commit abc123) introduced a NullPointerException in TaxCalculator",
                         "med", root=COMMIT),
                 propose("h2", "payments-ora connection-pool exhaustion", "low", root=DB),
             ],
             observation="change-first: the deploy (abc123) is the prime suspect (H1); payments-ora "
                         "pool exhaustion is the weaker alternative (H2)."),
    ], narrative="Change-first: the deploy is the prime suspect (H1); the DB is a weaker alternative "
       "(H2). 2 sibling services (billing-api, invoicing-api) filed the same NPE after adopting the "
       "shared taxcalc lib in v4.12.0 — a related prior reinforcing H1.",
       status="repeat")

    # the loop's confirm/refute turn: rule out the DB, confirm the code path.
    db_fact = fid(DB, "conn_pool_util", T_INV)
    err_fact = fid(ERRSIG, "count", T_INV)
    if not refuted_variant:
        investigate_confirm = phase("investigate", todos=[
            todo("rule the database in or out — is payments-ora saturated",
                 calls=[call("fetch_metrics",
                             "pull payments-ora's USE metrics — pool, connections, replication and "
                             "slow queries", service_name="payments-ora", window="15m")],
                 # the full DB USE pull that rules payments-ora OUT: pool a quarter full, connections
                 # well under the ceiling, replication current, no slow-query surge — a healthy store.
                 ops=[
                     fact(DB, "conn_pool_util", 0.28, T_INV, source=S.PROMETHEUS, reliability=0.99),
                     fact(DB, "active_connections", 56, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.99),
                     fact(DB, "max_connections", 200, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.99),
                     fact(DB, "replication_lag", 0.2, T_INV, unit="s", source=S.PROMETHEUS, reliability=0.98),
                     fact(DB, "slow_query_rate", 3, T_INV, unit="per_min", source=S.PROMETHEUS, reliability=0.98),
                 ],
                 observation="payments-ora is healthy on every axis: pool 28% (56/200 conns), "
                             "replication 0.2s, only 3 slow queries/min."),
            todo("refute the database hypothesis on the evidence",
                 ops=[
                     update("h2", status="refuted", add_refuting=[db_fact],
                            basis="pool util 28%, 56/200 connections, replication current — DB healthy, "
                            "not the cause"),
                 ],
                 observation="H2 refuted: a store this healthy cannot be driving a 40% 5xx rate."),
            todo("check whether the stack trace pins the code path",
                 calls=[call("error_signature_topk",
                             "get the top error signature since onset — does the stack trace land in "
                             "the changed code", service_name="payments-api", window="15m")],
                 ops=[
                     node(NT.ERROR_SIGNATURE, signature_hash="npe-taxcalc",
                          exception_class="NullPointerException", first_seen=T_ONSET,
                          file_line="TaxCalculator.java:88",
                          message="Cannot invoke \"com.corp.geo.Region.getCode()\" because the return value "
                                  "of \"com.corp.order.Order.getRegion()\" is null"),
                     fact(ERRSIG, "count", 152, T_INV, source=S.SPLUNK, reliability=0.98),
                     fact(ERRSIG, "last_seen", T_INV.isoformat(), T_INV, source=S.SPLUNK, reliability=0.98),
                     edge(ET.EMITTED, SVC, ERRSIG),
                 ],
                 observation="152 NPEs at TaxCalculator.java:88 since onset — the null Region path the "
                             "taxcalc change introduced."),
            todo("support the code-regression hypothesis and pin its root",
                 ops=[
                     edge(ET.CAUSED_BY, H1, COMMIT, level="high"),
                     update("h1", status="supported", level="high", add_supporting=[err_fact],
                            basis="NPE stack traces to TaxCalculator.java:88, only present in v4.12.0; "
                            "152 occurrences since onset"),
                 ],
                 observation="H1 supported: the NPE traces straight to the taxcalc change in abc123."),
        ], narrative="Ruled out the DB (pool 28%, 56/200 conns, no slow-query surge). Traces pin an "
           "NPE in TaxCalculator introduced by abc123.")
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

    # ACT stays a plain hypothesis-update op (NOT a reasoned tool call): the session write-gate
    # injects the matching apply_remediation WRITE onto this phase's calls, so keeping it ops-style
    # lets that injection append cleanly (as it does for every other scenario's act).
    act = phase("act", [
        update("h1", level="high", basis="proposed fix: roll payments-api back to v4.11.3 (revert abc123)"),
    ], "Safest reversible fix: roll back to v4.11.3. Awaiting approval (gated).")

    verify = phase("verify", todos=[
        todo("re-check the RED signals after the rollback",
             calls=[call("range_query",
                         "re-measure error ratio and degraded state post-rollback — did the symptom "
                         "actually clear", service_name="payments-api", window="15m")],
             ops=[
                 fact(SVC, "red_errors", 0.01, T_FIX, source=S.PROMETHEUS, reliability=0.98),
                 fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
                 event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
                 event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
             ],
             observation="post-rollback: 5xx back to 1%, degraded false, anomaly cleared."),
        todo("confirm the root cause on the recovery",
             ops=[
                 update("h1", status="confirmed", level="high",
                        basis="rollback cleared 5xx to 1% — recovery confirms the causal chain"),
             ],
             observation="recovery confirms the causal chain — reverting abc123 cleared the symptom."),
    ], narrative="Post-rollback: 5xx back to 1%, anomaly cleared. Root cause confirmed.")

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
