"""INC-4821 — the worked example, verbatim from ../../design/v2/04-data-model.html.

These are the design's exact JSON shapes. The domain models must parse every one of them with
`extra="forbid"`, which proves the model is field-complete vs the design (a dropped/renamed field
would fail the parse).
"""

SUBJECT = {"domain": "app-incident", "id": "INC-4821", "kind": "incident"}

FACT = {
    "key": "health", "value": "degraded", "source": "appd",
    "evidence_ref": "appd://payments-api/bt?t=14:02", "observed_at": "2026-06-18T14:02Z",
    "confidence": 0.9, "impact_state": "degraded",
}

NODE = {
    "id": "app:payments-api", "kind": "system", "type": "app", "layer": "app",
    "name": "Payments API", "labels": ["pci", "tier-0", "suspect"],
    "props": {"owner_team": "payments-sre", "on_call": "j.rivera"},
    "facts": [FACT],
    "summary": "degraded since 14:02; DB-backed latency",
    "sources": ["servicenow-cmdb", "appd"],
}

EDGE = {
    "type": "suspected_cause", "from": "INC-4821", "to": "stor:pay-vol",
    "props": {
        "confidence": {"value": 0.9, "basis": "evidence-complete + matches INC-4820"},
        "rank": 1,
        "path": ["app:payments-api", "db:payments-ora", "stor:pay-vol"],
    },
    "sources": ["otel", "netapp"],
}

STEP_REASONING = {
    "seq": 1, "at": "14:04:10", "kind": "reasoning",
    "note": "Assess flagged the DB backend slow — walk below the app, not the app code",
}
STEP_TOOL_CALL = {
    "seq": 2, "at": "14:04:30", "kind": "tool_call", "capability": "traces",
    "input": {"trace": "checkout"}, "result": {"db_span_ms": 3140, "pct_of_total": "75%"},
    "touched": ["svc:checkout", "db:payments-ora"], "evidence": ["otel://trace/9af3"],
    "note": "75% of latency is the DB call — not app code",
}

PHASE_RECORD = {
    "id": "INC-4821:root-cause:1", "subject": SUBJECT, "phase": "root-cause",
    "goal": "Find the most probable cause — ranked, with evidence.", "state": "done",
    "plan": "Walk below the app; correlate vs rev47; rank with a path.",
    "steps": [STEP_REASONING, STEP_TOOL_CALL],
    "summary": "Cause = pay-vol disk failure (storage), conf 0.9.",
    "opened_at": "14:04", "closed_at": "14:07",
}

ASSESS_RESULT = {
    "incident_type": "performance",
    "symptom": "checkout p99 0.4s→4.2s, errors 18%",
    "affected": ["app:payments-api", "biz:checkout-journey"],
    "impact_assessment": {
        "scope": "customer-facing", "blast_radius": ["app:payments-api"], "bounded_by": None,
        "severity": "P1", "urgency": "high",
        "business_impact": "checkout payments degraded; revenue path",
    },
    "changed": ["chg:deploy-rev47"],
    "time_factor": None,
    "suspected_locus": {"node": "db:payments-ora", "why": "DB span dominates"},
    "related": ["INC-4820"], "cluster": None,
    "suggestions": [
        {"possible_fix": "failover DB to standby", "basis": "similar_incident", "confidence": 0.7}
    ],
    "owner": "payments-sre",
}

ROOT_CAUSE_RESULT = {
    "candidates": [{
        "cause": "pay-vol disk failure → RAID rebuild → DB I/O", "node": "stor:pay-vol",
        "confidence": {"value": 0.9, "basis": "evidence-complete + matches INC-4820"},
        "rank": 1, "path": ["app:payments-api", "db:payments-ora", "stor:pay-vol"],
        "evidence": ["otel://trace/9af3", "netapp://aggr01/disk/1.4.7"],
        "recommended_fix": "failover DB to standby",
    }],
    "selected": 0,
    "ruled_out": [{"hyp": "rev47 deploy", "evidence": "trace shows DB cost, not app code"}],
    "status": "confident",
}

REMEDIATION_RESULT = {
    "actions": [{
        "action_id": "a1", "kind": "mitigate", "technique": "failover", "target": "db:payments-ora",
        "expected_effect": "DB served by standby; I/O normal", "blast_radius": "payments read path",
        "rollback": "fail back once disk replaced", "temporary": True, "revert_when": "incident_close",
        "idempotency_key": "INC-4821-a1", "gated": True,
        "approval": {"decision": "approve", "actor": "j.rivera", "at": "14:21"},
        "result": "applied; I/O 28ms→4ms", "status": "done",
    }],
    "followups": [{"detail": "replace disk 1.4.7", "basis": "escalate", "owner": "storage"}],
    "status": "applied",
}

VERIFY_RESULT = {
    "recovered": True,
    "before_after": "p99 4.2s→260ms, errors 18%→0.2%",
    "watch_window": "15m", "recovery_confidence": 0.95,
    "resolution": "failover to standby; durable fix = disk replacement",
    "temporary_actions_status": [{"action_id": "a1", "status": "scheduled"}],
    "residual": [{"item": "replace disk", "owner": "storage"}],
    "closed_by": "j.rivera", "status": "closed",
}

FEEDBACK = {
    "_id": "fb-9912", "subject": SUBJECT, "run_id": "INC-4821:run:1", "actor": "j.rivera",
    "kind": "outcome", "verdict": "fix held; good call on the failover",
    "note": "disk replaced next morning, no recurrence", "at": "2026-06-19T09:00Z",
}

PLAYBOOK = {
    "id": "incident-triage", "version": "1.0.0", "domain": "app-incident", "status": "active",
    "owner": "sre-platform",
    "phases": [
        {"id": "assess", "effect": "read-only", "output": "AssessResult",
         "goal": "Know what's affected", "needs": ["incident-source", "topology",
                                                    "change-history", "telemetry", "similar-incidents"]},
        {"id": "root-cause", "effect": "read-only", "output": "RootCauseResult",
         "min_confidence": 0.7, "goal": "Find the cause",
         "needs": ["metrics", "logs", "traces", "topology", "change-history", "layer-deep-dive"]},
        {"id": "remediation", "effect": "write", "gate_writes": True, "output": "RemediationResult",
         "goal": "Apply the safest fix", "needs": ["remediation-action", "escalation"]},
        {"id": "verify-close", "effect": "read-only", "output": "VerifyResult",
         "goal": "Confirm recovery", "needs": ["telemetry", "synthetic-replay"]},
    ],
    "defaults": {"on_failure": "run-remaining", "retry": {"max": 3, "backoff": "exponential"}},
    "unknown_access": "ask",
    "error_handler": {"action": "escalate", "to": "on-call", "via": "pagerduty"},
    "changelog": "1.0.0 — initial graph-based playbook",
}

PROVIDER = {
    "id": "appd", "name": "AppDynamics", "kind": "mcp_remote", "connection": {"url": "…"},
    "trusted": True, "status": "connected", "last_synced": "2026-06-18T09:00Z",
}
DECLARED_CAPABILITY = {
    "id": "appd__get_health", "provider": "appd", "description": "Health of an app / tier / node",
    "input_schema": {}, "output_schema": {}, "effect_hint": "read",
}
CAPABILITY_POLICY = {
    "capability_id": "bladelogic__restart_service", "effect": "write", "access": "ask",
    "status": "active", "reviewed_by": "sre-lead", "reviewed_at": "2026-06-10",
}
