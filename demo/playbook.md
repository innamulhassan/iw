# Demo Playbook — Incident Triage (the first use case)

> The phases the **engine** walks for one investigation. In this demo the "engine" is
> **VS Code Copilot / Claude** (see `copilot-instructions.md`). Each phase has a **goal**,
> the **questions** to answer, the capability **intents** it may use (each resolves to a
> real tool URL in `capabilities.json`), an **effect** (`read-only` | `write`), and whether
> it is **gated** (a human must approve before any write).

```
id: incident-triage
version: demo-1.0
domain: app-incident
```

## Human checkpoints — the operator approves at every boundary
The agent **never skips these**:
1. **End-of-phase checkpoint (EVERY phase, including read-only ones).** When a phase finishes, the agent **presents its findings** — *what is impacted · the impact · the phase output* — and **waits for the operator to approve before advancing** to the next phase. The operator can approve, or send it back to dig deeper.
2. **Write gate (Remediation only).** Before any production change, the agent proposes the specific action and waits for **approve · refine · deny** (Phase 3).
3. **Browser launch.** The agent **asks before opening a tool** in the browser; the operator logs in.

---

## Phase 1 — assess  ·  effect: read-only
- **Goal.** What's affected, how bad, what changed — surface impact + early suggestions.
- **Questions.** Which service(s)? How severe (users · error-rate · latency)? What changed recently? Any similar past incidents?
- **Capability intents.** `incident-source` · `topology` · `telemetry` · `change-history` · `similar-incidents`
- **Builds in the graph.** the incident node (the subject) + the affected service(s) + their 1-hop neighbours; health facts on the symptom.
- **Output — `AssessResult`.** `{ incident_type, symptom, affected[], impact, changed[], suggestions[] }`
- **Gate.** none (read-only).

## Phase 2 — root-cause  ·  effect: read-only  ·  min_confidence 0.7
- **Goal.** The most probable cause — ranked, with evidence and a stated basis.
- **Questions.** Walking the dependency graph from the victim, where does the signal lead? Can the recent change be ruled out? Is the cause a node, a change, a zone, or a time factor?
- **Capability intents.** `metrics` · `logs` · `traces` · `topology` · `change-history`
- **Builds in the graph.** deeper nodes (db · storage · network) + facts (latency · error_rate) + a `suspected_cause` edge with a confidence + the cause path.
- **Output — `RootCauseResult`.** `{ candidates[], selected, ruled_out[], confidence{ value, basis } }`
- **Gate.** none (read-only).

## Phase 3 — remediation  ·  effect: write  ·  ⛔ GATED
- **Goal.** The safest fix — reverse, mitigate, or escalate. Stop the bleeding.
- **Questions.** What's the safest action? Expected effect? Blast radius? Rollback? Temporary (with a `revert_when`)?
- **Capability intents.** `remediation-action` · `escalation`
- **Behaviour.** **PROPOSE** the action in chat — do **not** act. Wait for the human's **approve · refine · deny**. Record the decision. Only on **approve** perform the (browser) action.
- **Output — `RemediationResult`.** `{ actions[]{ kind, technique, expected_effect, blast_radius, rollback, approval, result } }`
- **Gate.** **REQUIRED** — a human approves every write.

## Phase 4 — verify-close  ·  effect: read-only
- **Goal.** Confirm recovery from the user's side; revert temporaries; a human closes.
- **Questions.** Did the symptom recover (before/after)? Any temporary action to revert? What actually fixed it (the resolution the next incident learns from)?
- **Capability intents.** `telemetry` · `synthetic-replay`
- **Output — `VerifyResult`.** `{ recovered, before_after, resolution, temporary_actions_status[] }`
- **Gate.** a human **closes** — the engine never auto-closes.

## Graph schema — the type system  ·  machine-readable: `graph-schema.json`
The engine builds the investigation graph **only** from these declared types — **it is not free-form, and types are never invented inline**:
- **node kinds** — `incident · cluster · app · service · database · storage · compute · network · external · change · alert`
- **edge types** — `depends_on · hosted_on · runs_on · routes_to · located_in · changed · affects · suspected_cause · related_to · member_of · observed_on`
- **health states** — `subject · impacted · degraded · unhealthy · healthy · ruled_out · related`

A node's `kind` must be a node-type; an edge's `type` an edge-type; a node's `health` a health-state. **If a type is missing, update `graph-schema.json` + this playbook deliberately — never invent one. If the schema itself is the gap, raise it for discussion.**

---

**Re-enterable.** If verify-close shows recovery didn't hold, backtrack to **root-cause**.
**One engine, many playbooks.** Swap this file for a different investigation (e.g. a data-quality sweep) and the same engine + viewer run it.
