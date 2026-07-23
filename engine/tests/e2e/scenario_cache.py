"""Scenario — CACHE stampede root cause (DESIGN §2.5 R-K3 layer: caching / hot path).

product-api's p99 latency spikes shortly after CHG-22, a deploy of product-api v3.4.0.
Differential diagnosis rules OUT an application code regression (the request-handling
compute path is byte-identical; the diff is only the cache-client config) and confirms
a CACHE STAMPEDE: the deploy silently disabled client-side request coalescing
(singleflight), so a hot key that previously generated 1 read/bucket now fans out to
~50 simultaneous reads into the Redis tier, saturating its connection pool and driving
tail latency. Discriminator: the slow exit calls are cache-bound (reads_from Redis),
the fan-out ratio is 50x, and the service's own p50 stays flat — a hot-path/cache
shape, not a code fault. The scripted planner drives the REAL engine through the
5-phase algebra (6 steps — the investigate loop runs twice), exercising appd,
prometheus, servicenow and git via CapabilityCall + fixtures.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, span, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 14, 10, tzinfo=UTC) + timedelta(minutes=minutes)


T_CHANGE = _t(0)     # 14:10 deploy product-api v3.4.0 (disables singleflight)
T_ONSET = _t(6)      # 14:16 latency anomaly onset, ALT-1 fires
T_INV = _t(21)       # 14:31 investigation: cache fan-out + pool pinned
T_FIX = _t(46)       # 14:56 singleflight restored, recovery confirmed

SVC = nid(NT.SERVICE, service_name="product-api", env="prod")
EP = nid(NT.API_ENDPOINT, service_name="product-api", env="prod", method="GET",
         route_template="/products/{id}")
CACHE = nid(NT.CACHE, cache_id="product-redis")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
INC = nid(NT.INCIDENT, incident_id="INC-5500")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-22")
COMMIT = nid(NT.CODE_COMMIT, sha="9f8e7d6")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) for the CACHE-stampede root-cause scenario."""
    subject = SubjectRef(domain="app-incident", id="INC-5500", kind="incident")

    # ── FRAME ────────────────────────────────────────────────────────────────────
    frame = phase("frame",
        calls=[call("find_recent_changes", ci="product-api", window="30m"),
               call("active_alerts", service="product-api", env="prod")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 6800, T_ONSET, unit="ms", source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            # onset RED snapshot: tail (p99) blown, service's own compute (p50) flat,
            # error rate normal — the shape that will discriminate a downstream/cache
            # saturation from a code fault.
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_latency_p99", 6800, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "red_latency_p50", 64, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "red_rate", 1400, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_errors", 0.002, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            node(NT.SERVICE, service_name="product-api", env="prod",
                 owner="catalog-platform@corp.example", version="v3.4.0"),
            node(NT.CACHE, cache_id="product-redis", engine="redis", version="7.2",
                 cluster_mode="enabled", owner="catalog-platform@corp.example"),
            node(NT.ALERT, alert_id="ALT-1"),
            node(NT.CHANGE_EVENT, change_id="CHG-22",
                 short_description="Deploy product-api v3.4.0 (cache client refactor)",
                 description="Deploy of product-api v3.4.0; refactors pkg/cache/client.go and "
                             "removes the singleflight de-duplication around cache reads, believed "
                             "redundant. Without it, concurrent misses on a hot key each issue "
                             "their own read — a cache stampede under load."),
            event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
            event(ALERT, "fired", T_ONSET, source=S.PROMETHEUS),
            event(CHG, "implemented", T_CHANGE, source=S.SERVICENOW,
                  change="deploy product-api v3.4.0"),
            # a captured distributed trace at onset — the SPAN species (§2.6): a bounded happening SVC is in
            span(SVC, "trace", T_ONSET, ended_at=T_ONSET + timedelta(milliseconds=6800),
                 correlation_id="trace-product-8c21", value={"error": False}, reliability=0.95),
            edge(ET.AFFECTS, ANOM, SVC),
            edge(ET.FIRED_ON, ALERT, SVC),
            edge(ET.CHANGED_BY, SVC, CHG),
            edge(ET.CORRELATED_WITH, ANOM, CHG, level="med"),
            edge(ET.READS_FROM, SVC, CACHE, origin="declared"),
        ],
        narrative="product-api p99 jumped to 6.8s at 14:16, 6m after the v3.4.0 deploy at "
                  "14:10. p50 is flat at 64ms, errors normal — a tail-only / downstream shape.")

    # ── scope/impact framing folded into FRAME (the retired TRIAGE — P7 5-phase algebra) ──
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-5500",
             title="product-api latency after cache deploy",
             short_description="product-api p99 up after cache deploy; hit-rate collapsed",
             description="HighLatencyP99 fired for product-api (prod, tier-1) at 14:16 UTC, ~6m "
                         "after cache-client deploy CHG-22 (commit 9f8e7d6). product-redis hit-rate "
                         "collapsed from ~96% to 41%, eviction rate surged to 420/min and cache "
                         "memory is at 94% — a cache-stampede shape. The service's own p50 stays "
                         "flat (67ms), so the app compute path is fine; the latency is all "
                         "cache-miss backend load.",
             work_notes="HighLatencyP99; redis hit-rate 41%, evictions surging.",
             caller_id="monitoring.alerting"),
        node(NT.API_ENDPOINT, service_name="product-api", env="prod", method="GET",
             route_template="/products/{id}"),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.EXPOSES, SVC, EP, origin="declared"),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
        fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
        fact(SVC, "slo_target", 0.999, T_ONSET, source=S.SERVICENOW),
    ], "calls": [*frame.calls, call("appd_bt_metrics", bt="/products/{id}", window="10m")],
       "narrative": frame.narrative + " Declared SEV2. The /products/{id} hot path is the "
       "source of the tail. It reads from product-redis — investigate the cache tier, "
       "don't blind-mitigate."})

    # ── INVESTIGATE opens the hypothesize⇄evidence loop ─────────────────────────
    investigate_open = phase("investigate",
        calls=[call("git_diff", sha="9f8e7d6", path="pkg/cache/client.go"),
               call("list_related_incidents", cmdb_ci="product-api")],
        status="repeat",
        ops=[
            node(NT.CODE_COMMIT, sha="9f8e7d6", repo="product-api", author="platform-team",
                 message="refactor(cache): drop singleflight around cache reads (PR #921)"),
            edge(ET.INTRODUCED_BY, CHG, COMMIT),
            # related prior: search-api hit the same stampede shape last quarter when IT
            # disabled coalescing — a hypothesis prior that sharpens H1.
            node(NT.INCIDENT, incident_id="INC-4210", severity="3 - Moderate"),
            edge(ET.SIMILAR_TO, INC, nid(NT.INCIDENT, incident_id="INC-4210"), level="high"),
            propose("h1",
                    "v3.4.0 (commit 9f8e7d6) disabled client-side cache coalescing "
                    "(singleflight), causing a read stampede into product-redis",
                    "med", root=COMMIT),
            propose("h2", "application code regression in product-api v3.4.0 request handler",
                    "low", root=COMMIT),
        ],
        narrative="Change-first: the deploy is the prime suspect. Two competing change "
                  "hypotheses — H1 a cache-config change (stampede), H2 a code regression. "
                  "search-api filed the same stampede shape last quarter when coalescing was "
                  "disabled — a related prior reinforcing H1.")

    # ── the loop.s confirm turn ─────────────────────────────────────────────────
    # rule OUT the code regression (p50 flat — the service's own compute is unaffected),
    # confirm the stampede (hit-rate collapsed, evictions surging, memory pinned — a
    # cache-tier saturation shape).
    hitrate_fact = fid(CACHE, "hit_rate", T_INV)
    evict_fact = fid(CACHE, "eviction_rate", T_INV)
    mem_fact = fid(CACHE, "mem_utilization", T_INV)
    p50_fact = fid(SVC, "red_latency_p50", T_INV)
    investigate_confirm = phase("investigate",
        calls=[call("appd_bt_metrics", bt="/products/{id}", window="10m"),
               call("instant_query", query="redis_connected_clients product-redis"),
               call("git_blame", sha="9f8e7d6", file="pkg/cache/client.go", line=88)],
        ops=[
            # the cache-tier USE snapshot: hit-rate collapsed, evictions surging, memory
            # pinned near the ceiling — a stampede shape.
            fact(CACHE, "hit_rate", 0.41, T_INV, source=S.PROMETHEUS, reliability=0.97),
            fact(CACHE, "eviction_rate", 420, T_INV, unit="per_min", source=S.PROMETHEUS,
                 reliability=0.95),
            fact(CACHE, "mem_utilization", 0.94, T_INV, source=S.PROMETHEUS, reliability=0.97),
            # the code path that matters: the service compute is unaffected (p50 flat)
            fact(SVC, "red_latency_p50", 67, T_INV, unit="ms", source=S.APPD, reliability=0.95),
            # the smoking gun in the diff: singleflight disabled
            node(NT.CODE_COMMIT, sha="9f8e7d6",
                 diff_summary="singleflight disabled in pkg/cache/client.go:88"),
            edge(ET.READS_FROM, SVC, CACHE, origin="discovered"),
            edge(ET.CAUSED_BY, H1, COMMIT, level="high"),
            # rule out the code regression: flat p50 (compute unaffected) — the handler path
            # is byte-identical, only the cache-client config changed.
            update("h2", status="refuted", add_refuting=[p50_fact],
                   basis="p50 flat at 67ms — the request-handler compute path is unaffected; "
                   "no regression in v3.4.0's handler"),
            update("h1", status="supported", level="high",
                   add_supporting=[hitrate_fact, evict_fact, mem_fact],
                   basis="hit-rate collapsed to 41%, evictions surging at 420/min, mem at 94%; "
                   "diff shows singleflight disabled at pkg/cache/client.go:88 — the missing "
                   "coalescing lets 50 simultaneous reads fan into product-redis per hot key"),
        ],
        narrative="Ruled out the code regression (p50 flat at 67ms). Confirmed the stampede: "
                  "hit-rate collapsed to 41%, evictions 420/min, memory pinned at 94%. The diff "
                  "disables singleflight at client.go:88 — without coalescing, every request "
                  "issues its own cache read instead of sharing one in-flight.")

    # ── ACT (human-gated) ────────────────────────────────────────────────────────
    act = phase("act",
        ops=[
            update("h1", level="high",
                   basis="proposed fix: revert product-api to v3.3.2 (re-enable singleflight) "
                   "and raise the Redis max connections as defense-in-depth"),
        ],
        narrative="Safest reversible fix: roll back to v3.3.2 (re-enable singleflight). "
                  "Awaiting approval (gated).")

    # ── VERIFY ───────────────────────────────────────────────────────────────────
    verify = phase("verify",
        ops=[
            fact(SVC, "red_latency_p99", 140, T_FIX, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
            fact(CACHE, "hit_rate", 0.96, T_FIX, source=S.PROMETHEUS, reliability=0.97),
            fact(CACHE, "eviction_rate", 4, T_FIX, unit="per_min", source=S.PROMETHEUS,
                 reliability=0.95),
            event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
            event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
            update("h1", status="confirmed", level="high",
                   basis="rollback restored singleflight: hit-rate back to 96%, evictions down "
                   "to 4/min, p99 back to 140ms — recovery confirms the causal chain"),
        ],
        narrative="Post-rollback: p99 back to 140ms, hit-rate 96%, evictions down to 4/min. "
                  "Root cause confirmed.")

    # ── CLOSE ────────────────────────────────────────────────────────────────────
    close = phase("close", ops=[],
        narrative="Postmortem: v3.4.0 (9f8e7d6) disabled singleflight in the cache client, "
                  "causing a 50x read stampede into product-redis; rollback to v3.3.2 resolved "
                  "it. Code regression ruled out.")

    script = [frame, investigate_open, investigate_confirm, act, verify, close]

    # ── fixtures: what the capability calls resolve to ────────────────────────────
    fixtures = {
        "appd": {
            "bt_metrics": [
                {"predicate": "red_latency_p99", "value": 6800, "unit": "ms", "at": T_INV,
                 "reliability": 0.93},
                {"predicate": "red_latency_p50", "value": 67, "unit": "ms", "at": T_INV,
                 "reliability": 0.95},
            ],
            "snapshots": [
                {"exit_calls": [{"type": "REDIS", "cache_id": "product-redis"}]},
            ],
        },
        "instant_query": {
            "metrics": [
                {"subject": CACHE, "predicate": "hit_rate", "value": 0.41,
                 "unit": "ratio", "at": T_INV, "reliability": 0.97},
                {"subject": CACHE, "predicate": "eviction_rate", "value": 420,
                 "unit": "per_min", "at": T_INV, "reliability": 0.95},
                {"subject": CACHE, "predicate": "mem_utilization", "value": 0.94,
                 "unit": "ratio", "at": T_INV, "reliability": 0.97},
            ],
        },
        # co-firing sibling: search-api filed the same stampede shape last quarter
        "list_related_incidents": {
            "primary_incident": "INC-5500",
            "related_incidents": [
                {"number": "INC-4210", "priority": "3 - Moderate", "opened_at": _t(-60),
                 "cmdb_ci": "search-api", "confidence": "high",
                 "title": "search-api cache stampede (prior quarter)",
                 "short_description": "search-api hit the same stampede when it disabled coalescing"},
            ],
        },
    }

    return subject, script, fixtures
