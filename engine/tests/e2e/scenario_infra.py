"""Scenario 8 — INFRA / noisy-neighbor root cause (DESIGN §2.5 R-K3 layer: infra; obs 11).

checkout-svc's tier-1 pod is evicted mid-shift. The node it runs on (node-prod-17) is under
memory pressure — an unbounded `etl-nightly` batch job co-scheduled on the same node ballooned
its heap and starved the box, so the kubelet evicted the lowest-priority tenant. Differential
diagnosis rules OUT an application memory leak in checkout-svc (the pod's OWN memory was
moderate right up to eviction; the pressure is node-level) and confirms the noisy-neighbor batch
job as root cause. Discriminator: HOST mem_utilization is pegged while the victim POD's own
mem_utilization is normal — a platform (co-tenancy) fault, not the app leaking. The scripted
planner drives the REAL engine through all 7 phases.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 2, 0, tzinfo=UTC) + timedelta(minutes=minutes)


T_START = _t(0)      # 02:00 etl-nightly batch job starts, heap grows unbounded
T_ONSET = _t(18)     # 02:18 checkout pod evicted, ALT-9 fires
T_INV = _t(30)       # 02:30 investigation: host mem pegged, pod's own mem normal
T_FIX = _t(55)       # 02:55 batch job rescheduled off the node, pod stable

SVC = nid(NT.SERVICE, service_name="checkout-svc", env="prod")
POD = nid(NT.POD, uid="checkout-7c9f-abc12")
HOST = nid(NT.HOST, fqdn="node-prod-17")
BATCH = nid(NT.BATCH_JOB, job_name="etl-nightly", schedule_id="sched-3")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-9")
ALERT = nid(NT.ALERT, alert_id="ALT-9")
INC = nid(NT.INCIDENT, incident_id="INC-8900")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) for the INFRA noisy-neighbor scenario."""
    subject = SubjectRef(domain="app-incident", id="INC-8900", kind="incident")

    frame = phase("frame",
        calls=[call("active_alerts"), call("pod_status")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-9"),
            fact(ANOM, "onset_value", 1, T_ONSET, unit="evictions", source=S.OCP),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            node(NT.SERVICE, service_name="checkout-svc", env="prod"),
            node(NT.POD, uid="checkout-7c9f-abc12"),
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_errors", 0.09, T_ONSET, source=S.PROMETHEUS, reliability=0.96),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(POD, "phase", "Failed", T_ONSET, source=S.OCP),
            fact(POD, "ready", False, T_ONSET, source=S.OCP),
            fact(POD, "node_name", "node-prod-17", T_ONSET, source=S.OCP),
            event(POD, "evicted", T_ONSET, source=S.OCP, reason="Evicted",
                  message="The node was low on resource: memory"),
            edge(ET.AFFECTS, ANOM, SVC),
        ],
        narrative="checkout-svc's tier-1 pod checkout-7c9f-abc12 was evicted at 02:18 with "
                  "reason 'node low on resource: memory' on node-prod-17. 5xx climbed as the "
                  "pod was rescheduled. Something on that node exhausted its memory.")

    triage = phase("triage", [
        node(NT.INCIDENT, incident_id="INC-8900"),
        node(NT.HOST, fqdn="node-prod-17"),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.RUNS_ON, POD, HOST, origin="declared"),
        fact(HOST, "mem_utilization", 0.97, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
    ], "Declared SEV2. The evicted pod ran on node-prod-17, whose memory is pegged at 97%. "
       "Find what on the node ate the memory before mitigating.")

    hypothesize = phase("hypothesize",
        calls=[call("find_recent_changes"), call("list_related_incidents")],
        ops=[
            node(NT.BATCH_JOB, job_name="etl-nightly", schedule_id="sched-3"),
            edge(ET.RUNS_ON, BATCH, HOST, origin="discovered"),
            fact(BATCH, "backlog_size", 8400000, T_ONSET, unit="rows", source=S.OCP, reliability=0.95),
            propose("h1", "the etl-nightly batch job (co-scheduled on node-prod-17) grew its "
                    "heap unbounded and starved the node's memory, so the kubelet evicted the "
                    "lowest-priority tenant — checkout-svc's pod", "med", root=BATCH),
            propose("h2", "checkout-svc has an application memory leak that OOM-pressured its "
                    "own pod off the node", "low", root=POD),
        ],
        narrative="Node-first: node-prod-17's memory is exhausted and an unbounded etl-nightly "
                  "batch job is co-scheduled there — prime suspect (H1, a noisy-neighbor "
                  "co-tenancy fault). A checkout-svc memory leak (H2) is the weaker alternative; "
                  "if it were the app, its OWN pod memory would be high — pending investigation.")

    host_mem_fact = fid(HOST, "mem_utilization", T_INV)
    pod_mem_fact = fid(POD, "mem_utilization", T_INV)
    batch_mem_fact = fid(BATCH, "last_duration", T_INV)

    investigate = phase("investigate",
        calls=[call("get_snapshots"), call("instant_query")],
        ops=[
            fact(HOST, "mem_utilization", 0.98, T_INV, source=S.PROMETHEUS, reliability=0.97),
            # the victim pod's OWN memory was moderate right up to eviction -> not an app leak.
            fact(POD, "mem_utilization", 0.55, T_INV, source=S.PROMETHEUS, reliability=0.96),
            # the batch job held the memory (its run overran ~4x its normal window).
            fact(BATCH, "last_duration", 4200, T_INV, unit="s", source=S.OCP, reliability=0.95),
            edge(ET.CAUSED_BY, H1, BATCH, level="high"),
            update("h2", status="refuted", add_refuting=[pod_mem_fact],
                   basis="checkout-svc's own pod memory was moderate (55%) right up to the "
                   "eviction — the pod was not leaking; the pressure was node-level, ruling "
                   "out an application memory leak"),
            update("h1", status="supported", level="high",
                   add_supporting=[host_mem_fact, batch_mem_fact],
                   basis="node-prod-17's memory is pegged at 98% and the etl-nightly job's run "
                   "overran to 4200s (≈4x normal), holding a huge working set — a classic "
                   "noisy-neighbor: the batch job starved the node and the kubelet evicted the "
                   "lowest-priority tenant"),
        ],
        narrative="The node's memory is exhausted (98%) but checkout-svc's OWN pod memory was "
                  "only 55% at eviction — this is not the app leaking. The etl-nightly batch "
                  "job overran ~4x its window on the same node, holding the memory. H2 (app "
                  "leak) refuted; H1 (noisy-neighbor batch job) confirmed at high confidence.")

    remediate = phase("remediate", [
        update("h1", level="high",
               basis="proposed fix: reschedule etl-nightly off the tier-1 node (node "
               "anti-affinity + a memory limit on the job) so it can never starve a tier-1 "
               "workload again; the evicted pod is already rescheduled elsewhere"),
    ], "Safest reversible fix: reschedule etl-nightly off the tier-1 node and cap its memory. "
       "Awaiting approval (gated).")

    verify = phase("verify", [
        fact(HOST, "mem_utilization", 0.61, T_FIX, source=S.PROMETHEUS, reliability=0.97),
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        event(POD, "started", T_FIX, source=S.OCP),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        update("h1", status="confirmed", level="high",
               basis="post-reschedule: node-prod-17 memory back to 61%, the checkout pod is "
               "Ready and stable, no further evictions — confirms the noisy-neighbor cause"),
    ], "Post-reschedule: node memory back to 61%; checkout pod Ready, no evictions. Root cause "
       "confirmed.")

    close = phase("close", [], "Postmortem: the unbounded etl-nightly batch job co-scheduled "
                  "on node-prod-17 grew its heap and starved the node's memory -> the kubelet "
                  "evicted the lowest-priority tenant (checkout-svc's tier-1 pod) -> checkout "
                  "5xx; rescheduling the batch job off the node with a memory cap resolved it. "
                  "An application memory leak was investigated and ruled out.",
                  status="done")

    script = [frame, triage, hypothesize, investigate, remediate, verify, close]

    fixtures = {
        "active_alerts": {
            "service": {"name": "checkout-svc", "env": "prod"},
            "alerts": [
                {"id": "ALT-9", "alertname": "PodEvicted", "at": T_ONSET, "state": "firing"},
            ],
        },
        "pod_status": {
            "pods": [
                {"uid": "checkout-7c9f-abc12", "phase": "Failed", "ready": False,
                 "node_name": "node-prod-17", "reason": "Evicted"},
            ],
        },
        "find_recent_changes": {"changes": []},
        "get_snapshots": {
            "service": {"name": "checkout-svc", "env": "prod"},
            "bt_metrics": [
                {"predicate": "red_errors", "value": 0.09, "at": T_INV, "reliability": 0.95},
            ],
        },
        "instant_query": {
            "metrics": [
                {"subject": HOST, "predicate": "mem_utilization", "value": 0.98,
                 "at": T_INV, "reliability": 0.97},
            ],
        },
        "list_related_incidents": {
            "primary_incident": "INC-8900",
            "related_incidents": [
                {"number": "INC-8901", "priority": "4 - Low", "opened_at": _t(19),
                 "cmdb_ci": "search-svc", "confidence": "med"},
            ],
        },
    }

    return subject, script, fixtures
