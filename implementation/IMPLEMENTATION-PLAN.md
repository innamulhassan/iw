# Incident-Triage Investigation Engine — Implementation Plan

> The build plan. Source of truth = the v2 design set (`../design/v2/`), PRD = `../design/v2/00-PRD.md`.
> **Discipline:** understand → plan the phase → build → unit-test → **validate against the design** → fix the design if the build exposes a gap → next. One component at a time, no hurry, fully autonomous, everything unit-tested, everything mockable before any real credential.

---

## 0. Principles (hold for every phase)

1. **As-per-design.** Every type, field, rule, and edge-case traces to a section of `../design/v2/`. The schema source is `04-data-model.html`; the engine source is `03-design.html` (Parts A–G, incl. B8 sessions, B9 graph runtime); governance is Part C / `07-capability-layer.html`; the contract is `00-PRD.md` (FR1–22, AC1–9).
2. **Design is the source — but fixable.** If implementation reveals the design is missing/wrong/ambiguous, **fix the design doc** (and note it in §6 of this file) before coding around it. Never silently diverge.
3. **One by one.** A component is *done* only when: code + unit tests (covering the edge-cases the design names) + green + design-validated. Then the next.
4. **Mock first, real last.** Every external boundary (capability sources, stores, event log) sits behind an interface with a **Mock** implementation that returns toy data (the INC-4821 fixtures). The whole system is unit- and integration-testable with **zero real credentials**. We **stop** before real integration; the user then supplies real API / model / Mongo / Postgres.
5. **No assumption.** Field names and values come from the design, verified, not from memory.
6. **Test pyramid.** Unit (per module) → contract (per interface) → one mocked end-to-end (INC-4821 through all 4 phases). pytest (backend), vitest + RTL (frontend).

---

## 1. Deliverables & tech stack

```
implementation/
  IMPLEMENTATION-PLAN.md      ← this file
  engine-backend/             ← Python · LangGraph · networkx · FastAPI · Pydantic v2
  workbench-ui/               ← React · Vite · TypeScript · vitest
```

| Layer | Tech | Design ref |
|---|---|---|
| Engine orchestration | **LangGraph** (+ `PostgresSaver`; SQLite/in-memory for tests) | 03-design B1–B7 |
| In-memory graph | **networkx** (engine-owned) + tool surface + render-slice + fold-adapters | 03-design B9 |
| Capability layer | registry + intent-resolver + `govern()` + per-kind adapters (mock→real) | 03-design C, 07 |
| Live session | per-session **lock** + append-only **event log**, client-polled (in-memory mock → Postgres advisory lock + event log in Mongo/Postgres; **no Redis/WebSocket**) | 03-design B8 |
| API | **FastAPI** + Pydantic | 03-design F |
| Data plane | **MongoDB** read-model (in-memory mock → real) | 04-data-model §7–8 |
| Config plane | **PostgreSQL** (playbook + registry; in-memory mock → real) | 04-data-model §6 |
| Console | **React + Vite + TS**, 3 panes + graph | 03-design F1, mockups |

---

## 2. Build order (phases)

Each phase below carries: **scope · design refs · unit tests · design-validation gate**. Dependency-ordered; later phases import earlier ones.

### P0 · Scaffold + tooling
- Backend: package layout under `src/engine/`, `pyproject.toml` (pydantic, pytest, ruff; later fastapi, networkx, langgraph), venv, `pytest.ini`, `README.md`, `.gitignore`.
- Frontend: deferred to P7 (Vite scaffold) — placeholder `README.md` now.
- **Done when:** `pytest` runs (zero tests = green); `python -c "import engine"` works.

### P1 · Domain model  *(foundation — design-complete, no external deps)*
- **Scope:** every Pydantic v2 model from `04-data-model`: `SubjectRef`; graph `Node`/`Fact`/`Edge` (+ enums: kind, layer, impact_state, edge types); `PhaseRecord`/`Step`; the four outputs `AssessResult`/`RootCauseResult`/`RemediationResult`/`VerifyResult` (+ sub-models: `Confidence{value,basis}`, `Candidate`, `Action`, `Followup`, `Suggestion`, `ImpactAssessment`, `TimeFactor`); `Feedback`; `Playbook` (+ `PhaseSpec`, `Defaults`); registry `Provider`/`DeclaredCapability`/`CapabilityPolicy`.
- **Design refs:** 04-data-model §2 (SubjectRef), §3 (graph), §4 (phase/step/outputs), §5 (feedback), §6 (playbook/registry).
- **Unit tests:** parse the design's INC-4821 worked-example JSON into each model (fixtures); enum-boundary (reject bad `impact_state`, `access`, `kind`); `confidence` is `{value,basis}` not a bare float; `revert_when` enum; `SubjectRef` unique-key `(domain,id)`; round-trip `model_dump`→`model_validate`.
- **Design-validation gate:** every field present for every entity; **no `slo`, no bare `incident_id`** anywhere; INC-4821 fixtures load clean.

### P2 · Graph runtime  *(B9 — engine-owned in-memory graph + tools)*
- **Scope:** `IncidentGraph` (networkx `DiGraph`) wrapping P1 `Node`/`Fact`/`Edge`; the **tool surface** `get · neighbours · walk · find · blast_radius · path · annotate`; the **render-slice** (cause path + frontier + suspects in full; healthy/ruled-out collapsed to a count — bounded); the **fold-adapter** interface + one sample adapter (tool result → Node/Fact upserts).
- **Design refs:** 03-design B9.1–B9.7.
- **Unit tests (the B9.6 guards):** unknown id → `unknown` (never invents); `annotate` without `evidence_ref` → rejected; fold **idempotent** keyed `(node, fact-key, source, observed_at)` (replay = no dupes); conflicting facts **kept** with source+confidence (no silent overwrite); `blast_radius`/`path` directionality; render-slice bounded for a 147-node graph; cycle-safe walk.
- **Design-validation gate:** AC6, AC8 hold; tool list matches B9.2 exactly.

### P3 · Capability layer  *(Part C / 07 — governed boundary)*
- **Scope:** in-memory **registry** (load `Provider`/`DeclaredCapability`/`CapabilityPolicy`); **intent resolver** (a playbook `need` → capabilities by intent + effect, **bounded by the phase's `effect`**); `govern()` (effect × access → allow/ask/deny; `unknown_access` fallback); **per-kind adapters** `skill | mcp_local | mcp_remote | a2a_agent | api` behind one `CapabilityAdapter` interface, **mock impls** returning INC-4821 toy data; a new capability lands `pending_review` + `deny`.
- **Design refs:** 03-design C1–C5; 07-capability-layer; PRD FR10–13, FR12 (read-only phase provably can't select a write).
- **Unit tests:** resolution by intent+effect; **read-only phase yields zero write candidates** (pre-gate proof, AC1); `govern()` decision table (allow/ask/deny, unknown→ask); pending_review→deny; mock adapter dispatch per kind.
- **Design-validation gate:** AC1, AC5 hold; kinds match the registry enum.

### P4 · Engine on LangGraph  *(Part B — the orchestrator)*  — split into P4a/b/c
- **P4a · Playbook loader + compile:** parse the markdown+front-matter playbook (`../incident-triage.playbook.md` / a faithful v2 copy) → `Playbook`; build a LangGraph `StateGraph` from the phases; define `RunState`. *Refs:* B1–B2. *Tests:* loads the 4 phases with `needs`/`effect`/`output`/`min_confidence`; compiles; rejects a tool-name in `needs` (must be intents).
- **P4b · Phase plan→execute loop + capability-in-loop:** per phase, resolve `need` → governed capability → **fold** into the graph → log a `Step` → until the typed **output** validates vs `output_schema`. *Refs:* B3–B4, FR3. *Tests:* a phase runs with mock capabilities, emits Steps with `touched`/`evidence`, produces a valid `AssessResult`.
- **P4c · Gate + checkpoint + conditional edges + failure:** gate via `interrupt_before` on `gate_writes` phases (FR4 — only `effect=write` caps; graph `annotate` not gated); checkpoint each step (SQLite/in-memory saver) + resume; conditional edges (`confidence < min_confidence` loop; Verify→Root-cause backtrack); `error_handler` (retry transient `max:3`/backoff; permanent→escalate; `on_failure: run-remaining` → `blocked`/`partial`). *Refs:* B5–B6, FR2/FR4/FR5. *Tests:* gate pauses + resumes (AC2/AC3); min_confidence loop; backtrack; retry-then-escalate; run-remaining.
- **Design-validation gate:** FR1–5, AC2, AC3 hold; the B7 INC-4821 trace reproduces.

### P5 · Live session  *(B8 — lock + polled event log)*
- **Scope:** session lifecycle (free chat → promote, **idempotent create on subject id**, promotion seeds context); **Problem 1** — per-session lock (run-owner) via **lease + heartbeat**, input queue **drained after the step / at the next gate**, crash → resume from checkpoint; **Problem 2** — per-session **append-only event log** (one stream: chat + graph + phase deltas under one `seq`), clients **poll `since(seq)`**, **snapshot + resume-from-seq**; **membership re-checked per event**. Mock backends: in-memory lock + in-memory event log (real = Postgres advisory lock + a durable event log in Mongo/Postgres; **no Redis/WebSocket — polling**).
- **Design refs:** 03-design B8.1–B8.4; PRD FR14–18.
- **Unit tests (the B8.4 cases):** create-or-join race → one thread (AC9); lease expiry → another owner resumes (AC9); queue drain trigger; per-event auth revocation stops events (AC9); headless/idle persists; concurrent-approval answered-once.
- **Design-validation gate:** AC4, AC9 hold.

### P6 · API  *(Part F — FastAPI)*
- **Scope:** endpoints — create/join session, post message, advance run, approve/deny gate, get read-model, `POST /feedback`; wire engine + session + graph; the **read-model projection** (denormalized incident document, 04-data-model §8) over an in-memory Mongo-like store.
- **Design refs:** 03-design F; 04-data-model §7–8; PRD FR19–22.
- **Unit/contract tests:** each endpoint; read-model matches the §8 document shape; feedback persists separate from the run.

### P7 · Workbench UI  *(Part F1 — React)*
- **Scope:** Vite + React + TS; 3-pane console (Incidents / Triage chat + inline gate / Phases & Steps ⇄ Graph); the **graph view** (focus: cause path + impacted + collapsed-healthy + minimap — hundreds of nodes); **mock API client** against fixtures; the session client (snapshot + resume-from-seq).
- **Design refs:** 03-design F1; mockups `diagrams/ui-console*.png`; PRD FR19–20.
- **Component tests (vitest + RTL):** panes render from fixtures; gate approve/deny; graph focuses + collapses; reconnect resumes from seq.

### P8 · Mocked end-to-end  *(the full-system gate before real)*
- **Scope:** wire everything against mocks (dummy MCP/A2A/API providers, in-memory stores, toy INC-4821); one end-to-end test: INC-4821 runs Assess → Root cause → gated Remediation → Verify-close; the graph builds; the 4 outputs validate; the read-model projects; feedback records.
- **Design-validation gate:** **AC1–AC9 all hold** against the mocked system.

### ⏸ STOP — hand-off for real integration
User supplies real **API / model / Mongo / Postgres**. Then:
- **P9 · Real integration:** swap mock adapters for real MCP/A2A; `PostgresSaver`; real Mongo read-model + the event log persisted to Mongo/Postgres. Integration tests against real services with toy data first.
- **P10 · Live UI wiring:** point the frontend's poll at the real `/poll` endpoint; multi-user via the shared event log — **no WebSocket/Redis** (push optional later, behind the same `seq` API).

---

## 3. Per-phase loop (run this for every phase above)

1. **Re-read** the design section(s) for the phase.
2. **Re-plan** the phase here (expand its bullet if needed).
3. **Build** faithfully.
4. **Unit-test** — table-driven; cover every edge-case the design names.
5. **Run green.**
6. **Validate vs design** — every field/rule present; fixtures parse; the relevant ACs pass.
7. **Fix the design** if the build exposed a gap → record in §6.
8. **Mark done** (§5), next.

---

## 4. Mock strategy (so it's fully testable with no creds)

| Boundary | Interface | Mock (now) | Real (P9+) |
|---|---|---|---|
| Capability source | `CapabilityAdapter` | returns INC-4821 toy data per `need` | MCP / dynamic-MCP / A2A |
| Checkpointer | LangGraph saver | `MemorySaver` / SQLite | `PostgresSaver` |
| Read-model store | `ReadModelStore` | in-memory dict | MongoDB |
| Config store | `ConfigStore` | in-memory / YAML+JSON | PostgreSQL |
| Session lock | `RunLock` | in-memory lease | Postgres advisory lock |
| Session event log | `EventLog` | in-memory append log | durable append log (Mongo/Postgres), **client-polled** |

---

## 5. Progress

| Phase | State | Notes |
|---|---|---|
| P0 Scaffold | ✅ done | pyproject + venv + pytest + ruff; `import engine` works |
| P1 Domain model | ✅ done | all 04-data-model shapes as Pydantic v2 (`extra="forbid"` → field-complete); **31 tests green**, ruff clean; gates: no SLO, SubjectRef-keyed, 5 provider kinds |
| P2 Graph runtime | ✅ done | networkx graph + 7-tool surface + render-slice + fold-adapters; **20 tests green** (all B9.6 guards: unknown-id, conflicting-facts, idempotent-fold, annotate-needs-evidence, cycle-safe, expand-cap); render bounded ≤30 on 147 nodes |
| P3 Capability layer | ✅ done | registry + govern() + resolver + adapters + autonomy; **15 tests green**; AC1 (read-only⇏write) + AC5 (only via registry+govern) hold; design fix: `intents[]` added |
| P4 Engine (a/b/c) | ✅ done | **a** loader+state+routers · **b** phase loop + capability-in-loop · **c** `compile_run` (LangGraph StateGraph + conditional edges + `interrupt_before` gate + checkpointer) + `error_handler` (retry/run-remaining). **23 tests**; the full INC-4821 run pauses at the gate, resumes, reaches END (B1/B3/B4/B5/B6/E4) |
| P5 Live session | ✅ done | lock (lease+heartbeat) + polled **event log** + manager + **the pen (one writer) + roles** + **SSE `/stream`** + `take-pen`/`release-pen`; **16 tests**; B8.4 cases + writer-gating |
| P6 API | ✅ done | FastAPI: sessions · advance · gate (approve/refine/deny) · read-model · messages/events · **`/poll`** · **SSE `/stream`** · **take/release-pen** · feedback; **12 contract tests** (TestClient) |
| P7 Workbench UI | ✅ done | Vite+React+TS · 3 panes · focused graph view · **widget registry** (text·tool-call·table·image·graph·gate·**sandboxed-iframe HTML**) · **pen badge + viewer read-only + take/release pen** · **SSE client wired** (`api/stream.ts`, `streamUrl`) · mock API; **16 component tests** + typecheck + prod build green |
| P8 Mocked E2E | ✅ done | INC-4821 through the whole system; **8 tests asserting AC1–AC9 all hold** — the stop-point gate before real integration |
| P9 Real integration | blocked | needs creds |
| P10 Live UI wiring | blocked | needs creds |
| P11 Interactive widgets (**MCP Apps**) | next direction | the agreed path for *interactive* widgets: a tool returns a `ui://` HTML resource → host renders it in a **sandboxed iframe** → the widget calls tools back via **JSON-RPC over postMessage** (the open SEP-1865 standard, Anthropic+OpenAI). Host-side **RBAC** gates UI-initiated tool-calls (the pen). Our `html` widget + the registry are the seam. **Pilot first** — the spec is stable (2026-01-26) but the reference impl is young (per the no-assumption research) |

## 6. Design fixes made during implementation

- **P3 · `DeclaredCapability.intents[]` was missing from `04-data-model` §6.2.** The intent resolver (`03-design` C4: `if need in c.intents`) and the registry table (C2) both require it, but the schema's JSON block omitted it. Added `intents[]` to the schema JSON + the field-list row in `04-data-model.html`, and to the `DeclaredCapability` model. The two design docs now agree.

## 7. Design decisions taken during implementation

- **UI patterns validated by research (workbench UIs + Claude Desktop) — keep the MVP; these ARE the convergent standards.** A message = typed parts → a **renderer registry** (`kind → component` + fallback) is the dominant pattern (Vercel AI SDK `data-*` parts; assistant-ui `MessagePrimitive.Parts` / `by_name` + `Fallback`; CopilotKit `useRenderTool` + wildcard; LangChain Agent Chat UI) — exactly our `widgets/registry.tsx`. **SSE** with start/content/end framing + `data:`-JSON deltas is universal — exactly our `/stream`. **Sandboxed-iframe** for agent-generated HTML = the **MCP-Apps** standard — exactly our `html` widget. Claude Desktop's **two-mode split** (ephemeral inline widgets + persistent right side-panel) = our chat widgets + the right pane. **Optional refinements (later, not MVP):** (1) one message = a *list* of parts (text + a widget in one turn); (2) tool-call **lifecycle streaming** with a `requires-action` status (the documented HITL hook); (3) interactive widget → **follow-up event** back into the agent loop (Claude's click→prompt; MCP-Apps `postMessage` round-trip for *interactive* iframes); (4) borrow from **LangChain Agent Chat UI** (Next.js+TS, renders any LangGraph stream + interrupts). **No standard exists for multi-user / observer UI** (presence, viewer/writer) — our pen/roles is a justified custom build, not a reinvention.
- **Shared-session MVP 1 (decided with the user) — the pen + roles + widget registry + SSE.** One session per incident; everyone sees the same chat (arrival-ordered by `seq`). **One writer at a time ("the pen")** — only the pen-holder may send or approve; everyone else is a read-only **viewer**; the pen is handed over (`take-pen` / `release-pen`, one holder at a time). The agent does **one unit of work to completion, then the next message** (sequential). **Single approval** by the pen-holder — **no dual approval**. **UI tech:** the chat renders a heterogeneous event stream via a **widget registry** (`kind → component`: text · agent · tool-call · table · image · graph · **sandboxed-iframe HTML**); transport is **SSE** (`GET /stream`, `Last-Event-ID` resume) for the real app and **polling** in the tested MVP — both over the same event log; roles enforced **UI + server**. Built: `session/manager.py` (pen + `require_writer`), API `/stream` + `take-pen`/`release-pen` + writer-gated `messages`/`gate`, frontend `widgets/registry.tsx` + `api/stream.ts` + the pen badge/composer gating.
- **Live sync = client polling, not push (decided with the user).** Problem 2 (B8.3) was specced as a pub/sub channel over Redis or a WebSocket hub. We chose **polling-primary**: an append-only per-session **event log** (one ordered stream, per-session `seq`); clients **poll `since(seq)`** every few seconds and apply deltas; join/reconnect = snapshot + resume-from-seq. **Rationale:** the channel was already liveness-only (correctness is in the stores), and polling **removes the cross-server fan-out problem entirely** — every server answers a poll statelessly from the shared store, so **no Redis, no WebSocket, no SSE, no sticky routing**. Trade-off accepted: ~seconds of latency + no token-level streaming (fine for triage). The `seq` interface is unchanged, so push (SSE / WebSocket+bus) can be added later with no other changes. Reflected in `03-design` B8.3/B8.4/E, `00-PRD` FR17/§7/§8/§9, and the code (`session/eventlog.py`, the API `/poll` endpoint, the frontend poll loop).
