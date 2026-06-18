---
id: incident-triage
version: 1.2.0
domain: app-incident
status: active
owner: iro-sre
changelog:
  - { version: 1.0.0, date: 2026-06-17, by: iro-sre, note: "First published process." }
  - { version: 1.1.0, date: 2026-06-17, by: iro-sre, note: "Added retry + unknown_access defaults." }
  - { version: 1.2.0, date: 2026-06-18, by: iro-sre, note: "Multi-app topology in Assess; closing is human." }

# defaults are inherited by every phase unless the phase overrides them
defaults:
  on_failure: run-remaining                  # finish independent steps, then report (vs fail-immediately)
  retry: { max: 3, backoff: exponential }    # retry TRANSIENT errors only; permanent -> escalate

# this playbook's default gate for capabilities whose effect is unknown
# (a capability's own policy still overrides this; the global matrix is the final fallback)
unknown_access: ask                          # deny | ask | allow

phases:
  - id: assess
    name: Assess
    goal: Identify the affected service(s), how bad it is, what changed, and the context around it.
    capabilities: [servicenow.incident.read, cmdb.read, appd.flowmap.read,
                   slo.read, changelog.read, incidents.search]
    output: AssessResult

  - id: root-cause
    name: Root cause
    goal: Find the probable cause, with evidence and a confidence score (ranked candidates, never one).
    capabilities: [appd.metrics.read, logs.search, traces.read, changelog.read]
    output: RootCauseResult
    min_confidence: 0.7                        # below this -> gather more, or ask the operator

  - id: remediation
    name: Remediation
    goal: Apply the safest reversible fix (often, roll back the change).
    capabilities: [k8s.rollback, k8s.restart, k8s.scale, runbook.run, telemetry.read]
    output: RemediationResult
    gate_writes: true                          # force the approval gate on every write here

  - id: verify-close
    name: Verify & close
    goal: Confirm recovery from the user's side; the operator confirms and closes.
    capabilities: [telemetry.read, synthetic.replay]
    output: VerifyResult

# output contracts, versioned with the playbook (validated on author, publish, and load)
schemas:
  AssessResult:      { type: object, required: [affected_services, impact, priority] }
  RootCauseResult:   { type: object, required: [candidates, status] }
  RemediationResult: { type: object, required: [action, approval, execution] }
  VerifyResult:      { type: object, required: [verification_outcome, resolution] }
---

## assess
Identify the affected service(s) — it may span several apps and their dependencies. Establish
severity / impact + blast radius; pull recent changes; load the topology and check health across
nodes; link related / prior incidents; set the owner and an initial suspected locus. Derive
priority from impact × urgency (read-only, the operator can override).

## root-cause
Walk the topology from the symptom toward the suspected locus. Correlate metrics / traces / logs
with recent changes and unhealthy dependencies. Produce **ranked candidates** with evidence and a
confidence score — never a single asserted cause. Rule out alternatives and record why. If the top
candidate is below `min_confidence`, gather more evidence or ask the operator.

## remediation
Propose the safest reversible fix — usually rolling back the offending change. Show the blast
radius and the rollback plan. Every write is **gated**: the operator approves, refines, or denies
the plan before it runs. Execute with an idempotency key (exactly-once). Retry transient failures
per `retry`; a permanent failure or a blocked action goes to the error handler (escalate / manual).

## verify-close
Re-check health and replay the user journey (synthetic). Confirm the symptom is cleared and the SLO
is recovering; capture before / after. If recovery did not hold, backtrack to root cause with the
new evidence. **Closing the incident is a human action** — the operator confirms resolution and
closes; the engine never auto-closes.
