"""Scenario 2 — bad DEPLOYMENT (DESIGN §2.5 R-K3 layer 2).

checkout-api's rev43 deploy removes the required ConfigMap key `DB_HOST`. The container
panics on boot, so the pod never reaches Ready (CrashLoopBackOff) and the rollout stalls
(ProgressDeadlineExceeded). Differential diagnosis rules OUT checkout-db (pool healthy,
and the panic happens before any DB connection is ever attempted) and confirms the
ConfigMap-key removal, traced blame -> PR #482 -> commit 9f2a1e0. Discriminator: the pod
NEVER reaches Ready — a downstream-dependency rival would still flap Ready intermittently.
The scripted planner drives the real engine through the 5-phase algebra (6 steps — the
investigate loop runs twice: hypothesize⇄evidence).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


T_DEPLOY = _t(0)      # 09:00 rev43 rollout begins
T_ONSET = _t(4)       # 09:04 CrashLoopBackOff / alert fires
T_TRIAGE = _t(6)      # 09:06 still bleeding, rollout stuck
T_INV = _t(18)        # 09:18 investigation — blame + diff + DB check
T_FIX = _t(40)        # 09:40 rollback to rev42 + recovery confirmed

SVC = nid(NT.SERVICE, service_name="checkout-api", env="prod")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-DEP-99")
DB = nid(NT.DATABASE, db_id="checkout-db")
DEP = nid(NT.DEPLOYMENT, uid="dep-checkout-api-7f9d8")
POD = nid(NT.POD, uid="pod-checkout-api-7f9d8-x1")
HOST = nid(NT.HOST, fqdn="k8s-node-17.prod.internal")
RELEASE = nid(NT.RELEASE, release_id="checkout-api-rev43")
COMMIT = nid(NT.CODE_COMMIT, sha="9f2a1e0")
PR = nid(NT.PULL_REQUEST, repo="checkout-api", pr_id="482")
ERRSIG = nid(NT.ERROR_SIGNATURE, signature_hash="cfg-missing-dbhost")
INC = nid(NT.INCIDENT, incident_id="INC-7731")
H1, H2 = hid("h1"), hid("h2")


def build(mitigated: bool = False):
    """Returns (subject, script, fixtures). `mitigated=True` skips the CONFIRMED status on
    the leading hypothesis at VERIFY — impact stops (rollback works) but the root cause is
    never independently confirmed, so the engine must close MITIGATED, not RESOLVED."""
    subject = SubjectRef(domain="app-incident", id="INC-7731", kind="incident")

    # ── FRAME: topology + the deploy change + the firing alert (all via capability calls) ──
    fixtures: dict = {}

    fixtures["get_dependencies"] = {
        "env": "prod",
        "dependencies": [
            {"parent": "checkout-api", "parent_type": "cmdb_ci_service",
             "child": "checkout-db", "child_type": "cmdb_ci_database",
             "rel_type": "Depends on::Used by"},
        ],
    }
    fixtures["find_recent_changes"] = {
        "changes": [
            {"number": "CHG-DEP-99", "type": "deployment",
             "cmdb_ci": {"display_value": "checkout-api"}, "requested_by": "svc-deploy-bot",
             "start_date": T_DEPLOY, "env": "prod",
             "u_release_tag": "checkout-api-rev43", "u_commit_sha": "9f2a1e0"},
        ],
    }
    fixtures["active_alerts"] = {
        "service": {"name": "checkout-api", "env": "prod"},
        "alerts": [{"id": "ALT-1", "alertname": "KubePodCrashLooping", "at": T_ONSET, "state": "firing"}],
    }

    frame = phase("frame",
        [
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 0.0, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            fact(ANOM, "severity_score", 3, T_ONSET, source=S.SERVICENOW),
            # onset RED for checkout-api: availability collapsed (0 ready replicas), so
            # near-100% of the trickle of requests 5xx and throughput has cratered.
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_errors", 1.0, T_ONSET, source=S.PROMETHEUS, reliability=0.98),
            fact(SVC, "red_rate", 38, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_latency_p99", 250, T_ONSET, unit="ms", source=S.APPD, reliability=0.9),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 0.999, T_ONSET, source=S.SERVICENOW),
            edge(ET.AFFECTS, ANOM, SVC),
        ],
        "checkout-api's rev43 rollout (09:00) never reached available replicas; pods "
        "crash-looping by 09:04. checkout-db is the only declared dependency.",
        calls=[call("get_dependencies"), call("find_recent_changes"), call("active_alerts")],
    )

    # ── scope/impact framing folded into FRAME (the retired TRIAGE's real content — P7
    # 5-phase algebra): rollout stuck, pod never Ready (the discriminator), healthy host ──
    fixtures["rollout_status"] = {
        "deployment": {"uid": "dep-checkout-api-7f9d8", "name": "checkout-api",
                       "namespace": "checkout-prod", "at": T_TRIAGE,
                       "image": "registry/checkout-api:rev43", "available_replicas": 0,
                       "desired_replicas": 3, "rollout_progress": 0},
        "release": {"release_id": "checkout-api-rev43", "version": "43", "at": T_DEPLOY},
    }
    fixtures["pod_status"] = {
        "pods": [
            {"uid": "pod-checkout-api-7f9d8-x1", "name": "checkout-api-7f9d8-x1",
             "namespace": "checkout-prod", "at": T_TRIAGE, "phase": "CrashLoopBackOff",
             "ready": False, "node_name": "k8s-node-17.prod.internal", "restart_count": 14,
             # the container dies in its boot/config phase before it ever serves traffic, so
             # its own resource draw is negligible — corroborates "panics on boot", not "OOM".
             "cpu_utilization": 0.02, "mem_utilization": 0.05},
        ],
    }
    fixtures["events"] = {
        "events": [
            {"involved_object": {"kind": "Deployment", "uid": "dep-checkout-api-7f9d8",
                                  "name": "checkout-api", "namespace": "checkout-prod"},
             "reason": "ProgressDeadlineExceeded", "at": T_TRIAGE,
             "message": "Deployment does not have minimum availability."},
            {"involved_object": {"kind": "Pod", "uid": "pod-checkout-api-7f9d8-x1",
                                  "name": "checkout-api-7f9d8-x1", "namespace": "checkout-prod"},
             "reason": "BackOff", "at": T_TRIAGE,
             "message": "Back-off restarting failed container"},
        ],
    }

    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-7731",
             title="checkout-api pods CrashLoopBackOff",
             short_description="checkout-api rev43 rollout stuck 0/3 ready; pods crash-loop",
             work_notes="KubePodCrashLooping; ProgressDeadlineExceeded. Suspect rev43.",
             caller_id="svc-deploy-bot"),
        edge(ET.AFFECTS, INC, SVC),
        event(INC, "declared", T_TRIAGE, source=S.SERVICENOW),
        # the host the crashing pod is scheduled onto is itself healthy — USE metrics all
        # nominal, no saturation — so the fault is the workload, not the node (rules out a
        # node-pressure explanation before it is even hypothesised).
        fact(HOST, "cpu_utilization", 0.31, T_TRIAGE, source=S.PROMETHEUS, reliability=0.98),
        fact(HOST, "mem_utilization", 0.52, T_TRIAGE, source=S.PROMETHEUS, reliability=0.98),
        fact(HOST, "disk_utilization", 0.44, T_TRIAGE, source=S.PROMETHEUS, reliability=0.98),
        fact(HOST, "net_utilization", 0.18, T_TRIAGE, source=S.PROMETHEUS, reliability=0.98),
        fact(HOST, "cpu_saturation", 0.0, T_TRIAGE, source=S.PROMETHEUS, reliability=0.98),
        fact(HOST, "disk_saturation", 0.0, T_TRIAGE, source=S.PROMETHEUS, reliability=0.98),
    ], "calls": [*frame.calls, call("rollout_status"), call("pod_status"), call("events")],
       "narrative": frame.narrative + " Declared SEV2. Rollout is stuck "
       "(ProgressDeadlineExceeded->rollback) and the pod is CrashLoopBackOff — it has never "
       "reached Ready; its host k8s-node-17 is healthy (CPU 31%, no saturation). "
       "Investigate before mitigating."})

    # ── INVESTIGATE opens the loop: change-first (H1) vs the declared dependency (H2) ──
    investigate_open = phase("investigate", [
        propose("h1", "rev43 (PR #482, commit 9f2a1e0) removed the required ConfigMap key "
                "DB_HOST; the container panics on boot, so the pod never reaches Ready",
                "med", root=COMMIT),
        propose("h2", "checkout-db is overloaded/unreachable, causing the readiness probe "
                "to fail", "low", root=DB),
    ], "Change-first: the rev43 deploy is the prime suspect (H1); the declared DB "
       "dependency is a weaker alternative (H2).", status="repeat")

    # ── the loop's confirm turn: blame + diff pin the commit; DB metrics rule out H2 ────
    fixtures["blame"] = {
        "blame": {"sha": "9f2a1e0", "repo": "checkout-api", "file": "config/loader.go",
                  "line": 57, "snippet": 'cfg.MustGet("DB_HOST") // panics if key missing'},
        "error_signature_hash": "cfg-missing-dbhost",
        "error_signature": {"exception_class": "ConfigMissingError", "first_seen": T_ONSET},
        "hypothesis_id": "h1",
    }
    fixtures["diff_range"] = {
        "commit": {"sha": "9f2a1e0", "repo": "checkout-api", "author": "jdoe",
                   "parent_sha": "7a1c220", "authored_at": T_DEPLOY},
        "pr": {"pr_id": "482", "repo": "checkout-api", "author": "jdoe",
               "merged_sha": "9f2a1e0", "event": "merged", "at": T_DEPLOY},
        "diff": {"at": T_INV, "files_changed": 1, "lines_added": 0, "lines_deleted": 3,
                 "reliability": 0.98},
    }
    fixtures["fetch_metrics"] = {
        # checkout-db's full USE pull — healthy on every axis (the pod never even opened a
        # connection), so the declared DB dependency (H2) is ruled out on the evidence.
        "metrics": [
            {"subject": DB, "predicate": "conn_pool_util", "value": 0.24, "at": T_INV, "reliability": 0.99},
            {"subject": DB, "predicate": "active_connections", "value": 41, "unit": "conn",
             "at": T_INV, "reliability": 0.99},
            {"subject": DB, "predicate": "max_connections", "value": 200, "unit": "conn",
             "at": T_INV, "reliability": 0.99},
            {"subject": DB, "predicate": "slow_query_rate", "value": 1, "unit": "per_min",
             "at": T_INV, "reliability": 0.98},
            {"subject": DB, "predicate": "replication_lag", "value": 0.1, "unit": "s",
             "at": T_INV, "reliability": 0.98},
        ],
    }

    db_fact = fid(DB, "conn_pool_util", T_INV)
    diff_fact = fid(COMMIT, "lines_deleted", T_INV)

    investigate_confirm = phase("investigate", [
        edge(ET.EMITTED, POD, ERRSIG),
        update("h2", status="refuted", add_refuting=[db_fact],
               basis="pool util 24% — checkout-db is healthy, and the pod panics on boot "
               "before any DB connection is ever attempted"),
        update("h1", status="supported", level="high", add_supporting=[diff_fact],
               basis="blame pins the panic to config/loader.go:57 in commit 9f2a1e0; the "
               "diff shows PR #482 deleted the 3 lines reading DB_HOST from the ConfigMap"),
    ], "Ruled out checkout-db (pool 24%, healthy). Blame + diff pin the panic to PR #482's "
       "removal of DB_HOST from the ConfigMap in commit 9f2a1e0.",
        calls=[call("blame"), call("diff_range"), call("fetch_metrics")])

    # ── ACT: safest reversible fix — roll back the Deployment (human-gated) ────
    act = phase("act", [
        update("h1", level="high",
               basis="proposed fix: roll checkout-api back from rev43 to rev42 (revert "
               "PR #482 / commit 9f2a1e0), restoring the DB_HOST ConfigMap key"),
    ], "Safest reversible fix: roll back the Deployment to the prior release rev42. "
       "Awaiting approval (gated).")

    # ── VERIFY: did the rollback clear the symptom? ─────────────────────────────
    verify_ops = [
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        fact(DEP, "available_replicas", 3, T_FIX, source=S.OCP, reliability=0.99),
        fact(POD, "phase", "Running", T_FIX, source=S.OCP, reliability=0.99),
        fact(POD, "ready", True, T_FIX, source=S.OCP, reliability=0.99),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
    ]
    if mitigated:
        verify_ops.append(update("h1", level="high",
            basis="impact stopped after the rollback; root cause is the leading, "
            "well-evidenced explanation but independent confirmation is deferred pending "
            "platform-team sign-off on the ConfigMap fix"))
        verify_narrative = ("Post-rollback: pod reached Ready, rollout complete, 5xx/degraded "
                            "cleared. Impact mitigated; root-cause confirmation still pending.")
    else:
        verify_ops.append(update("h1", status="confirmed", level="high",
            basis="rollback to rev42 restored the ConfigMap key; the pod reached Ready with "
            "no further restarts and the rollout completed — confirms the causal chain"))
        verify_narrative = ("Post-rollback: pod reached Ready, rollout complete, degraded "
                            "cleared. Root cause confirmed.")

    verify = phase("verify", verify_ops, verify_narrative)

    if mitigated:
        close = phase("close", [],
            "Postmortem: rollback to rev42 stopped the CrashLoopBackOff and restored "
            "availability. Root cause (rev43's ConfigMap key removal) is the leading, "
            "well-evidenced explanation but was not independently confirmed at close.",
            status="done")
    else:
        close = phase("close", [],
            "Postmortem: rev43 (PR #482, commit 9f2a1e0) removed the ConfigMap key "
            "DB_HOST; checkout-api panicked on boot -> CrashLoopBackOff, rollout stuck. "
            "Rollback to rev42 resolved it. checkout-db ruled out.", status="done")

    return subject, [frame, investigate_open, investigate_confirm, act, verify, close], fixtures
