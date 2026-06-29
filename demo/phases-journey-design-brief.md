# Design brief — redesign the "Phases & Journey" panel · The Investigation Workbench

## Product context
The **Investigation Workbench** is a **governed-autonomy** tool for running production investigations (incident triage is the first use case; it also handles data-quality issues and other production problems). It is **human-in-the-loop**: an AI agent does the investigative legwork — queries tools, builds a live knowledge graph, proposes fixes — but a human operator **approves every plan and every write**; nothing changes without approval.

Operators work in a 3-column **console**:
- **LEFT — "Phases & Journey"** (~300px rail) ← **this is what you are designing**
- **CENTER — "Conversation"**: the agent's step-by-step work as a chat the operator reads and approves
- **RIGHT — "Investigation"**: the live knowledge graph + a per-phase "record" (full audit detail)

Every investigation runs through **4 phases**: **Assess → Root cause → Remediation (gated write) → Verify & close.**

## What this panel is FOR (the real requirement — design from this, not from a field list)
On **first look**, the operator must instantly grasp the **whole investigation**: the 4-phase journey, **where we are right now** (which phase is active / gated / closed), and the **gist of what has happened** — situational awareness in ~2 seconds, because this is a decision surface and the human must orient fast.

Then, via **progressive disclosure**, the operator drills into any phase for depth. Do **not** dump everything at once; lead with the at-a-glance journey, reveal detail on demand.

(Context: a prior attempt failed because it put a per-phase field-dump inside an accordion and lost the "whole journey at a glance." Please design from the panel's purpose.)

## Per-phase detail (revealed progressively, all read from data — nothing invented)
1. **Goal** — what the phase is for (from the playbook)
2. **Capabilities available** — the tools/data the phase may use (read vs write)
3. **Current summary** — what happened in this phase, this run
4. **Result** — the phase's structured output/finding
…plus status (done/active/pending), step count, and any gate awaiting the operator.

## Data model (render ONLY from this — the design must be data-driven)
**playbook.json** (the process; identical for every incident):
`{ phases: [{ id, label, goal, effect: "read-only"|"write", gate: bool, intents: [string] }] }`
— the 4 phases, each with its goal and the intents (needs) it pursues; remediation is the gated write phase.

**capabilities.json** (the tools):
`{ capabilities: [{ id, label, intents: [string], effect: "read-only"|"write", what }] }`
— **a phase's available capabilities = the capabilities whose `intents` intersect that phase's `intents`** (e.g. Assess → ServiceNow + Datadog + GitHub, all read; Remediation → Runbook + PagerDuty, both write).

**incident.json** (one run):
`{ incident, title, status: "done"|"waiting_approval"|…, current_phase, pending_gate: {kind}|null, closed_by, closed_at,
   phases: [{ phase, state: "done"|"active"|"pending", summary, steps: [...], output: {…} }] }`
— the `output` shape **varies per phase**:
  - AssessResult `{ incident_type, symptom, affected[], impact, changed[], ruled_out[], related[] }`
  - RootCauseResult `{ candidates: [{ cause, node, confidence: { value, basis } }], selected, ruled_out[] }`
  - RemediationResult `{ actions: [{ kind, technique, blast_radius, approval, result }] }`
  - VerifyResult `{ recovered, before_after, resolution, follow_ups[] }`
  → **the result renderer must be generic** (handle any shape) — do not hard-code per-phase templates.

## Constraints
- **Data-driven only** — every label/value comes from the three files above; invent no copy.
- Fits a **~300px left rail** inside the existing console; it coexists with the center conversation + the right graph/record panes (so do **not** reproduce the full step list here).
- Match the existing visual language: light, clean operator-SaaS; system fonts; soft white cards on `#f9fafc`; blue accent `#2557a7`; health colors (healthy = green, impacted = amber, unhealthy = red, ruled-out = grey, related = purple).
- Core requirement, restated: **first look = the whole journey + current state; progressive disclosure = per-phase depth.**

## Deliverable
Design the redesigned **"Phases & Journey"** panel as a **self-contained HTML/CSS artifact** that renders from the data model above. Propose the layout and the progressive-disclosure interaction. Show it in **two states**: (a) a **closed** incident (all 4 phases done) and (b) an **in-progress, gated** incident (one phase active, awaiting the operator's approval). Include realistic inline sample data that matches the schemas.
