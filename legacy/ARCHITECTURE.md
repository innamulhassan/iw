---
project: LunaSRE — Architecture & 14-Layer Mental Model
written: 2026-06-01 (L25.P, before any code)
status: SOURCE OF TRUTH for the upgraded (enterprise) design
supersedes: README §4 (tech stack), §6 (build steps where they differ), §8 (directory layout) — README reconcile pending
companion: catalog/ai/agentic-standards-landscape-2026-05.md (D1–D2 breadth → this project is the D3 application)
---

# LunaSRE — Architecture & 14-Layer Mental Model

> Read this **before** writing any code. It is the map: the 14 layers, what we use at each, *why*, and — most importantly — **how they touch each other**. The per-layer detail only makes sense once you can trace one incident end-to-end through the whole stack (§1).

---

## 0. The one-paragraph mental model

LunaSRE is a **graph of LLM-powered agents** (L7 LangGraph) that investigate a mock incident. Agents **think** by calling models through one OpenAI-compatible proxy (L1 LiteLLM). They **act** by calling tools that live in separate MCP servers (L3), whose schemas they read as JSON (L2), over a wire that is stdio or HTTP (L0). They **collaborate** by delegating to peer agents (L4 A2A), finding each other through capability descriptions (L13 Agent Cards) indexed in a **registry** (the enterprise discovery mechanism). A **human watches and approves** through a streaming web UI (L5 AG-UI over SSE). Wrapping all of it are three production planes that touch *every* call: **observability** (L8 OTel→Phoenix), **governance** (L9 OWASP + audit log), and **identity** (L12 OAuth/OIDC — every agent is a workload identity). **Memory** (L10 Letta) persists patterns across incidents. **Commerce** (L11) never fires — there are no payments in incident response. **L6** (editor↔agent) is the plane we *build* in (Zed/Cursor), not part of the running product. The whole thing is **config-driven** and **discovery-based** so the local mocks swap for real banking services without touching agent code.

---

## 1. The data-flow narrative — one incident through all 14 layers

This is the spine. Follow alert **8472** (db-failure on `payments-api`) from page to postmortem. Each step names the layer(s) that light up.

1. **Alert arrives** — a mock page for 8472 enters the graph.
2. **Router (Ollama, local)** classifies it → `db-failure`. → **L1** (inference, via LiteLLM proxy) · **L7** (first graph node). *Cheap + local + private classification tier.*
3. **Route to IC Agent (Grok)** — IC is the **supervisor**. → **L7** (graph edge).
4. **IC needs tools** → asks the **MCP registry** which servers exist and what they expose. → **L3 discovery**. Registry returns `mock_datadog`, `mock_logs`, `mock_traces`, `mock_pg` + their capabilities. *Not hardcoded — resolved at runtime.*
5. **IC calls `drill_into_alert(8472)`** on `mock_datadog`. The call is **MCP / JSON-RPC** (**L3**) riding **stdio** locally / **HTTP** via the gateway (**L0**). The tool's input/output shape came from `tools/list` as **JSON Schema** (**L2**).
6. **IC opens a stateful MCP session** on `mock_logs`: `open_log_session(window, "payments-api") → session_id`; then `grep(session_id, "OOM")` inherits that window. → **L3 sessions** (stateful, not one-shot). *This is the MCP-session demo.*
7. **IC forms a hypothesis** ("OOM on primary, replicas behind") and, based on `type=db-failure`, consults the **agent registry** for a peer whose **Agent Card** advertises a `db-incident` capability → **DBOps specialist**. → **L13** (capability description) · **L4 discovery**.
8. **IC delegates to DBOps via A2A** — sends an A2A task to a peer agent that has its own card at `/.well-known/agent.json`. DBOps runs DB-specific tools, returns findings. → **L4** (agent↔agent) · **L13**. *Supervisor + specialist-workers pattern.*
9. **IC escalates deep analysis to RCA Agent (Gemini, long context)** via A2A — RCA reads ~5 MB of logs in one context window, returns root cause ("memory leak in connection-pool reaper since deploy 4f3a2e1"). → **L4** · **L1** (long-context model choice).
10. **Every** model call, tool call, and A2A handoff so far emitted an **OpenTelemetry GenAI span** → visible in **Arize Phoenix** with tokens, cost, latency. → **L8**.
11. **Each agent authenticated at startup** — acquired an **OAuth token (client-credentials) from Authelia**; the token *is* the agent's workload identity. The **MCP Gateway** validates the token and enforces **per-agent scope** (IC gets all tools; a future audit-only agent gets read-only). → **L12** (identity + trust boundary) · **L9** (scope governance).
12. **IC composes a remediation** ("rollback to v2.3.1 + failover to replica"). Before executing, the graph hits **`interrupt_before=["execute_remediation"]`** and **pauses**. → **L7 HITL**.
13. **The pause surfaces in the browser** as an **AG-UI human-input event** streamed over **SSE** to the **CopilotKit/React** dashboard. The human clicks **Approve**; `update_state(...)` resumes the graph. → **L5** (agent↔UI) · **L0** (SSE).
14. **PM Writer (Gemini)** drafts the postmortem. **Grok Reviewer (xAI)** critiques it adversarially — *deliberately cross-vendor* so it's a genuine second opinion, not a model grading itself. PM Writer incorporates the critique and finalizes. → **L4** (agent↔agent again).
15. **Letta stores the incident pattern** ("payments-api OOM ↔ deploy 4f3a2e1; check connection-pool reaper first") for future recall. → **L10** (memory — distinct from runtime state).
16. **Every tool call was written to `audit_log`** with the agent-id taken from its OAuth token. → **L9** (governance/audit).
17. **The LangGraph Postgres checkpointer** persisted state at every node — kill the process mid-incident, restart, it resumes from the last checkpoint. → **L7** (durability).
18. **L11 (commerce)** — never touched. No payments in incident response. **N/A by design.**
19. **L6 (editor↔agent)** — not in this runtime. It's the **Zed/Cursor + ACP** plane we used to *write* LunaSRE. **Awareness-level.**
20. **Throughout, L1 went through the LiteLLM proxy** — Ollama, Grok, Gemini all behind one `/v1/chat/completions`. Adding Claude later = **one config line** (the L1 portability lesson).

**If you can retell steps 1–20 from memory, you have the D3 mental model.** Everything below is reference detail for that narrative.

---

## 2. Layer reference — what we use, why, how it connects

| Layer | Component in LunaSRE | Tech | Why this choice | Connects to |
|---|---|---|---|---|
| **L0** Transport | JSON-RPC/stdio (local MCP), HTTP (LiteLLM, gateway), SSE (AG-UI) | built-in | Match wire to context: stdio for local subprocess, HTTP for remote, SSE for one-way streaming through enterprise proxies | carries L1, L3, L5 |
| **L1** Inference | LiteLLM proxy → Ollama / Grok / Gemini | LiteLLM | One OpenAI-compatible endpoint = vendor portability; swap/add models by config | feeds every agent (L7); rides L0/HTTP |
| **L2** Tool description | JSON-Schema tool defs from MCP `tools/list` | JSON Schema | The contract the model reads to decide tool calls; quality drives selection accuracy | the L1↔L3 seam |
| **L3** Agent↔tool | 3–4 mock MCP servers + gateway + **registry** + **sessions** | `fastmcp`, official `mcp` SDK | Real MCP server code; registry = runtime discovery; gateway = trust boundary; sessions = stateful tools | discovered via registry; secured by L12; observed by L8 |
| **L4** Agent↔agent | IC → DBOps/NetOps/DeployOps → RCA → PM/Reviewer | `a2a-sdk` | Supervisor+specialists; cross-vendor adversarial review; opacity (delegate without exposing internals) | discovers peers via L13 cards in registry |
| **L5** Agent↔UI | React dashboard streaming agent state + HITL prompts | CopilotKit + AG-UI/SSE | Real UI rendering of streaming tokens, tool calls, approval dialogs | rides L0/SSE; drives L7 HITL |
| **L6** Editor↔agent | (Zed/Cursor used to build LunaSRE) | ACP | Dev-time plane, **not product runtime** — awareness only | N/A at runtime |
| **L7** Runtime | LangGraph: StateGraph, Postgres checkpointer, `interrupt_before`, `Send` | LangGraph (latest) + Postgres 16 | The spine that sequences everything; durability + HITL + fan-out | orchestrates L1/L3/L4; pauses for L5 |
| **L8** Observability | OTel GenAI spans → Arize Phoenix | OpenInference/OpenLLMetry + Phoenix | Full traceability of agent decisions — mandatory for bank audit | wraps every L1/L3/L4 call |
| **L9** Governance | OWASP Agentic Top-10 checklist + `audit_log` table | manual checklist + Postgres | What governance feels like in code; per-agent scope + audit trail | uses L12 identity for audit attribution |
| **L10** Memory | Letta — past-incident pattern recall | Letta (Docker) | Memory ≠ runtime-state; recall across incidents via your own interface | read/written by agents (L7), distinct from checkpointer |
| **L11** Commerce | **N/A** (no payments) | (skip) | Sound architectural non-coverage; understand *why* it doesn't apply | — |
| **L12** Identity | Authelia OIDC + per-agent OAuth client-creds | Authelia (Docker) | Every agent = workload identity; agent-identity vs user-identity; zero-trust | gates L3 (gateway), attributes L9 audit |
| **L13** Capability | Agent Cards at `/.well-known/agent.json`, indexed in agent registry | JSON | Self-describing agents → discovery + capability routing | how L4 finds peers |

**How the layers group (the connective tissue):**
- **L0** carries everything.
- **L1 / L2** = how agents *think* and how tools *describe themselves*; their meeting point is the function-calling seam.
- **L3 / L4 / L13** = the *integration triangle* — agent→tool, agent→agent, and the capability-description that lets both discover each other (via registries).
- **L7** = the *spine* that sequences it all; **L5** = how a human watches/intervenes; **L6** = how a developer builds it.
- **L8 / L9 / L12** = the *cross-cutting production planes* — observability, governance, identity — each wraps every call (see §10).
- **L10** = memory across incidents; **L11** = commerce (absent here).

---

## 3. Agent topology — Supervisor + Specialist-Workers (7 agents)

```
                         Router (Ollama, local)
                              │ classify alert type
                              ▼
                      ┌──────────────┐
                      │  IC Agent    │  supervisor (xAI Grok)
                      │ (Grok)       │
                      └──────┬───────┘
            A2A delegate by alert type (capability-routed)
        ┌──────────────┬──────────────┬──────────────┐
        ▼              ▼              ▼              │
  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
  │ DBOps    │  │ NetOps   │  │ DeployOps│          │ A2A escalate
  │ (Grok)   │  │ (Grok)   │  │ (Grok)   │          ▼
  └────┬─────┘  └────┬─────┘  └────┬─────┘   ┌──────────────┐
       └─────────────┴─────────────┘────────▶│  RCA Agent   │ (Gemini, long ctx)
                  findings                    └──────┬───────┘
                                                     │ root cause
                                                     ▼
                                          ┌────────────────────┐
                                          │ HITL interrupt      │ ◀── human approves (L5)
                                          └─────────┬──────────┘
                                                    ▼
                                  ┌──────────┐   critique   ┌────────────────┐
                                  │PM Writer │ ◀──────────── │ Grok Reviewer  │
                                  │(Gemini)  │ ─────────────▶│ (xAI)          │
                                  └──────────┘   finalize    └────────────────┘
```

- **Specialist routing** is by alert `type`: `db-failure → DBOps`, `network-partition → NetOps`, `deploy-regression → DeployOps`. The mapping lives in IC's **config**, and the specialist is **discovered from the agent registry by capability**, not hardcoded.
- **Cross-vendor by design:** author (Gemini PM Writer) ≠ critic (Grok Reviewer), so the adversarial review is a genuine second opinion.

---

## 4. Runtime discovery — registries (the enterprise decoupling)

The single most "enterprise" property: **agents never hardcode where a tool or peer lives.** They resolve it at runtime from a registry.

- **`infra/registries/mcp_registry.yaml`** — servers with `name / description / transport / command|url / capabilities / auth / supports_sessions`. Seed: `mock_datadog`, `mock_logs` (`supports_sessions: true`), `mock_traces`, `mock_pg`.
- **`infra/registries/agent_registry.yaml`** — agents with `card_url` (`/.well-known/agent.json`) + `capabilities` + (for specialists) `triggers_on`. Seed: `ic-agent`, `rca-agent`, `dbops-agent` (db-failure), `netops-agent` (network-partition), `deployops-agent` (deploy-regression), `pm-writer`, `grok-reviewer`.

**Phase-1** = file-based registries behind a small `Registry` interface. **Phase-4** = swap the file loader for the **MCP Gateway** registry — *same interface, no agent code change*. That swap-without-rewrite is the replicability proof in miniature.

---

## 5. Config-driven agents

Each agent loads `agents/configs/<id>.yaml` at startup — no behavior baked into code. Shape (IC example):

```yaml
agent_id: ic-agent
role: incident-commander
llm:        { provider: xai, model: grok-3, via_proxy: http://localhost:4000 }
registries: { mcp: infra/registries/mcp_registry.yaml, agent: infra/registries/agent_registry.yaml }
tools:      { use_capabilities: [alert-drill, log-tail] }
delegation: { by_alert_type: { db-failure: dbops-agent, network-partition: netops-agent, deploy-regression: deployops-agent } }
memory:        { provider: letta, url: http://localhost:8283 }
observability: { otlp: http://localhost:4317 }
identity:      { oauth: client_credentials, issuer: http://localhost:9091, client_id: agent-ic, client_secret_env: AGENT_IC_SECRET }
runtime:       { checkpointer: postgres, human_in_loop: { interrupt_before: [execute_remediation] } }
```

Same in prod: point `via_proxy`, `issuer`, `otlp`, `url` at real services; secret comes from Vault instead of env. **No code change.**

---

## 6. MCP stateful sessions (the L3 depth demo)

`mock_logs` exposes a **session lifecycle**, contrasted with stateless one-shot tools:

- One-shot (re-specify everything each call): `tail_logs(service, window, pattern)` → re-reads/re-filters every time.
- **Stateful session:** `open_log_session(window_start, window_end, service) → session_id`; `grep(session_id, pattern)` reuses the opened window/cursor; `close_log_session(session_id)` releases resources.

Why it matters: real observability tools hold expensive context (a parsed time-window, a cursor, a connection). Sessions let an agent do many cheap follow-up queries against one established context — and teach the stateful side of MCP that one-shot examples hide.

---

## 7. Enterprise replicability — local → prod swap

Standard protocol at every seam → local↔prod is **config, not a rewrite**.

| Seam | LunaSRE (local) | Prod swap (Wells Fargo) | Cost |
|---|---|---|---|
| L1 inference | LiteLLM → Ollama/Grok/Gemini | LiteLLM/Portkey → Bedrock/Vertex/on-prem | config line |
| L3 tools | mock MCP servers (stdio) | real internal-tool MCP servers (HTTP) | new servers, **same client** |
| L3 discovery | file `mcp_registry.yaml` | MCP Gateway / service mesh | **same registry interface** |
| L4 peers | local A2A agents | cross-team A2A agents | **same protocol + cards** |
| L7 durability | Postgres checkpointer (Docker) | managed Postgres (RDS/CloudSQL) | connection string |
| L8 traces | Arize Phoenix | Datadog/Grafana via OTel | OTLP endpoint env var |
| L10 memory | Letta (Docker) | Letta cluster / managed | endpoint |
| L12 identity | Authelia OIDC | Okta / PingFederate / Entra ID | issuer URL |
| secrets | `.env` | Vault / Secrets Manager | secret source |

---

## 8. Tech stack (upgraded) + project layout

**Stack (supersedes README §4):** Python ≥3.11 · **uv** (pkg mgr) · Ruff (lint/format) · Pyright (types) · Pydantic v2 (models/config) · structlog (logging) · `just` (task runner) · pre-commit · pytest + pytest-asyncio · LangGraph (latest) · LiteLLM · `fastmcp` + official `mcp` SDK · `a2a-sdk` · Letta · Postgres 16 · Arize Phoenix · Authelia · CopilotKit / Next.js 15 / React 19 · Docker Compose v2.

**Layout (src-layout; supersedes README §8):**

```
practice/2026-05-27-multi-agent-sre-toy/
├── ARCHITECTURE.md          (this file — design source of truth)
├── README.md                (the phased plan; §4/§6/§8 reconcile pending)
├── pyproject.toml · uv.lock · justfile · ruff.toml · pyrightconfig.json · .pre-commit-config.yaml
├── docker-compose.yml       (Postgres + Phoenix + Letta + Authelia + Gateway)
├── src/lunasre/
│   ├── agents/{base.py, configs/*.yaml, router.py, ic.py, dbops.py, netops.py, deployops.py, rca.py, pm_writer.py, grok_reviewer.py}
│   ├── mcp_servers/{mock_datadog/, mock_logs/, mock_traces/, mock_pg/}/server.py
│   ├── registries/{mcp_registry.py, agent_registry.py}   (the Registry interface + file loader)
│   ├── memory/ · observability/ · identity/ · runtime/
├── infra/registries/{mcp_registry.yaml, agent_registry.yaml}
├── mock_data/{alerts.json, logs/, traces/, scenarios/}
├── tests/{test_mcp_servers.py, test_agents.py, test_e2e_scenarios.py}
└── frontend/                (Next.js 15 + CopilotKit — Phase 3)
```

**uv workflow:** `uv init` → `uv add langgraph litellm fastmcp mcp a2a-sdk pydantic structlog` → `uv add --dev ruff pyright pytest pytest-asyncio pre-commit` → `uv sync` → `uv run python -m lunasre.agents.ic` → `uv run pytest`.

---

## 9. Scope decisions (honest flags)

- **L6 (editor↔agent / ACP)** — dev-time only. Exercised by *using* Zed/Cursor to build LunaSRE; documented in `RETROSPECTIVE.md`. Forcing it into the SRE runtime would be artificial.
- **L11 (commerce / AP2)** — N/A; no payments in incident response. Stretch goal: turn HITL approval into an AP2 mandate for a vanity 14/14.
- **Agent evaluation** — partially covered (Grok reviewer = adversarial judge; Phase-5 scenarios = golden paths; `tests/`). A systematic eval harness is an *enhancement*, noted as "what production adds."
- **Secrets** — `.env` locally, Vault in prod (a conscious swap, §7).

---

## 10. Cross-cutting planes — they wrap *every* call

L8/L9/L12 are not "a phase" — they are aspects layered over the whole graph:

- **Identity (L12):** every agent acquires an OAuth token at startup; the token is its identity on every tool/peer call.
- **Governance (L9):** the gateway enforces per-agent tool scope; every tool call is written to `audit_log` attributed by that token; OWASP Agentic Top-10 is a review checklist with a documented posture per item.
- **Observability (L8):** every model call, tool call, and A2A handoff emits an OTel GenAI span → Phoenix shows the full incident as one trace (tokens, cost, latency, success).

Build them in Phase 4, but understand them as **wrappers around steps 5–16 of §1**, not as a bolt-on at the end.
