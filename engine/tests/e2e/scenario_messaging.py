"""Scenario 7 — MESSAGING root cause (DESIGN §2.5 R-K3 layer: messaging; obs 11 ≥2/layer).

order-processor's consumer group falls behind: a deploy (CHG-55) shipped a slower message
handler, so consumer_lag on the `orders.events` topic climbs and the downstream fulfilment
SLA breaches. Differential diagnosis rules OUT a broker/producer fault (the topic's DLQ stays
empty and producer throughput is steady — the backlog is purely on the consumer side) and
confirms the consumer deploy as root cause. Discriminator: consumer_lag climbs while dlq_depth
and producer throughput are flat — a consumer-side slowdown, not a broker rebalance or a
poison-message flood. The scripted planner drives the REAL engine through the 5-phase
algebra (6 steps — the investigate loop runs twice).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 9, 30, tzinfo=UTC) + timedelta(minutes=minutes)


T_CHANGE = _t(0)     # 09:30 CHG-55 (consumer deploy) ships a slower handler
T_ONSET = _t(12)     # 09:42 consumer_lag anomaly onset, ALT-7 fires
T_INV = _t(26)       # 09:56 investigation: lag climbing, DLQ empty, producer steady
T_FIX = _t(50)       # 10:20 deploy rolled back, lag drained

SVC = nid(NT.SERVICE, service_name="order-processor", env="prod")
MQ = nid(NT.MESSAGE_QUEUE, topic_id="orders.events")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-7")
ALERT = nid(NT.ALERT, alert_id="ALT-7")
INC = nid(NT.INCIDENT, incident_id="INC-8801")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-55")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) for the MESSAGING root-cause scenario."""
    subject = SubjectRef(domain="app-incident", id="INC-8801", kind="incident")

    frame = phase("frame",
        calls=[call("find_recent_changes"), call("active_alerts")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-7"),
            fact(ANOM, "onset_value", 42000, T_ONSET, unit="msgs", source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            node(NT.SERVICE, service_name="order-processor", env="prod"),
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_rate", 320, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.96),
            fact(SVC, "red_latency_p99", 180, T_ONSET, unit="ms", source=S.APPD, reliability=0.94),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 60, T_ONSET, unit="s", source=S.SERVICENOW),
            event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
            edge(ET.AFFECTS, ANOM, SVC),
        ],
        narrative="order-processor's consumer group fell behind at 09:42 — lag on the "
                  "orders.events topic hit 42k messages, 12 minutes after CHG-55 (a consumer "
                  "deploy) shipped. ALT-7 (HighConsumerLag) fired; the fulfilment SLA is "
                  "breaching downstream.")

    # scope/impact framing (the retired TRIAGE's real content — P7 5-phase algebra)
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-8801",
             title="order-processor consumer lag climbing",
             short_description="order-processor lag on orders.events up after CHG-55",
             description="HighConsumerLag fired for the order-processor consumer group on the "
                         "orders.events Kafka topic at 09:42 UTC. Lag is climbing (42k -> 61k msgs) "
                         "while producer ingress holds flat at ~1,470 msg/min and the DLQ stays "
                         "empty — the backlog is purely consumer-side throughput, not a producer "
                         "surge or a poison message. Consumer deploy CHG-55 shipped at 09:30.",
             work_notes="HighConsumerLag; DLQ empty, producer steady. Suspect CHG-55.",
             caller_id="monitoring.alerting"),
        node(NT.MESSAGE_QUEUE, topic_id="orders.events", broker="kafka-prod-1", partitions=12,
             owner="fulfillment-platform@corp.example"),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.CONSUMES_FROM, SVC, MQ, origin="declared"),
        fact(MQ, "consumer_lag", 42000, T_ONSET, unit="msgs", source=S.PROMETHEUS, reliability=0.97),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
    ], "narrative": frame.narrative + " Declared SEV2. order-processor consumes the "
       "orders.events topic; lag is climbing but the topic itself is up. Investigate the "
       "consumer path, don't restart the broker blind."})

    investigate_open = phase("investigate",
        calls=[call("diff_range"), call("list_related_incidents")],
        status="repeat",
        ops=[
            node(NT.CHANGE_EVENT, change_id="CHG-55"),
            edge(ET.CHANGED_BY, SVC, CHG),
            propose("h1", "CHG-55 (consumer deploy) shipped a slower per-message handler, "
                    "dropping the consumer group's processing rate below the ingress rate so "
                    "lag on orders.events accumulates", "med", root=CHG),
            propose("h2", "The broker is unhealthy (a partition rebalance storm) OR upstream "
                    "producers flooded the topic with a traffic surge", "low", root=MQ),
        ],
        narrative="Change-first: CHG-55 (consumer deploy) landed 12m before onset — prime "
                  "suspect (H1). A broker rebalance or a producer surge (H2) is the weaker "
                  "alternative; if it were the broker, the DLQ and producer throughput would "
                  "move too — pending investigation.")

    lag_fact = fid(MQ, "consumer_lag", T_INV)
    dlq_fact = fid(MQ, "dlq_depth", T_INV)
    tput_fact = fid(MQ, "throughput", T_INV)

    investigate_confirm = phase("investigate",
        calls=[call("get_snapshots"), call("instant_query")],
        ops=[
            fact(MQ, "consumer_lag", 61000, T_INV, unit="msgs", source=S.PROMETHEUS, reliability=0.97),
            # DLQ empty -> not a poison-message flood; producer throughput steady -> not a surge.
            fact(MQ, "dlq_depth", 0, T_INV, unit="msgs", source=S.PROMETHEUS, reliability=0.97),
            fact(MQ, "throughput", 1450, T_INV, unit="msgs_per_min", source=S.PROMETHEUS, reliability=0.96),
            edge(ET.CAUSED_BY, H1, CHG, level="high"),
            update("h2", status="refuted", add_refuting=[dlq_fact, tput_fact],
                   basis="the DLQ stays empty (no poison messages) and producer throughput "
                   "holds steady at ~1450 msg/min (no ingress surge, no broker rebalance) — "
                   "the backlog is entirely on the consumer side, ruling out a broker/producer "
                   "cause"),
            update("h1", status="supported", level="high",
                   add_supporting=[lag_fact, tput_fact],
                   basis="consumer_lag keeps climbing (61k) while ingress throughput is flat — "
                   "the consumer group can no longer keep up; get_snapshots shows the new "
                   "handler's per-message time roughly doubled after CHG-55"),
        ],
        narrative="Lag is still climbing (61k) but ingress throughput is flat and the DLQ is "
                  "empty — the deficit is purely consumer-side processing rate. get_snapshots "
                  "shows the post-CHG-55 handler is ~2x slower per message. H2 (broker/producer) "
                  "refuted; H1 (consumer deploy) confirmed at high confidence.")

    act = phase("act", [
        update("h1", level="high",
               basis="proposed fix: roll back CHG-55 to the prior consumer build (restores "
               "the faster handler) so the group's processing rate recovers and lag drains"),
    ], "Safest reversible fix: roll the consumer deploy back to the prior build. Awaiting "
       "approval (gated).")

    verify = phase("verify", [
        fact(MQ, "consumer_lag", 300, T_FIX, unit="msgs", source=S.PROMETHEUS, reliability=0.97),
        fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
        event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
        event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
        update("h1", status="confirmed", level="high",
               basis="post-rollback: consumer_lag drained to ~300 and the group is keeping up, "
               "anomaly cleared — confirms the consumer deploy was the cause"),
    ], "Post-rollback: lag drained to ~300 messages; anomaly cleared. Root cause confirmed.")

    close = phase("close", [], "Postmortem: CHG-55 (consumer deploy) shipped a slower handler "
                  "-> consumer processing rate fell below ingress -> lag on orders.events "
                  "accumulated -> fulfilment SLA breach; rolling the deploy back drained the "
                  "lag. A broker rebalance / producer surge was investigated and ruled out.",
                  status="done")

    script = [frame, investigate_open, investigate_confirm, act, verify, close]

    fixtures = {
        "find_recent_changes": {
            "changes": [
                {"number": "CHG-55", "type": "deployment",
                 "short_description": "Deploy order-processor v3.2.0 (per-item price enrichment)",
                 "description": "Deploy of order-processor v3.2.0. Replaces the batched pricing "
                                "lookup in the message handler with a per-item synchronous "
                                "enrichment call — a blocking RPC per message that ~2x'd handler "
                                "latency, so the consumer group can no longer keep up.",
                 "cmdb_ci": {"display_value": "order-processor",
                             "owner": "fulfillment-platform@corp.example", "version": "v3.2.0"},
                 "requested_by": "dev-mq", "start_date": T_CHANGE},
            ],
        },
        "active_alerts": {
            "service": {"name": "order-processor", "env": "prod"},
            "alerts": [
                {"id": "ALT-7", "alertname": "HighConsumerLag", "at": T_ONSET, "state": "firing"},
            ],
        },
        "diff_range": {
            "commit": {"sha": "e5f6a7b", "repo": "order-processor", "author": "dev-mq",
                       "parent_sha": "d4c3b2a", "authored_at": T_CHANGE,
                       "message": "perf(pricing): per-item enrichment for fresher prices (PR #310)"},
            "diff": {"at": T_CHANGE, "files_changed": 1, "lines_added": 22, "lines_deleted": 4,
                     "reliability": 0.99},
            "change": {"change_id": "CHG-55", "change_type": "deployment"},
        },
        "get_snapshots": {
            "service": {"name": "order-processor", "env": "prod"},
            "bt_metrics": [
                {"predicate": "red_latency_p99", "value": 210, "unit": "ms", "at": T_INV,
                 "reliability": 0.94},
            ],
        },
        "instant_query": {
            "metrics": [
                {"subject": MQ, "predicate": "consumer_lag", "value": 61000, "unit": "msgs",
                 "at": T_INV, "reliability": 0.97},
            ],
        },
        "list_related_incidents": {
            "primary_incident": "INC-8801",
            "related_incidents": [
                {"number": "INC-8802", "priority": "3 - Moderate", "opened_at": _t(14),
                 "cmdb_ci": "fulfilment-api", "confidence": "high",
                 "title": "fulfilment-api orders delayed",
                 "short_description": "fulfilment-api SLA breach downstream of the orders.events lag"},
            ],
        },
    }

    return subject, script, fixtures
