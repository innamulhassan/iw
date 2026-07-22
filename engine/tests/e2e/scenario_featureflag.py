"""Scenario — FEATURE-FLAG root cause (DESIGN §2.5 R-K3 layer: configuration / feature flag).

cart-api starts throwing 5xx right after CHG-77, a feature-flag flip that takes the
`new-tax-engine` flag from 5% to 100% rollout. Differential diagnosis rules OUT an
application-code deploy regression (there was NO deploy — the last build shipped 3 days
ago) and confirms the FLAG as root cause: the new code path the flag gates throws a
TaxEngineException on carts with >5 line items, and the error signature is only present
in the flag-gated branch. Discriminator: the flag flip time (14:05) correlates exactly
with onset (14:05:30), no build/deploy event in the window, and flipping the flag OFF
(recycling to 0%) clears the errors. Teaches the lesson that "not every correlated change
is a release" — a config/flag flip can be the cause. The scripted planner drives the REAL
engine through the 5-phase algebra (6 steps — the investigate loop runs twice), exercising
appd, prometheus, servicenow and git via CapabilityCall + fixtures.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 14, 5, tzinfo=UTC) + timedelta(minutes=minutes)


T_FLAG = _t(0)      # 14:05:00 CHG-77 flips new-tax-engine 5% → 100%
T_ONSET = _t(0.5)   # 14:05:30 5xx onset (flag hits the heavy carts)
T_INV = _t(16)      # 14:21 investigation
T_FIX = _t(38)      # 14:43 flag recycled to 0%, recovery confirmed

SVC = nid(NT.SERVICE, service_name="cart-api", env="prod")
EP = nid(NT.API_ENDPOINT, service_name="cart-api", env="prod", method="POST",
         route_template="/cart/checkout")
FLAG = nid(NT.FEATURE_FLAG, flag_key="new-tax-engine", env="prod")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
INC = nid(NT.INCIDENT, incident_id="INC-5600")
CHG = nid(NT.CHANGE_EVENT, change_id="CHG-77")
ERRSIG = nid(NT.ERROR_SIGNATURE, signature_hash="taxengine-bulk-cart")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) for the FEATURE-FLAG root-cause scenario."""
    subject = SubjectRef(domain="app-incident", id="INC-5600", kind="incident")

    # ── FRAME ────────────────────────────────────────────────────────────────────
    frame = phase("frame",
        calls=[call("find_recent_changes", ci="cart-api", window="30m"),
               call("active_alerts", service="cart-api", env="prod")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            node(NT.SERVICE, service_name="cart-api", env="prod"),
            node(NT.FEATURE_FLAG, flag_key="new-tax-engine", env="prod"),
            node(NT.ALERT, alert_id="ALT-1"),
            node(NT.CHANGE_EVENT, change_id="CHG-77", change_type="feature-flag",
                 target_ref="new-tax-engine", actor="tax-platform"),
            # onset RED snapshot: 5xx errors spiked, rate steady, p99 elevated — an error
            # shape (not a saturation shape). p50 still sane.
            fact(ANOM, "onset_value", 0.34, T_ONSET, source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_errors", 0.34, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_rate", 940, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_latency_p50", 71, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "red_latency_p99", 2100, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 0.999, T_ONSET, source=S.SERVICENOW),
            # the change in the window is a FLAG FLIP, not a deploy
            fact(FLAG, "enabled", True, T_ONSET, source=S.SERVICENOW, reliability=0.99),
            fact(FLAG, "rollout_percentage", 100, T_ONSET, source=S.SERVICENOW, reliability=0.99),
            event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
            event(ALERT, "fired", T_ONSET, source=S.PROMETHEUS),
            event(CHG, "implemented", T_FLAG, source=S.SERVICENOW,
                  change="flip feature-flag new-tax-engine 5% → 100%"),
            event(FLAG, "flipped", T_FLAG, source=S.SERVICENOW, rollout=100),
            edge(ET.AFFECTS, ANOM, SVC),
            edge(ET.FIRED_ON, ALERT, SVC),
            edge(ET.CHANGED_BY, SVC, CHG),
            edge(ET.CORRELATED_WITH, ANOM, CHG, level="high"),
        ],
        narrative="cart-api 5xx spiked to 34% at 14:05:30. The only change in the 30m "
                  "window is CHG-77 — a feature-flag flip (new-tax-engine 5% → 100%) at "
                  "14:05:00, 30 seconds before onset. No build/deploy event in the window.")

    # ── scope/impact framing folded into FRAME (the retired TRIAGE — P7 5-phase algebra) ──
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-5600"),
        node(NT.API_ENDPOINT, service_name="cart-api", env="prod", method="POST",
             route_template="/cart/checkout"),
        # the checkout endpoint's status-code distribution: 34% 5xx — the scope evidence
        # that narrows the suspect set to the checkout path.
        fact(EP, "status_code_dist", {"500": 0.34, "200": 0.64, "4xx": 0.02}, T_ONSET,
             source=S.APPD, reliability=0.95),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.EXPOSES, SVC, EP, origin="declared"),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
    ], "calls": [*frame.calls, call("appd_bt_metrics", bt="/cart/checkout", window="10m")],
       "narrative": frame.narrative + " Declared SEV2. /cart/checkout is returning 34% 5xx. "
       "The flag flip is the prime suspect — but investigate the actual code path before "
       "blindly reverting a flag that other teams may depend on."})

    # ── INVESTIGATE opens the hypothesize⇄evidence loop ─────────────────────────
    investigate_open = phase("investigate",
        calls=[call("git_log", path="services/cart-api/src/tax", limit=5),
               call("list_related_incidents", cmdb_ci="cart-api")],
        status="repeat",
        ops=[
            node(NT.CODE_COMMIT, sha="c3d4e5f"),  # the last deploy — 3 days ago, pre-flag
            # NOTE: feature_flag is edge-isolated in the model (no typed edges to/from it);
            # the causal link to the gated code path is carried by the hypothesis
            # (CAUSED_BY H1 → FLAG) and the change_event correlation, not a direct edge.
            # related prior: pricing-api hit the same flag-onset error shape when its
            # flag rolled out — a hypothesis prior that sharpens H1.
            node(NT.INCIDENT, incident_id="INC-4988", severity="3 - Moderate"),
            edge(ET.SIMILAR_TO, INC, nid(NT.INCIDENT, incident_id="INC-4988"), level="high"),
            propose("h1",
                    "the new-tax-engine flag gates an untested tax path that throws "
                    "TaxEngineException on carts with >5 line items",
                    "med", root=FLAG),
            propose("h2", "application code regression in cart-api's last deploy (c3d4e5f)",
                    "low", root=nid(NT.CODE_COMMIT, sha="c3d4e5f")),
        ],
        narrative="The flag (H1) is the prime suspect — onset is 30s after the flip. The "
                  "deploy hypothesis (H2) is weak: the last build c3d4e5f shipped 3 days ago "
                  "with no errors. pricing-api filed the same flag-onset shape when its flag "
                  "rolled out — a related prior reinforcing H1.")

    # ── the loop.s confirm turn ─────────────────────────────────────────────────
    # rule OUT the deploy (last deploy was 3 days ago; the error signature is NEW),
    # confirm the flag (signature only in the flag-gated branch; bulk carts only).
    err_fact = fid(ERRSIG, "count", T_INV)
    p50_fact = fid(SVC, "red_latency_p50", T_INV)
    investigate_confirm = phase("investigate",
        calls=[call("appd_bt_metrics", bt="/cart/checkout", window="10m"),
               call("git_blame", sha="c3d4e5f", file="services/cart-api/src/tax/engine.py",
                    line=142),
               call("list_related_incidents", cmdb_ci="cart-api")],
        ops=[
            # the error signature: TaxEngineException, only in the flag-gated branch,
            # first_seen at flag-flip time, surging on bulk carts.
            node(NT.ERROR_SIGNATURE, signature_hash="taxengine-bulk-cart",
                 exception_class="TaxEngineException", first_seen=T_FLAG,
                 file_line="services/cart-api/src/tax/engine.py:142"),
            fact(ERRSIG, "count", 312, T_INV, source=S.SPLUNK, reliability=0.98),
            fact(ERRSIG, "last_seen", T_INV.isoformat(), T_INV, source=S.SPLUNK, reliability=0.98),
            fact(FLAG, "enabled", True, T_INV, source=S.SERVICENOW, reliability=0.99),
            fact(FLAG, "rollout_percentage", 100, T_INV, source=S.SERVICENOW, reliability=0.99),
            # the service's own compute path is unchanged — p50 flat
            fact(SVC, "red_latency_p50", 73, T_INV, unit="ms", source=S.APPD, reliability=0.95),
            edge(ET.EMITTED, SVC, ERRSIG),
            edge(ET.CAUSED_BY, H1, FLAG, level="high"),
            # rule out the deploy: the last build predates onset by 3 days, and the error
            # signature is NEW (first_seen at flag-flip time)
            update("h2", status="refuted", add_refuting=[p50_fact],
                   basis="last deploy c3d4e5f shipped 3 days ago with zero errors; the error "
                   "signature first_seen at flag-flip time — not a deploy regression"),
            update("h1", status="supported", level="high", add_supporting=[err_fact],
                   basis="TaxEngineException at engine.py:142, first_seen at flag-flip time, "
                   "312 occurrences since onset; the signature lives only in the new-tax-engine "
                   "branch gated by the flag"),
        ],
        narrative="Ruled out the deploy (c3d4e5f is 3 days old, signature is new). Confirmed "
                  "the flag: TaxEngineException at engine.py:142, first_seen at the flip, "
                  "312 occurrences on bulk carts — the signature exists only behind the flag.")

    # ── ACT (human-gated) ────────────────────────────────────────────────────────
    act = phase("act",
        ops=[
            update("h1", level="high",
                   basis="proposed fix: recycle new-tax-engine to 0% rollout (disable the flag)"),
        ],
        narrative="Safest reversible fix: flip new-tax-engine back to 0% rollout. Awaiting "
                  "approval (gated).")

    # ── VERIFY ───────────────────────────────────────────────────────────────────
    verify = phase("verify",
        ops=[
            fact(FLAG, "enabled", False, T_FIX, source=S.SERVICENOW, reliability=0.99),
            fact(FLAG, "rollout_percentage", 0, T_FIX, source=S.SERVICENOW, reliability=0.99),
            fact(SVC, "red_errors", 0.003, T_FIX, source=S.PROMETHEUS, reliability=0.98),
            fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
            event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
            event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
            update("h1", status="confirmed", level="high",
                   basis="flag recycled to 0%: 5xx back to 0.3% — recovery confirms the "
                   "flag-gated path as root cause"),
        ],
        narrative="Post-recycle: flag at 0%, 5xx back to 0.3%. Root cause confirmed.")

    # ── CLOSE ────────────────────────────────────────────────────────────────────
    close = phase("close", ops=[],
        narrative="Postmortem: CHG-77 flipped new-tax-engine to 100%, gating a tax path that "
                  "threw TaxEngineException on bulk carts (>5 items) at engine.py:142; "
                  "recycling the flag to 0% resolved it. Deploy ruled out — the cause was a "
                  "flag flip, not a release.")

    script = [frame, investigate_open, investigate_confirm, act, verify, close]

    # ── fixtures: what the capability calls resolve to ────────────────────────────
    fixtures = {
        "appd": {
            "bt_metrics": [
                {"predicate": "red_errors", "value": 0.34, "unit": "ratio", "at": T_ONSET,
                 "reliability": 0.95},
                {"predicate": "red_latency_p50", "value": 73, "unit": "ms", "at": T_INV,
                 "reliability": 0.95},
            ],
            "snapshots": [
                {"errors": [{"exception": "TaxEngineException", "file_line":
                             "services/cart-api/src/tax/engine.py:142"}]},
            ],
        },
        "git": {
            "log": [
                {"sha": "c3d4e5f", "date": _t(-4320).isoformat(),  # 3 days ago
                 "message": "cart-api: routine tax-table refresh"},
            ],
            "blame": [
                {"sha": "c3d4e5f", "file": "services/cart-api/src/tax/engine.py",
                 "line": 142, "source_line": "if items.size > 5: raise TaxEngineException()"},
            ],
        },
        "list_related_incidents": {
            "primary_incident": "INC-5600",
            "related_incidents": [
                {"number": "INC-4988", "priority": "3 - Moderate", "opened_at": _t(-2880),
                 "cmdb_ci": "pricing-api", "confidence": "high"},
            ],
        },
    }

    return subject, script, fixtures
