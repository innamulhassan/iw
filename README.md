# LunaSRE — Multi-Agent SRE Incident Investigation

A reference implementation that exercises **all 14 layers of the agentic-AI stack**
in one focused system: MCP tools · A2A agent-to-agent delegation · a LangGraph
supervisor with human-in-the-loop approval · AG-UI streaming · OpenTelemetry
tracing · an audit log · per-agent identity · an MCP-gateway scope swap. Runs
locally on Ollama (no API keys); every local component swaps for its production
backend by config.

**A simulated SRE incident-investigation platform:** it takes a mock alert, runs a
supervisor → specialist → root-cause multi-agent investigation over mock
monitoring tools, **gates remediation on human approval**, and audits every step.

### Start here
- **Run it:** `bash scripts/pilot.sh` — one command boots everything + runs an incident. See **[DEMO.md](DEMO.md)**.
- **Understand it:** **[WALKTHROUGH.md](WALKTHROUGH.md)** — the 14 layers → files map, the request flow, and the patterns.
- **Run individual services:** **[SERVICES.md](SERVICES.md)** — every entry point.
- **Deep design:** **[ARCHITECTURE.md](ARCHITECTURE.md)**.

> Built as a hands-on learning project. Requires `uv` + Python 3.12 + Ollama
> (`ministral-3:8b`). 84 tests, MIT-licensed.

---

## 1. Project objective (the why)

Build ONE focused toy project that **exercises all 14 layers** of the agent standards stack from `catalog/ai/agentic-standards-landscape-2026-05.md`, using **LangGraph** as the orchestration centerpiece and a **multi-vendor LLM mix** (Gemini + xAI Grok + local Ollama) — so that by the end of the build, every layer has been touched with real code, not just understood from reading. (No Anthropic key required — Claude is droppable/addable via a single LiteLLM config line, which is itself the L1 portability lesson.)

**Use case:** a simulated SRE incident-investigation platform that takes a "page" (mock alert), runs multi-agent investigation through mock monitoring tools, gates remediation on human-in-the-loop approval, writes a postmortem, and audits every step.

**Why this use case:**
- Directly relevant to your Wells Fargo Senior Director / Principal Engineer SRE role (joining soon)
- Naturally exercises 13 of 14 layers (commerce L11 explicitly N/A — would be added if commerce were the domain)
- Buildable locally on a Mac with mock infrastructure
- Demoable end-to-end in a 5-minute walkthrough

**What you'll learn at D3 depth (vs D2 reading depth):**
- The actual feel of LangGraph state machines, checkpointers, interrupts, subgraphs, `Send` API
- How MCP servers + MCP gateway + tool isolation actually work in code
- How A2A Agent Cards + delegation flow under the hood
- How AG-UI events stream to a real frontend
- How OpenTelemetry GenAI traces look in production-grade observability tooling
- How OAuth/OIDC + agent identity manifest in a real auth flow
- How memory layer integrates (and where it bumps into runtime-state-management)
- How OWASP Agentic Top 10 checks become real review steps

---

## 2. Architecture at a glance

> **Reconciled 2026-06-03 at L26.P to match `ARCHITECTURE.md`.** Canonical diagrams live in `ARCHITECTURE.md`: §1 narrates one incident end-to-end through all 14 layers; §3 has the agent topology; §8 the directory layout. This section is the one-screen visual.

```
                                  ┌──────────────────────────────────────┐
                                  │  Browser — Next.js 15 + React 19 +   │
                                  │  CopilotKit · AG-UI dashboard (L5)   │
                                  └────────────────┬─────────────────────┘
                                                   │ AG-UI events / SSE (L0)
   ┌───────────────────────────────────────────────┼──────────────────────────────────────┐
   │  LangGraph orchestrator (Python; Postgres checkpointer) — Layer 7                    │
   │                                               │                                      │
   │   Router (Ollama, local)                                                             │
   │      │ classify alert type                                                           │
   │      ▼                                                                               │
   │   IC Agent (xAI Grok) — supervisor                                                   │
   │      │ A2A delegate by alert type (L4 — capability-routed from agent_registry)       │
   │      ├──▶ DBOps (Grok)      ─┐                                                       │
   │      ├──▶ NetOps (Grok)      ├── findings ──▶ RCA Agent (Gemini, long context)       │
   │      └──▶ DeployOps (Grok)  ─┘                       │ root cause                    │
   │                                                       ▼                              │
   │                                            HITL interrupt (L5; AG-UI prompt)         │
   │                                                       │ user Approve                 │
   │                                                       ▼                              │
   │                              ┌──── critique ──── Grok Reviewer (xAI) ◀──┐            │
   │                              ▼                                          │            │
   │                       PM Writer (Gemini) ───── finalized postmortem ────┘            │
   │                                                                                      │
   │  Cross-cutting planes wrap every call:                                               │
   │   • L8 Observability: OTel GenAI → Arize Phoenix                                     │
   │   • L9 Governance:    OWASP Agentic Top-10 checklist + audit_log table               │
   │   • L12 Identity:     per-agent OAuth (Authelia OIDC) — every agent = workload id    │
   │  L10 Memory: Letta (past-incident pattern recall; behind own interface)              │
   └─────────────────────────────────┬────────────────────────────────────────────────────┘
                                     │ MCP / JSON-RPC (L3) — runtime discovery from
                                     │ infra/registries/mcp_registry.yaml (Phase 1 file)
                                     │ → MCP Gateway (Phase 4 swap, same Registry interface)
                              ┌──────┴────────────────┐
                              │  MCP Gateway (Phase 4)│ ← agentic-community/mcp-gateway-registry
                              │  per-agent tool scope │   Holds creds; audits every call.
                              │  (L3 + L12)           │
                              └──────┬────────────────┘
        ┌───────────────┬────────────┼────────────┬───────────────┐
        ▼               ▼            ▼            ▼               │
  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────┐
  │ mock_datadog│ │ mock_logs    │ │ mock_traces  │ │ mock_pg  │   4 MCP servers (fastmcp + mcp SDK)
  │             │ │ (STATEFUL    │ │              │ │          │
  │             │ │  SESSIONS)   │ │              │ │          │
  └─────────────┘ └──────────────┘ └──────────────┘ └──────────┘
        mock data: alerts.json · logs/*.jsonl · traces/*.json

   Inference: LiteLLM proxy (L1 — OpenAI-compatible /v1/chat/completions)
              ├─→ Gemini (Google)  — RCA + PM Writer
              ├─→ xAI Grok         — IC + DBOps + NetOps + DeployOps + Reviewer
              └─→ Ollama local     — Router
              (Claude re-addable via one LiteLLM config line — no Anthropic key needed.)

   7 A2A agents (Router is graph-first — no Agent Card, not in agent_registry).
```

---

## 3. Layer-by-layer mapping (the 14-layer coverage)

| Layer | Component in this project | Tech | What you'll learn hands-on |
|---|---|---|---|
| **L0 Transport substrate** | JSON-RPC over stdio (local MCP) + SSE (AG-UI) + HTTP (LiteLLM) | Built-in to each tool | The wire shape; why SSE wins over WebSocket through enterprise infra |
| **L1 Inference API** | LiteLLM proxy serving OpenAI-compatible `/v1/chat/completions` over Gemini+Grok+Ollama | LiteLLM | Vendor-portability in code — swap models (or add Claude later) with a config line |
| **L2 Tool description** | Tool schemas in OpenAI function-calling format, derived from MCP server's `tools/list` | JSON Schema | The L1↔L2 portability seam |
| **L3 Agent ↔ tool** | 4 mock MCP servers (`mock_datadog` · `mock_logs` [stateful sessions] · `mock_traces` · `mock_pg`) + MCP Gateway in front (Phase 4) + runtime registry discovery | `fastmcp` + official `mcp` SDK (Python) + `agentic-community/mcp-gateway-registry` (Docker) | Real MCP server code · registry runtime-discovery · MCP **stateful sessions** · gateway trust boundary |
| **L4 Agent ↔ agent** | **Supervisor + 3 specialists** (DBOps / NetOps / DeployOps) — IC delegates by alert type via A2A; escalates to RCA via A2A; PM Writer ↔ Grok Reviewer adversarial pair | `a2a-sdk` (official; replaces `python-a2a`) | Agent Cards · task lifecycle (submitted / working / input-required / completed) · opacity · capability-routed fan-out |
| **L5 Agent ↔ UI** | React dashboard streaming AG-UI events | CopilotKit (React) + AG-UI SSE | Real UI rendering of streaming tokens, tool calls, HITL prompts |
| **L6 Editor ↔ agent** | (Optional — use Zed or Cursor with Claude Code over ACP for development) | Zed or Cursor | The dev-tooling plane during development (not part of product runtime) |
| **L7 Runtime / orchestration** | LangGraph state-machine; Postgres checkpointer; `interrupt_before` HITL gate; `Send` API for parallel sub-agent fan-out | LangGraph 1.0 (Python) + Postgres | The runtime centerpiece — state, nodes, edges, checkpointer, interrupts, subgraphs |
| **L8 Observability** | OpenTelemetry GenAI instrumentation auto-emitted by LangGraph → Arize Phoenix UI | OpenLLMetry (Traceloop) or OpenInference (Arize) + Phoenix Docker | Real trace UI showing every LLM call, tool call, cost, latency |
| **L9 Governance** | OWASP Agentic Top 10 checklist applied at code review + simple audit log table | Manual checklist + DB table | What governance feels like in code (not just on a slide) |
| **L10 Memory** | Letta server for past-incident pattern recall (separate from LangGraph state) | Letta (Docker) | Memory vs runtime-state distinction; "abstract through your own interface" pattern |
| **L11 Commerce/payments** | **N/A for SRE incident use case** — would add UCP+AP2 wrappers for commerce-style use cases | (skip) | Explicit non-coverage; understand why this layer doesn't apply here |
| **L12 Identity / auth** | Authelia OIDC for user login; per-agent OAuth client credentials for MCP Gateway | Authelia (Docker) + OAuth flows | OAuth flows, agent-as-OAuth-client, the agent-identity-vs-user-identity distinction |
| **L13 Capability / schema description** | A2A Agent Cards at `/.well-known/agent.json` for the **7 A2A agents** (IC · RCA · DBOps · NetOps · DeployOps · PM Writer · Grok Reviewer); indexed in `agent_registry.yaml` for capability-based discovery. *(Router is graph-first — no card.)* | JSON | Self-describing agents · registry-indexed discovery · capability routing |

**Coverage: 12/14 layers exercised hands-on in runtime code; L6 (editor↔agent) exercised at dev-time (Zed / Cursor used to *build* LunaSRE, not in product runtime); L11 (commerce) explicitly N/A. Effective: 13/14.**

---

## 4. Tech stack (with versions to pin)

> **Reconciled 2026-06-03 at L26.P to match `ARCHITECTURE.md` §8 (the design source-of-truth).** Replaces the original pip / `python-a2a` / flat-layout stack with the enterprise-grade upgrades locked at L25.P: `uv` package manager, full dev tooling, `src/` layout, official `a2a-sdk`, official `mcp` SDK alongside `fastmcp`.

**Language + dev tooling:**

| Tool | Purpose | Version target |
|---|---|---|
| Python | Project language | 3.11+ (3.12 in dev) |
| `uv` | Package + venv manager (replaces pip) | latest |
| Ruff | Lint + format | latest |
| Pyright | Type checking | latest |
| Pydantic v2 | Models + config validation | v2 |
| structlog | Structured logging | latest |
| `just` | Task runner | latest |
| pre-commit | Git hooks | latest |
| pytest + pytest-asyncio | Tests (sync + async) | latest |

**Runtime:**

| Tool | Purpose | Version target |
|---|---|---|
| LangGraph | Agent runtime (state machine + Postgres checkpointer + `interrupt_before` + `Send`) | latest |
| LiteLLM | Multi-vendor OpenAI-compatible proxy | latest |
| `fastmcp` + official `mcp` SDK | Build Python MCP servers (one-shot + stateful sessions) | latest |
| `a2a-sdk` (official; replaces `python-a2a`) | A2A protocol SDK | latest |
| Letta | Memory layer (behind own interface) | latest Docker image |
| Postgres | LangGraph checkpointer + `audit_log` table | 16+ Docker |
| Arize Phoenix | Observability UI (OTel GenAI) | latest Docker |
| Authelia | OIDC provider (local) + per-agent OAuth client-creds | latest Docker |
| `agentic-community/mcp-gateway-registry` | MCP gateway (Phase 4 — swaps in behind the same `Registry` interface as the Phase-1 file loader) | latest |
| Ollama | Local LLM runtime (Router tier) | latest |

**Frontend:**

| Tool | Purpose | Version target |
|---|---|---|
| Next.js | Frontend framework | 15+ |
| React | UI library | 19+ |
| CopilotKit + AG-UI | Agent-streamed dashboard + HITL prompts | latest |

**Models (locked at L25.P, unchanged from L24.E):** Router = Ollama Llama-3.1-8B or Phi-3.5-mini · IC + DBOps + NetOps + DeployOps + Grok Reviewer = xAI Grok · RCA + PM Writer = Gemini 2.5 Pro. **No Anthropic key by design** — Claude is re-addable via one LiteLLM config line (the L1 portability lesson).

## 5. Prerequisites

Before starting Phase 1, ensure:

```bash
# On your Mac
brew install python@3.11 node ollama docker
ollama pull llama3.1:8b   # or phi3.5:3.8b for faster

# API keys (load via .env or 1Password)
GOOGLE_API_KEY=...      # Gemini (RCA + PM Writer)
XAI_API_KEY=...         # Grok (IC Agent + adversarial reviewer)
# No ANTHROPIC_API_KEY needed — Claude not used in this build.

# Verify
docker --version
python --version
node --version
ollama list
```

---

## 6. Phased build plan (5 phases, day-by-day)

> **Reconciled 2026-06-03 at L26.P to match `ARCHITECTURE.md`.** The original phase plan stood the loop up with hardcoded wiring + flat layout + `python-a2a`; the upgraded plan stands the same loop up on `uv` + `src/` layout + **registry-discovered tools and peers** + **config-driven agents** + **MCP stateful sessions** + **Supervisor + 3 specialists**. Models unchanged. Phase day-budgets retained; build *shape* changes, not scope.

### Phase 1 — Skeleton: discovery-based scaffold + first config-driven agent (Day 1-2, ~6 hrs)

**Goal:** Get the smallest end-to-end loop running on the upgraded stack — `src/lunasre/` layout, `uv` env, registry-discovered MCP server, config-driven agent. One agent (xAI Grok via LiteLLM proxy) calls one mock MCP tool, *discovered from a registry, not hardcoded*.

> **Two chunks; build in strict order. Chunk 1 = scaffold + registries + first MCP server, no LLM yet. Chunk 2 = LiteLLM proxy + first config-driven LangGraph agent, loop closes.**

#### Chunk 1 — scaffold + registries + first MCP server

1. **Scaffold (uv + src-layout):** `uv init`; `pyproject.toml` (project metadata + `[tool.ruff]` / `[tool.pyright]` blocks); `uv add` runtime deps (`langgraph`, `litellm`, `fastmcp`, `mcp`, `a2a-sdk`, `pydantic`, `structlog`); `uv add --dev` dev deps (`ruff`, `pyright`, `pytest`, `pytest-asyncio`, `pre-commit`); create `src/lunasre/` package skeleton; `justfile` (recipes: `dev`, `test`, `lint`, `mcp:run-<name>`); `ruff.toml`, `pyrightconfig.json`, `.pre-commit-config.yaml`; `.env.example` (`XAI_API_KEY`, `GOOGLE_API_KEY` — Google not blocking Chunk 1).
2. **Registries (file-based; same interface as Phase-4 MCP Gateway swap):**
   - `src/lunasre/registries/{mcp_registry.py, agent_registry.py}` — the `Registry` interface + file loader.
   - `infra/registries/mcp_registry.yaml` seeded with `mock_datadog`, `mock_logs` (`supports_sessions: true`), `mock_traces`, `mock_pg` + each server's `capabilities` + transport.
   - `infra/registries/agent_registry.yaml` seeded with the **7 A2A agents** (`ic-agent`, `rca-agent`, `dbops-agent` `triggers_on=db-failure`, `netops-agent` `triggers_on=network-partition`, `deployops-agent` `triggers_on=deploy-regression`, `pm-writer`, `grok-reviewer`) each with `card_url` (`/.well-known/agent.json`) + `capabilities`. *(Router is a graph-first routing node, not an A2A peer — not registered here.)*
3. **First MCP server (`mock_datadog`):** `src/lunasre/mcp_servers/mock_datadog/server.py` using `fastmcp`; tools `drill_into_alert(alert_id)` + `tail_logs(service)`; reads `mock_data/alerts.json` (alerts 8472 / 8473 / 8474 — db-failure / network-partition / deploy-regression). Smoke test: `uv run python -m lunasre.mcp_servers.mock_datadog.server` returns expected payloads for alert 8472.

**Chunk 1 success criteria:** `just lint` clean; registries load; `mock_datadog` returns alert 8472 via direct test. **Zero LLM calls yet.**

#### Chunk 2 — LiteLLM proxy + first config-driven LangGraph agent

4. **LiteLLM proxy:** `infra/litellm_config.yaml` routing Gemini / Grok / Ollama under `/v1/chat/completions`; run `litellm --config infra/litellm_config.yaml --port 4000`.
5. **First config-driven agent (IC, xAI Grok):**
   - `src/lunasre/agents/configs/ic.yaml` per ARCHITECTURE.md §5 shape (llm / registries / tools / delegation / memory / observability / identity / runtime).
   - `src/lunasre/agents/base.py` (config loader + registry resolver — shared across all agents).
   - `src/lunasre/agents/ic.py` loads `ic.yaml`, resolves `mock_datadog` from the MCP registry, builds a one-node LangGraph (`investigate` node calling Grok via LiteLLM proxy at `localhost:4000` with the discovered tools), returns reasoning + tool-call results.
6. **Run:** `uv run python -m lunasre.agents.ic --alert-id 8472` → IC investigates alert 8472, discovers + calls the right tools, summarizes.

**Chunk 2 success criteria:** IC picks alert 8472, discovers `mock_datadog` from the registry (no hardcoded path), calls `drill_into_alert(8472)` + `tail_logs("payments-api")` via MCP, gets mock data back, summarizes the incident. End-to-end loop closes through the upgraded stack.

**Layers exercised by end of Phase 1:** L0 (stdio + HTTP) · L1 (LiteLLM proxy) · L2 (tool schemas via MCP) · L3 (1 MCP server + **registry discovery**) · L7 (LangGraph basic, **config-driven**) — **5/14**, all enterprise-shape.

---

### Phase 2 — Multi-agent + MCP stateful sessions + Letta memory (Day 2-3, ~7 hrs)

**Goal:** Add the 3 specialists (DBOps / NetOps / DeployOps) + RCA Agent via A2A; demonstrate MCP stateful sessions on `mock_logs`; add Letta memory + Postgres checkpointer.

**Build steps:**
1. **Three more MCP servers, one with stateful sessions:**
   - `src/lunasre/mcp_servers/mock_logs/server.py` (fastmcp) exposes a **session lifecycle**: `open_log_session(window_start, window_end, service) → session_id`, `grep(session_id, pattern)`, `tail(session_id, n)`, `close_log_session(session_id)`. Contrast with one-shot `tail_logs(service, window, pattern)` — the session reuses an established window + cursor across many cheap follow-up queries. *This is the MCP-sessions depth demo.*
   - `src/lunasre/mcp_servers/mock_traces/server.py` (`get_trace`, `find_slow_spans`).
   - `src/lunasre/mcp_servers/mock_pg/server.py` (`query_metric`, `connection_pool_status`).
2. **3 Specialists + RCA (config-driven):**
   - `src/lunasre/agents/{dbops,netops,deployops}.py` + `configs/{dbops,netops,deployops}.yaml`. Each carries a `triggers_on` capability so IC's `delegation.by_alert_type` resolves the right specialist from the agent registry.
   - `src/lunasre/agents/rca.py` + `configs/rca.yaml` — Gemini Pro long-context, takes IC + specialist findings, does long-context log analysis, returns root cause.
3. **A2A wiring:** publish Agent Cards at `/.well-known/agent.json` for IC + RCA + 3 specialists (`src/lunasre/agents/cards/*.json`); use `a2a-sdk` for IC's task delegation to specialists by alert type, and IC's escalation to RCA. **Opacity preserved** — peers only see capability + endpoint, not internals.
4. **Letta memory (behind own interface):** `docker run -p 8283:8283 letta/letta`; `src/lunasre/memory/letta_client.py` exposes `recall_similar(incident_signature)` + `store_pattern(incident)`; IC writes after each incident, reads similar past incidents before starting. *Memory is distinct from runtime state (checkpointer).*
5. **Postgres checkpointer:** `app.compile(checkpointer=PostgresSaver(...))`; verify durability — kill the process mid-investigation, restart, see it resume from the last checkpoint.

**Success criteria:** Alert 8472 (db-failure) → IC investigates → A2A-delegates to DBOps via registry lookup → escalates to RCA via A2A → RCA returns root cause; one MCP session on `mock_logs` runs `open → grep → grep → close`; Letta recalls similar past incident if present; crash mid-investigation → resume.

**Layers exercised:** + L3 (**stateful sessions**) · L4 (A2A, **supervisor + 3 specialists**) · L10 (Letta) · L13 (Agent Cards) — **8/14**

---

### Phase 3 — AG-UI frontend + HITL + adversarial reviewer (Day 3-4, ~8 hrs)

**Goal:** Real Next.js 15 + React 19 + CopilotKit frontend; human approves remediation before execution; xAI Grok adversarially critiques the postmortem.

**Build steps:**
1. **Frontend (Next.js 15 + CopilotKit):** `cd frontend && npx create-next-app@latest .`; `npm i @copilotkit/react-core @copilotkit/react-ui`; pages: incident dashboard + AG-UI SSE proxy; components: `IncidentStream.tsx`, `HITLApproval.tsx`, `PostmortemView.tsx`.
2. **AG-UI wiring:** backend emits AG-UI events (text deltas, tool-call start/end, state snapshots, human-input requests) over SSE; frontend renders.
3. **HITL gate:** `app.compile(interrupt_before=["execute_remediation"])`; on agent's proposed remediation the graph pauses; frontend shows Approve/Reject dialog (AG-UI human-input event); user approves; `app.update_state(...)`; flow resumes.
4. **Grok Reviewer (config-driven):** `src/lunasre/agents/grok_reviewer.py` + `configs/grok_reviewer.yaml` — calls xAI Grok on the proposed postmortem draft, returns adversarial critique; wired as a LangGraph node between IC's draft and PM Writer's finalize.
5. **PM Writer (config-driven):** `src/lunasre/agents/pm_writer.py` + `configs/pm_writer.yaml` — Gemini takes Grok's critique + RCA findings, finalizes postmortem markdown. **Cross-vendor by design** (author = Gemini, critic = Grok) so the review is a genuine second opinion.

**Success criteria:** Full incident flow runs in the browser. User sees streaming reasoning + live tool calls. HITL prompt appears; user approves; execution continues. Grok's critique appears; PM Writer finalizes incorporating it.

**Layers exercised:** + L5 (AG-UI) — **9/14**

---

### Phase 4 — Cross-cutting planes: Identity + Governance + Observability + MCP Gateway (Day 4-5, ~8 hrs)

**Goal:** Production-readiness planes — every call wrapped with identity, audit, traces. **MCP Gateway swaps in behind the same `Registry` interface** as the Phase-1 file loader (the replicability-without-rewrite proof).

**Build steps:**
1. **Observability (L8):** spin up Arize Phoenix in Docker; install OpenLLMetry / OpenInference; `src/lunasre/observability/init.py` initializes at agent startup (`Traceloop.init(app_name="LunaSRE")`); verify traces appear in Phoenix UI for every LLM call (model / tokens / cost / latency) + every tool call + multi-agent spans.
2. **Identity (L12) — per-agent OAuth:** spin up Authelia in Docker as OIDC provider; configure OAuth clients for the user (`web-app`) + each of the 7 A2A agents (`agent-ic`, `agent-rca`, `agent-dbops`, `agent-netops`, `agent-deployops`, `agent-pm-writer`, `agent-grok-reviewer`) via `client_credentials` grant; `src/lunasre/identity/oauth_client.py` acquires the token at agent startup per config; *the token is the agent's workload identity on every tool/peer call.*
3. **MCP Gateway (L3 swap):** deploy `agentic-community/mcp-gateway-registry` via Docker; register the 4 MCP servers; configure per-agent tool scope (IC = all tools; future audit-only = read-only). **Swap the `Registry` implementation** in `src/lunasre/registries/mcp_registry.py` from file loader to Gateway client — agent code unchanged. *This is the enterprise-replicability proof in miniature.*
4. **Router (Ollama) as first graph node:** `src/lunasre/agents/router.py` + `configs/router.yaml` — Ollama (Llama-3.1-8B or Phi-3.5) classifies the alert type (`db-failure` / `network-partition` / `deploy-regression` / `unknown`) and routes to the specialist via IC's delegation map. *Router is a graph-first routing node, not an A2A peer (no Agent Card, not in agent_registry).* Demonstrates the cheap+local+private routing tier.
5. **Governance + audit (L9):** create `audit_log` table (Postgres) via `governance/audit_schema.sql`; every tool call logs `(timestamp, agent_id_from_oauth_token, tool, args, result_fingerprint)`; apply OWASP Agentic Top 10 as a code-review checklist → `governance/owasp-agentic-checklist.md` with documented posture per item.

**Success criteria:** Traces visible in Phoenix end-to-end. User logs in via Authelia. Each of the 7 A2A agents acquires its own OAuth token; MCP Gateway audits every tool call by agent identity. Router (Ollama) classifies alerts locally + cheaply. MCP Gateway swap-in did not require any change to agent code.

**Layers exercised:** + L3 (full — Gateway swap) · L8 (OTel GenAI) · L9 (governance + audit) · L12 (identity, per-agent) — **12/14**

(L11 commerce stays N/A.)

---

### Phase 5 — Polish + 3 incident scenarios + demo (Day 5-7, ~6 hrs)

**Goal:** Demoable end-to-end across all 3 incident types.

**Build steps:**
1. Three mock incident scenarios in `mock_data/scenarios/`:
   - **DB outage** (alert 8472) — primary DB crashed; logs show OOM kill; remediation = failover.
   - **Network partition** (alert 8473) — cross-region split; traces show timeouts; remediation = drain + reroute.
   - **Deploy regression** (alert 8474) — bad config in last deploy; logs show 5xx surge; remediation = rollback.
2. Each scenario gives different tool-call sequences + different specialist paths via the Router → IC delegation.
3. Record a 5-minute screen demo showing all three scenarios + AG-UI dashboard + Phoenix traces + audit log.
4. Write `RETROSPECTIVE.md` — what each layer felt like in code (D3 reflection); surprises; where the standards helped; where they got in the way; what you'd do differently.
5. Update `catalog/ai/agentic-standards-landscape-2026-05.md` with a footer linking back to this project as the D3 application of the D1–D2 catalog.

**Success criteria:** all 3 scenarios run end-to-end; demo recording exists; retrospective written; catalog cross-linked.

**Layers exercised:** all 13 applicable (L11 N/A) — **13/14 ✓** (L6 awareness-only — exercised by *building* LunaSRE in Zed/Cursor, not in the runtime).

---

## 7. Mock data + 3 sample scenarios

### `mock_data/alerts.json`

```json
[
  {
    "alert_id": "8472",
    "type": "db-failure",
    "severity": "critical",
    "service": "payments-api",
    "fired_at": "2026-06-15T03:14:22Z",
    "message": "primary DB connection pool exhausted; replicas catching up but lagging"
  },
  {
    "alert_id": "8473",
    "type": "network-partition",
    "severity": "critical",
    "service": "user-service-cross-region",
    "fired_at": "2026-06-15T03:14:55Z",
    "message": "cross-AZ latency p99 > 10s; suspected partition between us-east-1a and us-east-1b"
  },
  {
    "alert_id": "8474",
    "type": "deploy-regression",
    "severity": "warning",
    "service": "search-api",
    "fired_at": "2026-06-15T03:15:01Z",
    "message": "5xx error rate up 8x since deploy 4f3a2e1 at 03:12"
  }
]
```

### `mock_data/logs/payments-api.jsonl`

```
{"ts": "2026-06-15T03:14:00Z", "level": "ERROR", "msg": "connection pool exhausted, max=200"}
{"ts": "2026-06-15T03:14:05Z", "level": "ERROR", "msg": "OOM kill: process killed by oom-killer"}
{"ts": "2026-06-15T03:14:10Z", "level": "WARN",  "msg": "replica lag > 30s"}
... (50 more lines per scenario)
```

### Expected agent flow for db-failure scenario

```
Router (Ollama)      → classifies as "db-failure" → routes to DB investigation path
IC Agent (xAI Grok)  → calls drill_into_alert(8472), tail_logs("payments-api"),
                       recent_errors("payments-api")
                     → hypothesis: "OOM kill on primary, replicas behind"
IC                   → A2A delegate to RCA: "verify OOM + check replica state"
RCA Agent (Gemini)   → calls get_trace(...), grep_logs("OOM", last 1h),
                       analyzes 5MB of logs with long-context Gemini
                     → returns root cause: "memory leak in connection-pool reaper
                       since deploy 4f3a2e1 06-14 18:00"
IC                   → composes remediation: "rollback payments-api to v2.3.1
                       (last known good); failover to replica during rollout"
HITL gate            → AG-UI dashboard shows the proposed remediation
                       → user clicks Approve
PM Writer (Gemini)   → drafts postmortem
Grok Reviewer (xAI)  → critiques: "draft doesn't address why replicas were behind;
                       add capacity analysis"
PM Writer (Gemini)   → incorporates Grok's critique; finalizes markdown
Memory (Letta)       → stores incident pattern: "payments-api OOM correlates with
                       deploy 4f3a2e1; future similar alerts → check connection-pool
                       reaper first"
Phoenix Observability→ entire trace visible: tokens per LLM call, costs, latencies,
                       tool-call success rate
Audit log            → every tool call logged with agent-id from OAuth token
```

---

## 8. Directory structure (code skeleton)

> **Reconciled 2026-06-03 at L26.P to match `ARCHITECTURE.md` §8.** Src-layout under `src/lunasre/`; root carries `uv` / `pyright` / `ruff` / `just` / `pre-commit` config; `infra/registries/` is new (seed files behind the `Registry` interface that swaps to MCP Gateway in Phase 4); `src/lunasre/registries/` holds the interface + file loader; **8 agent files** in `src/lunasre/agents/` (router + 7 A2A); **4 MCP servers** (was 3 — `mock_pg` added per the 3-specialist topology); **7 Agent Cards** (Router has no card — not an A2A peer).

```
practice/2026-05-27-multi-agent-sre-toy/
├── ARCHITECTURE.md                    (design source-of-truth — read FIRST)
├── README.md                          (this file — phased plan)
├── RETROSPECTIVE.md                   (written at Phase 5 close)
├── .env.example                       (XAI_API_KEY, GOOGLE_API_KEY)
├── pyproject.toml                     (project metadata + [tool.ruff] / [tool.pyright])
├── uv.lock                            (locked dep graph)
├── justfile                           (recipes: dev / test / lint / mcp:run-<name>)
├── ruff.toml · pyrightconfig.json · .pre-commit-config.yaml
├── docker-compose.yml                 (Postgres + Phoenix + Letta + Authelia + Gateway)
├── src/lunasre/
│   ├── __init__.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py                    (config loader + registry resolver — shared)
│   │   ├── configs/                   (config-driven agents — per ARCHITECTURE.md §5)
│   │   │   ├── router.yaml · ic.yaml · dbops.yaml · netops.yaml · deployops.yaml
│   │   │   └── rca.yaml · pm_writer.yaml · grok_reviewer.yaml
│   │   ├── router.py                  (Ollama — Phase 4; graph-first routing node, NOT an A2A peer)
│   │   ├── ic.py                      (xAI Grok — IC supervisor; Phase 1 Chunk 2)
│   │   ├── dbops.py                   (xAI Grok — db-failure specialist; Phase 2)
│   │   ├── netops.py                  (xAI Grok — network-partition specialist; Phase 2)
│   │   ├── deployops.py               (xAI Grok — deploy-regression specialist; Phase 2)
│   │   ├── rca.py                     (Gemini — RCA, long context; Phase 2)
│   │   ├── pm_writer.py               (Gemini — PM finalizer; Phase 3)
│   │   ├── grok_reviewer.py           (xAI — adversarial reviewer; Phase 3)
│   │   └── cards/                     (A2A Agent Cards at /.well-known/agent.json — 7 cards; Router excluded)
│   │       ├── ic.json · rca.json · dbops.json · netops.json · deployops.json
│   │       └── pm_writer.json · grok_reviewer.json
│   ├── mcp_servers/
│   │   ├── mock_datadog/server.py     (fastmcp; Phase 1 Chunk 1)
│   │   ├── mock_logs/server.py        (fastmcp; STATEFUL SESSIONS demo; Phase 2)
│   │   ├── mock_traces/server.py      (fastmcp; Phase 2)
│   │   └── mock_pg/server.py          (fastmcp; Phase 2)
│   ├── registries/                    (Registry INTERFACE — file loader Phase 1, Gateway swap Phase 4)
│   │   ├── mcp_registry.py
│   │   └── agent_registry.py
│   ├── memory/                        (Letta client behind own interface — Phase 2)
│   │   └── letta_client.py
│   ├── observability/                 (OTel GenAI init — Phase 4)
│   │   └── init.py
│   ├── identity/                      (OAuth client for per-agent identity — Phase 4)
│   │   └── oauth_client.py
│   └── runtime/                       (LangGraph compile helpers, checkpointer wiring)
│       └── graph_factory.py
├── infra/
│   ├── registries/                    (registry SEED FILES — read by Phase-1 file loader)
│   │   ├── mcp_registry.yaml          (mock_datadog · mock_logs[supports_sessions: true] · mock_traces · mock_pg)
│   │   └── agent_registry.yaml        (7 A2A agents w/ card_url + capabilities + specialist triggers_on)
│   ├── litellm_config.yaml            (Phase 1 Chunk 2 — Gemini/Grok/Ollama routing)
│   ├── authelia_config.yml            (Phase 4 — OIDC clients)
│   ├── mcp_gateway_config.yaml        (Phase 4 — gateway + agent identities)
│   ├── otel_config.yaml               (Phase 4 — OpenTelemetry GenAI)
│   └── phoenix_init.py                (Phase 4 — Phoenix Docker bootstrap)
├── mock_data/
│   ├── alerts.json                    (alerts 8472 db-failure / 8473 network-partition / 8474 deploy-regression)
│   ├── logs/
│   │   └── payments-api.jsonl · user-service.jsonl · search-api.jsonl
│   ├── traces/
│   │   └── db-failure.json · network-partition.json · deploy-regression.json
│   └── scenarios/                     (expected flows — Phase 5)
│       └── db-failure.md · network-partition.md · deploy-regression.md
├── frontend/                          (Next.js 15 + React 19 + CopilotKit — Phase 3)
│   ├── package.json
│   ├── pages/
│   │   ├── index.tsx                  (incident dashboard)
│   │   └── api/agui.ts                (AG-UI SSE proxy)
│   └── components/
│       └── IncidentStream.tsx · HITLApproval.tsx · PostmortemView.tsx
├── governance/                        (Phase 4)
│   ├── owasp-agentic-checklist.md
│   └── audit_schema.sql
├── tests/
│   └── test_mcp_servers.py · test_agents.py · test_e2e_scenarios.py
└── scripts/
    ├── start_all.sh                   (one-command spin-up)
    └── reset_all.sh                   (clean state for fresh run)
```

---

## 9. Common pitfalls + debug tips

| Pitfall | Symptom | Debug |
|---|---|---|
| LiteLLM proxy crash on Ollama | Router calls fail with connection refused | Verify Ollama is running: `ollama serve` then `curl http://localhost:11434/api/tags` |
| LangGraph state not persisting after crash | Resume gives fresh state | Verify Postgres connection in checkpointer; check `thread_id` is consistent across runs |
| MCP server tool not appearing in agent | Agent doesn't see the tool | Check MCP server is registered with gateway; check `tools/list` response in Phoenix trace |
| A2A delegation fails with auth error | RCA gets 401 | Ensure IC's OAuth token has been refreshed; check Authelia client_credentials grant is configured |
| AG-UI events not streaming to frontend | UI shows blank | Check SSE headers (`Content-Type: text/event-stream`), no buffering proxy in between |
| HITL interrupt doesn't pause | Agent runs through approval step | Verify `interrupt_before=["execute_remediation"]` is on compile, NOT in graph definition |
| Phoenix shows no traces | UI empty | Check `OTEL_EXPORTER_OTLP_ENDPOINT` env var points to Phoenix's OTLP receiver port |
| Long-context Gemini call hangs | RCA stuck | Check log file size; chunk if > 500KB; verify Gemini API key has `gemini-2.5-pro` access |
| Letta memory writes succeed but reads return nothing | "No past incidents found" | Letta's semantic search may need agent embeddings — confirm `archival_memory_search` is being called with relevant query |

---

## 10. Closing artifacts (Phase 5 outputs)

By end of Phase 5 you should have:

1. **Working end-to-end demo** — all 3 scenarios run in the browser
2. **Recorded screen demo** (~5 min) saved to `demo/lunasre-walkthrough.mp4`
3. **`RETROSPECTIVE.md`** — what each layer felt like in code; honest gaps
4. **`governance/owasp-agentic-checklist.md`** filled in
5. **Phoenix trace screenshots** for at least one full incident in `demo/phoenix-traces/`
6. **Audit log dump** for one full incident in `demo/audit-log-sample.json`
7. **Updated catalog** — `catalog/ai/agentic-standards-landscape-2026-05.md` footer linked back to this project as the D3 reference implementation
8. **LinkedIn post draft** in `posts/` — "Building an agentic SRE platform exercises all 14 standards in 1 week — here's what I learned" (per the project's content-cadence discipline)

---

## 11. Stretch goals (only if time allows)

- **Add UCP/AP2 sub-flow** — turn "remediation approval" into a payment-mandate-style flow (overkill for SRE but demonstrates L11 commerce coverage on top — make the 14/14 complete)
- **Deploy to a real K8s cluster** — port from local Docker Compose to a kind cluster, exercise SPIFFE workload identity for L12 at production grade
- **Add Bedrock AgentCore comparison** — run the same workflow under AWS Bedrock AgentCore + LiteLLM for vendor-portability proof
- **Add EU AI Act high-risk classification check** — the IC agent classifies its own decision as high-risk per EU AI Act + emits the required documentation (governance L9 production-grade)

---

## 12. Success criteria for the project as a whole

By Phase 5 close, you should be able to:

1. **Articulate at Director level** how each of the 14 layers feels in real code (D3 fluency, not just D2 reading)
2. **Demo end-to-end** in 5 minutes to a peer or interviewer
3. **Show 12 of 14 layers exercised** in working code; L11 explicitly N/A; L6 used in your dev environment (Zed/Cursor for development)
4. **Cite specific design decisions** at each layer with rationale grounded in the trade-offs from the catalog landscape file
5. **Have honest gaps documented** in RETROSPECTIVE.md — where the standards helped, where they got in the way, what you'd do differently

This is the bridge from D2 reading (the catalog landscape) to D3 application (this project) — exactly the depth a Senior Director / Principal Engineer in SRE walks in able to demonstrate.

---

## 13. Time budget (honest)

| Phase | Estimated hrs | Realistic w/ debugging |
|---|---|---|
| 1. Skeleton | 6 | 8-10 |
| 2. Multi-agent + memory | 7 | 10-12 |
| 3. AG-UI + HITL + Grok | 8 | 11-14 |
| 4. Governance + Obs + Identity + Gateway | 8 | 12-15 |
| 5. Polish + scenarios + demo | 6 | 8-10 |
| **Total** | **35** | **49-61 hrs (1 calendar week at ~7 hrs/day, OR 2 weeks at ~3 hrs/day)** |

**Recommended pace:** spread across 7-10 calendar days at 4-6 hrs/day so you have time to absorb each layer's surprises. Don't try to do 14+ hrs/day — the depth is the point, not the speed.

---

## 14. Pre-flight checklist (run before Phase 1)

- [ ] Docker Desktop running, can pull images
- [ ] Python 3.11+ in a fresh venv
- [ ] Node 20+ + npm
- [ ] Ollama installed + `llama3.1:8b` pulled (or `phi3.5:3.8b` for faster)
- [ ] API keys set: GOOGLE_API_KEY, XAI_API_KEY (no ANTHROPIC key needed for this build)
- [ ] Postgres 16 image pulled
- [ ] Arize Phoenix image pulled
- [ ] Letta image pulled
- [ ] Authelia image pulled
- [ ] `agentic-community/mcp-gateway-registry` repo cloned
- [ ] 50+ GB free disk for Docker volumes
- [ ] An editor with good Python + TypeScript support (Zed, Cursor, or VS Code)
- [ ] Catalog landscape file open as reference: `catalog/ai/agentic-standards-landscape-2026-05.md`

---

## Project status

- **Created:** 2026-05-26 (L24.E close)
- **Build start:** L25.P (next session)
- **Build end target:** L25.P + 6 more practice days
- **Status:** Plan ready; build pending
- **Companion artifact:** `catalog/ai/agentic-standards-landscape-2026-05.md` (the D1-D2 breadth this project applies at D3)
