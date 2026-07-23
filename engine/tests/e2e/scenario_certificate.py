"""Scenario — CERTIFICATE expiry root cause (DESIGN §2.5 R-K3 layer: TLS / certificate).

auth-svc starts returning INTERMITTENT 503s — only a subset of clients fail, depending on
whether their TLS stack validates the full chain (modern clients pin the intermediate;
older JDK builds that trust the leaf directly still succeed). Differential diagnosis rules
OUT a service-side outage (pods Ready, no code change, no deploy) and confirms an
EXPIRING INTERMEDIATE CA certificate: the cross-signed intermediate lapsed at 00:00 UTC,
so any client that builds the chain through it now fails handshake with
PKIX path building failed. Discriminator: the failure is PARTIAL (only ~40% of clients,
those validating the intermediate) and intermittent by client/SNI — not a total outage.
Fix: renew/re-push the intermediate cert on the auth-svc TLS secret. Teaches that
cert-based failures are partial + client-dependent, a classic discriminator from code/
infra faults. The scripted planner drives the REAL engine through the 5-phase algebra
(6 steps — the investigate loop runs twice), exercising appd, prometheus, servicenow and
splunk via CapabilityCall + fixtures.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.enums import EdgeType as ET
from iw_engine.domain.enums import NodeType as NT
from iw_engine.domain.enums import Source as S
from iw_engine.domain.subject import SubjectRef

from ._helpers import call, edge, event, fact, fid, hid, nid, node, phase, propose, span, update


def _t(minutes: int) -> datetime:
    return datetime(2026, 7, 19, 14, 30, tzinfo=UTC) + timedelta(minutes=minutes)


T_EXPIRY = _t(-30)    # 14:00 intermediate cert expired (00:00 UTC ≈ 14:00 local-ish for the window)
T_ONSET = _t(0)       # 14:30 first 503s surface (enough modern clients hit it)
T_INV = _t(15)        # 14:45 investigation
T_FIX = _t(40)        # 15:10 cert renewed/re-pushed, recovery confirmed

SVC = nid(NT.SERVICE, service_name="auth-svc", env="prod")
EP = nid(NT.API_ENDPOINT, service_name="auth-svc", env="prod", method="POST",
         route_template="/oauth/token")
CERT = nid(NT.CERTIFICATE, cert_id="auth-tls-intermediate",
           subject="auth-svc.internal", issuer="Corp Intermediate CA")
ANOM = nid(NT.ANOMALY, anomaly_id="ANOM-1")
ALERT = nid(NT.ALERT, alert_id="ALT-1")
INC = nid(NT.INCIDENT, incident_id="INC-5700")
ERRSIG = nid(NT.ERROR_SIGNATURE, signature_hash="pkix-path-building-failed")
H1, H2 = hid("h1"), hid("h2")


def build():
    """Returns (subject, script, fixtures) for the CERTIFICATE-expiry root-cause scenario."""
    subject = SubjectRef(domain="app-incident", id="INC-5700", kind="incident")

    # ── FRAME ────────────────────────────────────────────────────────────────────
    frame = phase("frame",
        calls=[call("find_recent_changes", ci="auth-svc", window="30m"),
               call("active_alerts", service="auth-svc", env="prod")],
        ops=[
            node(NT.ANOMALY, anomaly_id="ANOM-1"),
            node(NT.SERVICE, service_name="auth-svc", env="prod",
                 owner="identity-platform@corp.example", version="v5.0.2"),
            node(NT.CERTIFICATE, cert_id="auth-tls-intermediate",
                 subject="auth-svc.internal", issuer="Corp Intermediate CA",
                 serial="4A:9F:2C:11:88:0E:73:BD", key_algorithm="RSA-2048",
                 sig_algorithm="SHA256withRSA", not_after="2026-07-19T14:00:00+00:00",
                 san="auth-svc.internal,auth.corp.example"),
            node(NT.ALERT, alert_id="ALT-1"),
            # onset RED snapshot: errors present but PARTIAL (~40% — only chain-validating
            # clients fail); p99 elevated only for failing clients; throughput roughly normal.
            # The partial / client-dependent shape is the discriminator from a total outage.
            fact(ANOM, "onset_value", 0.40, T_ONSET, source=S.PROMETHEUS),
            fact(ANOM, "severity_score", 2, T_ONSET, source=S.SERVICENOW),
            fact(SVC, "degraded", True, T_ONSET, source=S.PROMETHEUS),
            fact(SVC, "red_errors", 0.40, T_ONSET, source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_rate", 1100, T_ONSET, unit="rpm", source=S.PROMETHEUS, reliability=0.97),
            fact(SVC, "red_latency_p50", 48, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "red_latency_p99", 1900, T_ONSET, unit="ms", source=S.APPD, reliability=0.95),
            fact(SVC, "tier", "tier-1", T_ONSET, source=S.SERVICENOW),
            fact(SVC, "slo_target", 0.999, T_ONSET, source=S.SERVICENOW),
            # the cert is near/past expiry — the seed of the hypothesis
            fact(CERT, "days_to_expiry", 0, T_ONSET, source=S.ARTIFACTORY, reliability=0.99),
            event(SVC, "degraded_started", T_ONSET, source=S.PROMETHEUS),
            event(ALERT, "fired", T_ONSET, source=S.PROMETHEUS),
            # a captured distributed trace at onset — the SPAN species (§2.6): a bounded happening SVC is in
            span(SVC, "trace", T_ONSET, ended_at=T_ONSET + timedelta(milliseconds=520),
                 correlation_id="trace-auth-3f9d", value={"error": True}, reliability=0.9),
            edge(ET.AFFECTS, ANOM, SVC),
            edge(ET.FIRED_ON, ALERT, SVC),
        ],
        narrative="auth-svc returning 40% 5xx — but only for a subset of clients. No deploy "
                  "or code change in the 30m window. The TLS intermediate cert shows 0 days "
                  "to expiry. The partial/client-dependent failure shape points at TLS, not "
                  "a total outage.")

    # ── scope/impact framing folded into FRAME (the retired TRIAGE — P7 5-phase algebra) ──
    frame = frame.model_copy(update={"ops": [*frame.ops,
        node(NT.INCIDENT, incident_id="INC-5700",
             title="auth-svc intermittent 503s",
             short_description="auth-svc 503s from TLS handshake fails; cert expiring",
             description="High5xxRate fired for auth-svc (prod, tier-1) at 14:30 UTC. ~40% of "
                         "requests return 503 with PKIX path-building failures on the TLS "
                         "handshake — a partial failure that points at an expiring cert rather "
                         "than a total outage. The auth-tls-intermediate certificate (Corp "
                         "Intermediate CA) reached not_after at 14:00; handshake errors began "
                         "exactly then. No code or config change in the window.",
             work_notes="High5xxRate; PKIX path-building failures. Cert expiry suspected.",
             caller_id="monitoring.alerting"),
        node(NT.API_ENDPOINT, service_name="auth-svc", env="prod", method="POST",
             route_template="/oauth/token"),
        # the token endpoint's status-code distribution: 40% 5xx, the rest 200 — the
        # partial failure that narrows the suspect set to the TLS handshake path.
        fact(EP, "status_code_dist", {"500": 0.40, "200": 0.58, "4xx": 0.02}, T_ONSET,
             source=S.APPD, reliability=0.95),
        edge(ET.AFFECTS, INC, SVC),
        edge(ET.EXPOSES, SVC, EP, origin="declared"),
        event(INC, "declared", T_ONSET, source=S.SERVICENOW),
    ], "calls": [*frame.calls, call("appd_bt_metrics", bt="/oauth/token", window="10m")],
       "narrative": frame.narrative + " Declared SEV2. /oauth/token is the failing path, "
       "but only ~40% of clients — the ones validating the full chain. The cert's 0 "
       "days-to-expiry is the prime suspect; confirm via the actual handshake errors "
       "before touching the secret."})

    # ── INVESTIGATE opens the hypothesize⇄evidence loop ─────────────────────────
    investigate_open = phase("investigate",
        calls=[call("list_related_incidents", cmdb_ci="auth-svc")],
        status="repeat",
        ops=[
            node(NT.CERTIFICATE, cert_id="auth-tls-intermediate",
                 subject="auth-svc.internal", issuer="Corp Intermediate CA"),
            # related prior: billing-svc hit the exact same intermediate-expiry shape last
            # year — a hypothesis prior that sharpens H1.
            node(NT.INCIDENT, incident_id="INC-3344", severity="3 - Moderate"),
            edge(ET.SIMILAR_TO, INC, nid(NT.INCIDENT, incident_id="INC-3344"), level="high"),
            propose("h1",
                    "the Corp Intermediate CA cert expired, breaking the TLS chain for "
                    "clients that validate the intermediate (~40%)",
                    "med", root=CERT),
            propose("h2", "auth-svc service-side outage (pod crash / resource exhaustion)",
                    "low", root=SVC),
        ],
        narrative="The cert (H1) is the prime suspect — 0 days to expiry + partial failures on "
                  "chain-validating clients. The outage hypothesis (H2) is weak: pods are Ready "
                  "and ~58% of clients succeed. billing-svc filed the same intermediate-expiry "
                  "shape last year — a related prior reinforcing H1.")

    # ── the loop.s confirm turn ─────────────────────────────────────────────────
    # rule OUT the service outage (pods Ready, no restarts, error signature is TLS-side),
    # confirm the cert (PKIX path building failed, only on failing clients; cert expired).
    err_fact = fid(ERRSIG, "count", T_INV)
    expiry_fact = fid(CERT, "days_to_expiry", T_INV)
    p50_fact = fid(SVC, "red_latency_p50", T_INV)
    investigate_confirm = phase("investigate",
        calls=[call("appd_bt_metrics", bt="/oauth/token", window="10m"),
               call("instant_query", query="cert_expiry_days auth-tls-intermediate"),
               call("list_related_incidents", cmdb_ci="auth-svc")],
        ops=[
            # the handshake error signature: PKIX path building failed — a TLS-chain error,
            # not an application error. Only present on the failing (chain-validating) clients.
            node(NT.ERROR_SIGNATURE, signature_hash="pkix-path-building-failed",
                 exception_class="SSLHandshakeException", first_seen=T_EXPIRY,
                 file_line="sun.security.validator.Validator"),
            fact(ERRSIG, "count", 8840, T_INV, source=S.SPLUNK, reliability=0.98),
            fact(ERRSIG, "last_seen", T_INV.isoformat(), T_INV, source=S.SPLUNK, reliability=0.98),
            # the cert is expired (days_to_expiry <= 0)
            fact(CERT, "days_to_expiry", -1, T_INV, source=S.ARTIFACTORY, reliability=0.99),
            event(CERT, "expired", T_EXPIRY, source=S.ARTIFACTORY),
            # the service is healthy — pods up, p50 flat, no restart surge
            fact(SVC, "red_latency_p50", 51, T_INV, unit="ms", source=S.APPD, reliability=0.95),
            edge(ET.EMITTED, SVC, ERRSIG),
            edge(ET.CAUSED_BY, H1, CERT, level="high"),
            # rule out the service outage: pods healthy, p50 flat, the error is TLS-side
            update("h2", status="refuted", add_refuting=[p50_fact],
                   basis="pods Ready with no restart surge, p50 flat at 51ms — the service is "
                   "healthy; the error signature is SSLHandshakeException (TLS-side), not an "
                   "app/runtime fault"),
            update("h1", status="supported", level="high",
                   add_supporting=[err_fact, expiry_fact],
                   basis="cert at -1 days to expiry, expired event at 14:00; SSLHandshakeException "
                   "(PKIX path building failed) on 8,840 failing-client calls — the intermediate "
                   "lapsed so chain-validating clients can no longer build the trust path"),
        ],
        narrative="Ruled out the service outage (pods Ready, p50 flat, TLS-side error). Confirmed "
                  "the cert: -1 day to expiry, expired at 14:00, 8,840 PKIX-path-building failures "
                  "on chain-validating clients — the intermediate lapsed.")

    # ── ACT (human-gated) ────────────────────────────────────────────────────────
    act = phase("act",
        ops=[
            update("h1", level="high",
                   basis="proposed fix: renew the Corp Intermediate CA cert and re-push the "
                   "auth-svc TLS secret (redeploy the rollout that mounts it)"),
        ],
        narrative="Safest reversible fix: renew the intermediate cert + re-push the auth-svc "
                  "TLS secret. Awaiting approval (gated).")

    # ── VERIFY ───────────────────────────────────────────────────────────────────
    verify = phase("verify",
        ops=[
            fact(CERT, "days_to_expiry", 90, T_FIX, source=S.ARTIFACTORY, reliability=0.99),
            event(CERT, "renewed", T_FIX, source=S.ARTIFACTORY),
            fact(SVC, "red_errors", 0.002, T_FIX, source=S.PROMETHEUS, reliability=0.98),
            fact(SVC, "degraded", False, T_FIX, source=S.PROMETHEUS),
            event(SVC, "degraded_cleared", T_FIX, source=S.PROMETHEUS),
            event(ANOM, "cleared", T_FIX, source=S.PROMETHEUS),
            update("h1", status="confirmed", level="high",
                   basis="cert renewed (90 days to expiry), 5xx back to 0.2% — recovery "
                   "confirms the expired intermediate as root cause"),
        ],
        narrative="Post-renewal: cert at 90 days, 5xx back to 0.2%. Root cause confirmed.")

    # ── CLOSE ────────────────────────────────────────────────────────────────────
    close = phase("close", ops=[],
        narrative="Postmortem: the Corp Intermediate CA cert expired at 14:00, breaking the "
                  "TLS chain for chain-validating clients (~40%); renewing + re-pushing the "
                  "cert resolved it. Service outage ruled out — the failure was partial and "
                  "client-dependent, the cert-expiry signature.")

    script = [frame, investigate_open, investigate_confirm, act, verify, close]

    # ── fixtures: what the capability calls resolve to ────────────────────────────
    fixtures = {
        "appd": {
            "bt_metrics": [
                {"predicate": "red_errors", "value": 0.40, "unit": "ratio", "at": T_ONSET,
                 "reliability": 0.95},
                {"predicate": "red_latency_p50", "value": 51, "unit": "ms", "at": T_INV,
                 "reliability": 0.95},
            ],
            "snapshots": [
                {"errors": [{"exception": "SSLHandshakeException",
                             "message": "PKIX path building failed",
                             "client": "modern-jdk17"}]},
            ],
        },
        "instant_query": {
            "metrics": [
                {"subject": CERT, "predicate": "days_to_expiry", "value": -1,
                 "unit": "days", "at": T_INV, "reliability": 0.99},
            ],
        },
        "list_related_incidents": {
            "primary_incident": "INC-5700",
            "related_incidents": [
                {"number": "INC-3344", "priority": "3 - Moderate", "opened_at": _t(-525600),
                 "cmdb_ci": "billing-svc", "confidence": "high",
                 "title": "billing-svc TLS handshake errors (last year)",
                 "short_description": "Same Corp Intermediate CA chain expiry; caught early"},
            ],
        },
    }

    return subject, script, fixtures
