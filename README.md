# The Investigation Workbench

A **governed, human-in-the-loop investigation engine**. One domain-neutral engine runs a
versioned **playbook** through gated phases, building a **typed knowledge graph** as it goes,
reasoning over it to rank **evidence-backed hypotheses**, and acting only through abstract,
**mockable capabilities** — with **a human approving every production-changing action** and an
**append-only journal** that is the reconstructable audit trail.

**Incident triage is the first domain.** The engine is deliberately domain-neutral: a second
domain (provisioning, capacity, data-quality) is a new playbook + entity registry, not an
engine change.

> **Core promise (the invariant):** *every production-changing action is human-approved,
> reversible, and reconstructable back to its evidence.*

## Layout

| Path | What |
|---|---|
| **`engine/`** | The Python core — the typed domain registry, the graph/journal/ledger projections, the thin phased engine, and 8 mockable capability adapters. See [`engine/docs/DESIGN.md`](engine/docs/DESIGN.md). |
| **`workbench/`** | The React + TypeScript UI — renders an investigation's graph, hypothesis ledger, journal timeline, and phase/gate state. |
| **`design/`** | Product design docs (the v2 PRD + data model). |
| **`iw.py`** | One cross-platform controller script — `init` / `start` / `stop` / `status` / `logs` for both services. See [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md). |

## The core ideas (what makes it right)

- **One typed, closed graph model.** Every entity is a node of a *defined type* (service, pod,
  database, code-commit, change, alert, hypothesis, …); relations are edges; static attributes
  are properties; **observed events are facts carrying a timestamp, source, and confidence**.
  The model reasons over the incident domain naturally and is **bi-temporal** (you can
  reconstruct the graph as it was at incident-start).
- **One uniform phase-output contract.** Every phase (Frame → Triage → Hypothesize →
  Investigate → Remediate → Verify → Close) emits the *identical* `PhaseResult`, folded by a
  single function into three projections: the **graph** (a blackboard), the **hypothesis ledger**
  (ranked causal chains, holding both *supporting and refuting* evidence), and the **journal**
  (the append-only source of truth — replaying it rebuilds the other two exactly).
- **The engine orchestrates; the LLM judges; the playbook configures.** Three authors, no
  overlap — which is why the playbook stays tuned and the phase outputs compose.
- **Governed capabilities.** ServiceNow, Splunk, AppDynamics, Prometheus, CMDB, OpenShift,
  Artifactory, and Git are modeled as pure, mockable adapters; the whole system runs
  **credential-free** on canned tool outputs for testing + demos.

## Run it

**Engine (credential-free, fully mocked):**
```bash
cd engine
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest          # unit + end-to-end scenarios across 5 incident layers
.venv/bin/ruff check
```

**Workbench UI:**
```bash
cd workbench
npm install
npm run build && npm test
npm run dev               # renders the demo investigation bundle
```

The demo bundle (`workbench/public/demo-code-regression.json`) is produced by a real engine run:
```bash
cd engine && .venv/bin/python scripts/build_demo.py
```
