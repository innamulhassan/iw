"""Scenario 3 — NETWORK layer (DESIGN §2.5 R-K3 layer 3).

checkout-svc's calls to pricing-svc start timing out ~30m after an MTU/uplink change
(CHG-77) is applied to the network segment SEG-EDGE-12 that carries that traffic.
AppD shows checkout-svc's own BT ("CheckoutFlow") degraded, but a health-rule check on
pricing-svc comes back clean — the callee is healthy. Prometheus shows RetransSegs
spiking and probe_success flapping on the segment itself. Differential diagnosis rules
OUT pricing-svc's own database (pool healthy) and confirms the network change as root
cause, discriminated by retransmits-at-the-boundary + a healthy callee (not an
app/DB-side symptom). Reverting the MTU change resolves it. The scripted planner drives
the real engine through the 5-phase algebra (6 steps — the investigate loop runs twice),
with appd/prometheus/servicenow capability calls
resolved against mocked fixtures.
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
    span,
    update,
)


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 13, 10, tzinfo=UTC) + timedelta(minutes=minutes)


T_CHANGE = _t(0)     # 13:10 MTU/uplink change on SEG-EDGE-12
T_ONSET = _t(30)     # 13:40 retransmits climb / probe_success flaps / checkout-svc degrades
T_INV = _t(45)       # 13:55 investigation
T_FIX = _t(70)       # 14:20 revert + recovery

SVC = nid(NT.SERVICE, service_name="checkout-svc", env="prod")           # caller (anomaly's target)
SVC_CALLEE = nid(NT.SERVICE, service_name="pricing-svc", env="prod")     # callee (discovered via flowmap)
NETSEG = nid(NT.NETWORK_SEGMENT, segment_id="SEG-EDGE-12")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-77")
INC = nid(NT.INCIDENT, incident_id="INC-9001")
DB = nid(NT.DATABASE, db_id="pricing-db")
BT = nid(NT.BUSINESS_TRANSACTION, service_name="checkout-svc", bt_name="CheckoutFlow")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) — the real engine drives `script` through all 7
    phases, resolving `calls` against `fixtures` via the real appd/prometheus/servicenow
    adapters (default_adapters()), while the Anomaly/hypotheses are planner-direct ops."""
    subject = SubjectRef(domain="app-incident", id="INC-9001", kind="incident")

    frame = phase("frame",
        calls=[call("active_alerts"), call("find_recent_changes")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 0.58, T_ONSET, source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            # checkout-svc's onset RED: timeouts on the pricing-svc path drag p99 to ~4.8s and
            # push the error rate up, while throughput holds — a downstream-latency shape.
            fact(SVC, "red_rate", 960, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_errors", 0.12, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_latency_p50", 210, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "red_latency_p99", 4800, T_ONSET, unit="ms", source=S.APPD, reliability=0.94),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 500, T_ONSET, unit="ms", source=S.SERVICENOW),
            node(NT.NETWORK_SEGMENT, segment_id="SEG-EDGE-12", cidr="10.20.4.0/24", vlan=204,
                 mtu=1400, device="edge-agg-2.dc1", interface="Ethernet1/14",
                 managed_by="netops-team", owner="netops@corp.example"),
            # a captured distributed trace at onset — the SPAN species (§2.6): a bounded happening SVC is in
            span(SVC, "trace", T_ONSET, ended_at=T_ONSET + timedelta(milliseconds=4800),
                 correlation_id="trace-checkout-7fee", value={"error": True}, reliability=0.9),
            edge(ET.AFFECTS, ANOM, SVC),
            edge(ET.CONNECTS_TO, SVC, NETSEG),
            edge(ET.CHANGED_BY, NETSEG, CHG),
            edge(ET.CORRELATED_WITH, ANOM, CHG, level="med"),
        ],
        narrative="checkout-svc probe_success degraded and retransmits climbed from 13:40, "
                  "~30m after a network change (CHG-77, MTU/uplink) on segment SEG-EDGE-12 at 13:10.")
    # scope/impact framing (the retired TRIAGE's real content — P7 5-phase algebra)
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-9001",
             title="checkout-svc -> pricing-svc timeouts",
             short_description="checkout-svc -> pricing-svc timeouts after an MTU change",
             description="HighRetransSegs fired for the SEG-EDGE-12 uplink at 13:40 UTC. "
                         "checkout-svc calls to pricing-svc are timing out (~4.8s p95 on "
                         "CheckoutFlow); synthetic probes across SEG-EDGE-12 fail ~58% with heavy "
                         "TCP retransmits. No app deploy in the window — an L2/L3 path change "
                         "(MTU/uplink) on the edge segment is suspected.",
             work_notes="HighRetransSegs on SEG-EDGE-12; probes failing. Network change?",
             caller_id="monitoring.alerting"),
        edge(ET.AFFECTS, INC, SVC),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
        fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
    ], "calls": [*frame.calls, call("bt_health"), call("flowmap")],
       "narrative": frame.narrative + " Declared SEV2. checkout-svc's CheckoutFlow BT is "
       "degraded (art_p95 4800ms) and flowmap shows it depends on pricing-svc over HTTP. "
       "Still bleeding; investigate before mitigating."})

    # INVESTIGATE opens the hypothesize⇄evidence loop (verdict=repeat keeps looping)
    investigate_open = phase("investigate", [
        node(NT.DATABASE, db_id="pricing-db", engine="postgresql"),
        edge(ET.DEPENDS_ON, SVC_CALLEE, DB, origin="declared"),
        propose("h1", "MTU/uplink change on network segment SEG-EDGE-12 (CHG-77) is causing packet "
                "loss and retransmits on the checkout-svc -> pricing-svc path", "med", root=NETSEG),
        propose("h2", "pricing-svc's own database (pricing-db) is degraded (pool exhaustion / slow "
                "queries) — a code/db-side cause independent of the network", "low", root=DB),
    ], "Change-first: the network change (H1) lines up with onset and the caller/callee boundary; "
       "pricing-svc's own DB (H2) is a weaker alternative pending its own health check.",
       status="repeat")

    # the loop's confirm turn: rule out pricing-db, confirm the network-boundary path.
    db_fact = fid(DB, "conn_pool_util", T_INV)
    retrans_fact = fid(NETSEG, "retrans_segs", T_INV)
    probe_fact = fid(NETSEG, "probe_success", T_INV)
    callee_clean_fact = fid(SVC_CALLEE, "no_evidence:healthrule_violations", T_INV)

    investigate_confirm = phase("investigate",
        calls=[call("instant_query"), call("range_query"), call("healthrule_violations")],
        ops=[
            # pricing-db's full USE pull — healthy, ruling out the DB-side rival (H2).
            fact(DB, "conn_pool_util", 0.24, T_INV, source=S.PROMETHEUS, reliability=0.98),
            fact(DB, "active_connections", 61, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.98),
            fact(DB, "max_connections", 300, T_INV, unit="conn", source=S.PROMETHEUS, reliability=0.98),
            fact(DB, "slow_query_rate", 4, T_INV, unit="per_min", source=S.PROMETHEUS, reliability=0.97),
            fact(DB, "replication_lag", 0.2, T_INV, unit="s", source=S.PROMETHEUS, reliability=0.97),
            # the callee's own RED is nominal — it is the network hop between them that is on
            # fire, not pricing-svc (the boundary discriminator, alongside the clean health rule).
            fact(SVC_CALLEE, "red_rate", 940, T_INV, unit="rpm", source=S.APPD, reliability=0.95),
            fact(SVC_CALLEE, "red_errors", 0.004, T_INV, source=S.APPD, reliability=0.95),
            fact(SVC_CALLEE, "red_latency_p99", 120, T_INV, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC_CALLEE, "degraded", False, T_INV, source=S.APPD, reliability=0.95),
            # the segment carries real loss, not just retransmits — the physical boundary symptom.
            fact(NETSEG, "packet_loss", 0.09, T_INV, unit="ratio", source=S.PROMETHEUS, reliability=0.96),
            no_evidence("healthrule_violations", SVC_CALLEE, T_INV,
                       basis="AppD health-rule violations for pricing-svc came back clean (0 "
                             "violations) — its own BT (PriceLookup) is healthy"),
            edge(ET.CAUSED_BY, H1, CHG, level="high"),
            update("h2", status="refuted", add_refuting=[db_fact],
                   basis="pricing-db pool util 24% — healthy, not the cause"),
            update("h1", status="supported", level="high",
                   add_supporting=[retrans_fact, probe_fact, callee_clean_fact],
                   basis="retrans_segs spiked to 245/s and probe_success dropped to 42% on "
                         "SEG-EDGE-12 right after the MTU change; pricing-svc's own BT is clean "
                         "— a network-boundary problem, not app/DB-side"),
        ],
        narrative="Ruled out pricing-db (pool 24%, healthy). Retransmits + flapping probes on "
                  "SEG-EDGE-12, plus a clean callee, pin the fault to the network boundary.")

    act = phase("act", [
        update("h1", level="high", basis="proposed fix: revert the MTU/uplink change on "
               "SEG-EDGE-12 (CHG-77) — restore the prior MTU config"),
    ], "Safest reversible fix: revert the MTU change. Awaiting approval (gated).")

    verify = phase("verify", [
        fact(NETSEG, "retrans_segs", 8, T_FIX, unit="count", source=S.PROMETHEUS, reliability=0.97),
        fact(NETSEG, "probe_success", 0.99, T_FIX, unit="ratio", source=S.PROMETHEUS, reliability=0.97),
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        update("h1", status="confirmed", level="high",
               basis="reverting the MTU/uplink change restored retrans_segs to 8/s and "
                     "probe_success to 99% — recovery confirms the causal chain"),
    ], "Post-revert: retransmits back to 8/s, probe_success 99%, anomaly cleared. Root cause confirmed.")

    close = phase("close", [], "Postmortem: an MTU/uplink change on SEG-EDGE-12 (CHG-77) caused "
                  "retransmits and probe failures on the checkout-svc -> pricing-svc path; "
                  "pricing-svc's own BT/DB were healthy throughout. Reverting the change resolved "
                  "it. pricing-db ruled out.", status="done")

    fixtures = {
        "active_alerts": {
            "service": {"name": "checkout-svc", "env": "prod"},
            "alerts": [{"id": "ALT-1", "alertname": "HighRetransSegs", "at": T_ONSET, "state": "firing"}],
        },
        "find_recent_changes": {
            "changes": [{"number": "CHG-77", "type": "network_change",
                        "short_description": "MTU/uplink change on SEG-EDGE-12 (edge-agg-2.dc1)",
                        "description": "NetOps change lowering the MTU on the SEG-EDGE-12 uplink "
                                       "from 1500 to 1400 for a new overlay tunnel, without "
                                       "clamping MSS — oversized frames to pricing-svc are dropped, "
                                       "driving retransmits and probe failures.",
                        "start_date": T_CHANGE, "requested_by": "netops-oncall"}],
        },
        "bt_health": {
            "service": {"name": "checkout-svc", "env": "prod"},
            "bt": {"name": "CheckoutFlow"},
            "bt_metrics": [
                {"predicate": "art_p95", "value": 4800, "unit": "ms", "at": T_ONSET, "reliability": 0.95},
                {"predicate": "epm", "value": 57000, "unit": "calls_per_min", "at": T_ONSET, "reliability": 0.93},
                {"predicate": "delta_vs_baseline", "value": 3.2, "at": T_ONSET, "reliability": 0.9},
            ],
        },
        "flowmap": {
            "service": {"name": "checkout-svc", "env": "prod"},
            "flowmap": [{"exit_calls": [
                {"type": "HTTP", "target_service": "pricing-svc", "target_env": "prod"},
            ]}],
        },
        "instant_query": {
            "metrics": [{"subject": NETSEG, "predicate": "retrans_segs", "value": 245,
                        "unit": "count", "at": T_INV, "reliability": 0.97}],
        },
        "range_query": {
            "metrics": [{"subject": NETSEG, "predicate": "probe_success", "value": 0.42,
                        "unit": "ratio", "at": T_INV, "reliability": 0.95}],
        },
        "healthrule_violations": {
            "service": {"name": "pricing-svc", "env": "prod"},
            "violations": [],
        },
    }

    return subject, [frame, investigate_open, investigate_confirm, act, verify, close], fixtures
