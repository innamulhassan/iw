---
id: incident-triage
version: 1.0.0
domain: app-incident
status: active
owner: sre-platform
unknown_access: ask
defaults:
  on_failure: run-remaining
  retry: { max: 3, backoff: exponential }
error_handler: { action: escalate, to: on-call, via: pagerduty }
phases:
  - id: assess
    effect: read-only
    output: AssessResult
    goal: "Know what's affected, how bad, what changed — surface impact + early suggestions."
    needs: [incident-source, topology, change-history, telemetry, similar-incidents]
  - id: root-cause
    effect: read-only
    output: RootCauseResult
    min_confidence: 0.7
    goal: "Find the most probable cause — ranked, with evidence; pivot if the health-walk dead-ends."
    needs: [metrics, logs, traces, topology, change-history, layer-deep-dive]
  - id: remediation
    effect: write
    gate_writes: true
    output: RemediationResult
    goal: "Apply the safest fix — reverse, mitigate, or escalate; stop the bleeding."
    needs: [remediation-action, escalation]
  - id: verify-close
    effect: read-only
    output: VerifyResult
    goal: "Confirm recovery from the user's side; revert temporaries; a human closes."
    needs: [telemetry, synthetic-replay]
graph_schema:
  node_types:
    system: [app, database, network, storage, compute, business, location, external]
    incident: [incident]
    change: [change]
    alert: [alert]
  edges: [depends_on, hosted_on, runs_on, affects, suspected_cause, related_to, member_of, observed_on]
  facts: [health, error_rate, latency, sessions, deadline, io_wait_ms, latency_ms]
  labels: [suspect, policy_block, central, impacted, pci, tier-0]
---

## assess
Know what's affected, how bad, what changed. Resolve the subject from the incident source, walk
topology to the affected systems, pull recent changes + telemetry, and surface similar incidents.

## root-cause
Walk below the symptom along `depends_on`; overlay health; correlate against recent changes; rule
out; rank candidates with a causal `path`. Loop while top confidence `< min_confidence`.

## remediation
Apply the safest fix — reverse, mitigate, or escalate. Every write is gated; each action carries
`expected_effect`, `blast_radius`, `rollback`, and `temporary`/`revert_when`.

## verify-close
Confirm recovery from the user's side (before/after + synthetic replay); ensure every temporary
action is reverted or scheduled; a human performs the final close.
