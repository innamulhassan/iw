"""Scenario 5 — FIREWALL / security-rule layer (DESIGN §2.5 R-K3).

checkout-api's calls to a third-party fraud-scoring dependency start failing 7 minutes
after ServiceNow change CHG-3311 ("tighten egress ACL on prod-vpc") is implemented.
Blackbox probes show probe_success=0 to exactly ONE egress target (egress-fraud-score)
while sibling egress paths stay healthy; Splunk shows CLEAN policy denies
(action="blocked") hitting FirewallRule FW-EGR-118 — not packet loss/retransmits.
Differential diagnosis rules OUT a physical-layer link flap (packet_loss stays 0.0%)
and confirms the ACL change as root cause. The fix (revert CHG-3311 on FW-EGR-118) is a
SECURITY change: ACT only PROPOSES it (an UpdateHypothesis, no capability call) —
the write-gate (CapabilityLayer.invoke, allow_write only in the writes_allowed phase) is exercised
directly by a premature auto-remediation attempt (`ocp__restart`) fired outside the
gated phase, which the engine must block. The scripted planner drives the real engine
through the 5-phase algebra (6 steps — the investigate loop runs twice) to a RESOLVED close.
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


T_CHANGE = _t(5)    # 09:05 CHG-3311 "tighten egress ACL on prod-vpc" implemented
T_ONSET = _t(12)    # 09:12 denies begin / alert fires / probe fails
T_INV = _t(25)      # 09:25 investigation: fw denies + segment health probes
T_FIX = _t(50)      # 09:50 post-approval ACL revert verified

SVC = nid(NT.SERVICE, service_name="checkout-api", env="prod")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-3311")
INC = nid(NT.INCIDENT, incident_id="INC-7702")
EXT = nid(NT.EXTERNAL_SERVICE, service_name="fraud-score-vendor")
SEG_FRAUD = nid(NT.NETWORK_SEGMENT, segment_id="egress-fraud-score")
SEG_GEO = nid(NT.NETWORK_SEGMENT, segment_id="egress-geoip")
SEG_PAY = nid(NT.NETWORK_SEGMENT, segment_id="egress-payment-gw")
RULE = nid(NT.FIREWALL_RULE, rule_id="FW-EGR-118", direction="egress", proto="tcp",
           port_range="443", src="10.20.0.0/24", dst="fraud-score.vendor.com/32")
H1, H2 = hid("h1"), hid("h2")


def build(premature_write: bool = False):
    """Returns (subject, script, fixtures). When `premature_write` is True, FRAME fires an
    extra `ocp__restart` capability call (an over-eager auto-remediation attempt) — proving
    the write-gate (CapabilityLayer: writes only execute with allow_write, granted only in
    the writes_allowed phase) blocks it outside the approved gate without disturbing the rest of the
    run."""
    subject = SubjectRef(domain="app-incident", id="INC-7702", kind="incident")

    frame = phase("frame",
        calls=[call("active_alerts"), call("find_recent_changes")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 0.18, T_ONSET, source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            # onset RED for checkout-api: 18% of calls fail (the fraud-scoring dependency),
            # throughput and p50 for the rest of the surface stay normal — a scoped failure.
            fact(SVC, "red_rate", 640, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_latency_p50", 62, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 0.995, T_ONSET, source=S.SERVICENOW),
            event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
            event(ANOM, "detected", T_ONSET, source=S.PROMETHEUS),
            edge(ET.AFFECTS, ANOM, SVC),
        ],
        narrative=("checkout-api's calls to the fraud-scoring dependency started erroring "
                   "at 09:12, 7 minutes after change CHG-3311 ('tighten egress ACL on "
                   "prod-vpc') was implemented at 09:05."))

    # scope/impact framing (the retired TRIAGE's real content — P7 5-phase algebra)
    scope_ops = [
        node(NT.INCIDENT, incident_id="INC-7702",
             title="fraud-scoring egress calls failing",
             short_description="fraud-scoring egress failing ~7m after CHG-3311 ACL change",
             work_notes="ExternalDependencyErrorRateHigh; recurrence of INC-7699.",
             caller_id="monitoring.alerting"),
        node(NT.EXTERNAL_SERVICE, service_name="fraud-score-vendor", vendor="FraudScoreCo"),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.DEPENDS_ON, SVC, EXT, origin="declared"),
        fact(SVC, "red_latency_p99", 900, T_ONSET, unit="ms", source=S.APPD, reliability=0.9),
        # the vendor's own exit-call RED, seen from checkout-api: the endpoint is UP
        # (availability 1.0 — this is NOT a vendor outage) yet our calls to it error out and
        # the call rate has collapsed — the signature of a block on OUR side, not theirs.
        fact(EXT, "availability", 1.0, T_ONSET, source=S.APPD, reliability=0.9),
        fact(EXT, "error_rate", 0.98, T_ONSET, source=S.APPD, reliability=0.92),
        fact(EXT, "call_rate", 3, T_ONSET, unit="rpm", source=S.APPD, reliability=0.92),
        fact(EXT, "latency_p99", 10000, T_ONSET, unit="ms", source=S.APPD, reliability=0.9),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
    ]
    if premature_write:
        # an impatient on-call tries an auto-remediation restart before root-causing —
        # the write-gate (allow_write only in the writes_allowed phase) must block it here.
        frame = frame.model_copy(update={
            "ops": [*frame.ops, *scope_ops],
            "calls": [*frame.calls, call("ocp__restart")],
            "narrative": frame.narrative + " Declared SEV3. checkout-api depends on the "
            "fraud-scoring vendor (declared in CMDB). An on-call engineer, impatient, tries "
            "an auto-remediation restart before root-causing — the write-gate must hold: "
            "only the human-gated ACT phase may execute a write."})
    else:
        frame = frame.model_copy(update={
            "ops": [*frame.ops, *scope_ops],
            "narrative": frame.narrative + " Declared SEV3. checkout-api depends on the "
            "fraud-scoring vendor (declared in CMDB). Still failing; investigate before "
            "touching network policy."})

    investigate_open = phase("investigate",
        calls=[call("list_related_incidents")],
        ops=[
            node(NT.NETWORK_SEGMENT, segment_id="egress-fraud-score", cidr="10.20.0.0/24"),
            node(NT.FIREWALL_RULE, rule_id="FW-EGR-118", direction="egress", proto="tcp",
                 port_range="443", src="10.20.0.0/24", dst="fraud-score.vendor.com/32"),
            edge(ET.CONNECTS_TO, SVC, SEG_FRAUD),
            edge(ET.SECURED_BY, SEG_FRAUD, RULE),
            edge(ET.CHANGED_BY, RULE, CHG),
            propose("h1", "FirewallRule FW-EGR-118 (tightened by CHG-3311, 'egress ACL "
                    "hardening on prod-vpc') is blocking egress to fraud-score.vendor.com",
                    "med", root=RULE),
            propose("h2", "Physical-layer link flapping/packet loss on network segment "
                    "egress-fraud-score", "low", root=SEG_FRAUD),
        ],
        narrative="Change-first: CHG-3311 tightened the egress ACL 7 minutes before onset — "
                  "the firewall rule is the prime suspect (H1). ServiceNow flags this as a "
                  "RECURRENCE of INC-7699 (last quarter, same FW-EGR-118: an egress-ACL "
                  "tightening blocked the same vendor, resolved by reverting the ACL) — a "
                  "known-recurrence prior that immediately sharpens H1. A raw link-layer flap "
                  "on the same segment is a weaker alternative (H2).",
        status="repeat")

    # the loop's confirm turn: clean policy denies (not drops) confirm the ACL; sibling
    # segments + normal packet-loss on this segment rule out a physical-layer flap.
    deny_fact = fid(RULE, "deny_count", T_INV)
    seg_fact = fid(SEG_FRAUD, "packet_loss", T_INV)
    investigate_confirm = phase("investigate",
        calls=[call("fetch_metrics"), call("search_fw_denies")],
        ops=[
            node(NT.NETWORK_SEGMENT, segment_id="egress-geoip", cidr="10.20.1.0/24"),
            node(NT.NETWORK_SEGMENT, segment_id="egress-payment-gw", cidr="10.20.2.0/24"),
            edge(ET.CONNECTS_TO, SVC, SEG_GEO),
            edge(ET.CONNECTS_TO, SVC, SEG_PAY),
            edge(ET.CAUSED_BY, H1, RULE, level="high"),
            update("h2", status="refuted", add_refuting=[seg_fact],
                   basis="packet_loss is 0.0% on egress-fraud-score — no link flap; the "
                         "probe failure is a clean policy deny, not a physical fault"),
            update("h1", status="supported", level="high", add_supporting=[deny_fact],
                   basis="214 clean 'blocked' denies at FW-EGR-118 since 09:12; probes to "
                         "egress-geoip/egress-payment-gw stay healthy (probe_success=1) — "
                         "the failure is scoped exactly to the rule CHG-3311 touched"),
        ],
        narrative=("Splunk: 214 clean policy denies (action=blocked) at FW-EGR-118 since "
                   "09:12, one target only. Prometheus: probe_success=0 on egress-fraud-"
                   "score alone; egress-geoip and egress-payment-gw stay healthy, and "
                   "packet_loss on the affected segment is 0.0% — not a network flap. H2 "
                   "ruled out; H1 confirmed as leading (supported/high)."))

    act = phase("act", [
        update("h1", level="high",
               basis="proposed fix: revert CHG-3311 on FW-EGR-118 (restore the prior "
                     "egress ACL) via emergency change — a security-policy write, "
                     "human-gated: presented for approval, never auto-applied"),
    ], "Safest reversible fix: revert the ACL tightening on FW-EGR-118. This is a "
       "security change — proposed only; execution awaits the human-approved gate.")

    verify = phase("verify", [
        fact(SVC, "red_errors", 0.01, T_FIX, source=S.PROMETHEUS),
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        fact(SEG_FRAUD, "probe_success", 1, T_FIX, source=S.PROMETHEUS),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        update("h1", status="confirmed", level="high",
               basis="post-approval revert of CHG-3311 applied to FW-EGR-118: denies "
                     "stopped, probe_success back to 1, red_errors back to 1% — confirms "
                     "the causal chain"),
    ], "Post-revert: denies stopped, egress-fraud-score probe_success recovered, "
       "checkout-api red_errors back to 1%. Root cause confirmed.")

    close = phase("close", [], "Postmortem: CHG-3311 (egress ACL hardening) tightened "
                  "FW-EGR-118 and blocked egress to fraud-score.vendor.com; the "
                  "human-approved ACL revert resolved it. Link-layer flap ruled out.",
                  status="done")

    fixtures = {
        "active_alerts": {
            "service": {"name": "checkout-api", "env": "prod"},
            "alerts": [{"id": "ALT-1", "alertname": "ExternalDependencyErrorRateHigh",
                        "at": T_ONSET, "state": "firing"}],
            "metrics": [{"predicate": "red_errors", "value": 0.18, "at": T_ONSET,
                         "reliability": 0.97}],
        },
        "find_recent_changes": {
            "changes": [{
                "number": "CHG-3311",
                "type": "network",
                "cmdb_ci": {"display_value": "checkout-api"},
                "start_date": T_CHANGE,
                "requested_by": "netops-automation",
                "env": "prod",
            }],
        },
        "fetch_metrics": {
            "metrics": [
                {"subject": SEG_FRAUD, "predicate": "probe_success", "value": 0,
                 "at": T_INV, "reliability": 0.99},
                {"subject": SEG_FRAUD, "predicate": "packet_loss", "value": 0.0,
                 "at": T_INV, "reliability": 0.97},
                # retransmits are ZERO on the affected segment — the traffic isn't being dropped
                # on the wire, it's being cleanly denied by policy (rules out the link-flap H2).
                {"subject": SEG_FRAUD, "predicate": "retrans_segs", "value": 0, "unit": "count",
                 "at": T_INV, "reliability": 0.97},
                {"subject": SEG_GEO, "predicate": "probe_success", "value": 1,
                 "at": T_INV, "reliability": 0.99},
                {"subject": SEG_GEO, "predicate": "packet_loss", "value": 0.0,
                 "at": T_INV, "reliability": 0.97},
                {"subject": SEG_PAY, "predicate": "probe_success", "value": 1,
                 "at": T_INV, "reliability": 0.99},
                {"subject": SEG_PAY, "predicate": "packet_loss", "value": 0.0,
                 "at": T_INV, "reliability": 0.97},
            ],
        },
        # a true RECURRENCE: the same FW rule + same egress vendor took the same incident down
        # last quarter — folded as a RECURRENCE_OF edge off INC-7702 (a strong hypothesis prior).
        "list_related_incidents": {
            "primary_incident": "INC-7702",
            "related_incidents": [
                {"number": "INC-7699", "priority": "2 - High", "opened_at": _t(-43200),
                 "cmdb_ci": "checkout-api", "relation": "recurrence", "confidence": "high"},
            ],
        },
        "search_fw_denies": {
            "fw_denies": [{
                "rule_id": "FW-EGR-118", "action": "blocked", "direction": "egress",
                "proto": "tcp", "port_range": "443", "src": "10.20.0.0/24",
                "dst": "fraud-score.vendor.com/32", "_time": T_INV, "deny_count": 214,
                "reliability": 0.97,
            }],
        },
        "ocp__restart": {},  # never reached — the write-gate blocks before normalize()
    }

    script = [frame, investigate_open, investigate_confirm, act, verify, close]
    return subject, script, fixtures
