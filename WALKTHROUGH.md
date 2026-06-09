# LunaSRE — Code Walkthrough (understand the whole system fast)

> Read this once, top to bottom, and you'll understand the entire 14-layer
> agentic system + know exactly which file does what. ~15 min read.
> Pair with `ARCHITECTURE.md` (deep design) + `DEMO.md` (how to run).

---

## 1. The one-paragraph mental model

LunaSRE is a **graph of LLM agents** that investigate a mock SRE incident. The
**Incident Commander (IC)** is a *supervisor*: it gathers evidence, then **routes**
the alert to the right *specialist worker* (DBOps / NetOps / DeployOps) over the
**A2A** agent-to-agent protocol; an **RCA** agent synthesizes a root cause; IC
writes a report and **pauses for a human to approve** the remediation. Agents
*think* by calling models through one OpenAI-compatible proxy (**LiteLLM**); they
*act* by calling tools in separate **MCP** servers; they *remember* across
incidents (**memory**); and every call is **traced**, **audited**, and **identity-
verified**. The whole thing is config-driven, so each local piece swaps for a
production backend without touching agent code.

---

## 2. The request flow (trace ONE incident through the code)

Run `uv run python -m lunasre.agents.ic --alert-id 8472 --debug` and follow along:

```
main()  ─ agents/ic.py
  └─ run()  builds ICAgent + the supervisor graph, ainvoke()s it
       │
       ▼  the LangGraph supervisor (agents/ic.py + runtime/graph_factory.py):
       │
   ┌─ investigate_node ──────────────────────────────────────────────┐
   │  opens an MCPLiveSession to mock_datadog (runtime/mcp_session.py)│
   │  LLM (via LiteLLM) calls drill_into_alert + tail_logs            │  L1 L2 L3 L7
   │  parses alert_type + service; recalls similar past incidents     │  L10 (memory)
   └─────────────────────────────────────────────────────────────────┘
       │  route_after_investigate()  — deterministic edge
       ▼  alert_type in delegation map?  ── no ──▶ summarize
   ┌─ delegate_node ─────────────────────────────────────────────────┐
   │  agent_registry.find → fetch the specialist's Agent Card          │  L13
   │  A2A POST to e.g. DBOps :8003 with a signed identity token         │  L4 L12
   │  (DBOps runs its own loop over mock_logs, returns findings)        │
   └─────────────────────────────────────────────────────────────────┘
       │
   ┌─ rca_node ──────────────────────────────────────────────────────┐
   │  A2A POST the evidence to RCA :8002 → root-cause synthesis         │  L4
   └─────────────────────────────────────────────────────────────────┘
       │
   ┌─ summarize_node ────────────────────────────────────────────────┐
   │  final LLM call → the incident report + proposed remediation       │
   └─────────────────────────────────────────────────────────────────┘
       │
       ▼  [INTERRUPT before execute_remediation]   ← HITL pause (needs checkpointer)  L7
       │  human Approves/Rejects (CLI run_hitl/resume_hitl OR browser web.py)  L5
       ▼
   ┌─ execute_remediation_node ──────────────────────────────────────┐
   │  approved → simulated execute;  rejected → skip                   │
   └─────────────────────────────────────────────────────────────────┘

Wrapping EVERY tool call + A2A hop above: an OTel span (L8) + an audit-log
entry attributed to a verified agent identity (L9 + L12). Then memory stores
the incident (L10).
```

---

## 3. The 14 layers → which file implements each

| Layer | Concern | File(s) |
|---|---|---|
| **L0** Transport | stdio (MCP) / HTTP (LiteLLM, A2A) / SSE (AG-UI) | (built into the libs; `mcp_session.py` uses stdio, `a2a_*` use HTTP) |
| **L1** Inference | one OpenAI-compatible endpoint over many models | `infra/litellm_config.yaml` + the `AsyncOpenAI` client in `agents/ic.py` / `agents/specialist.py` |
| **L2** Tool schema | JSON-Schema tool defs the model reads | `agents/tool_loop.py` (`to_openai_tools`) |
| **L3** Agent↔tool (MCP) | call tools; discover servers; stateful sessions; gateway scope | `mcp_servers/*/server.py` (servers) · `runtime/mcp_session.py` (client) · `registries/mcp_registry.py` + `registries/gateway.py` (discovery) |
| **L4** Agent↔agent (A2A) | delegate to peer agents | `runtime/a2a_server.py` + `runtime/a2a_client.py` · `registries/agent_registry.py` |
| **L5** Agent↔UI (AG-UI) | stream to a human, take approval | `runtime/a2a_server.py` (SSE concepts) · `web.py` (`/stream`, `/approve`) · `frontend/index.html` |
| **L6** Editor↔agent (ACP) | dev-time only (Zed/Cursor used to build) | — (awareness; not in runtime, by design) |
| **L7** Runtime | the agent loop: graph, interrupt, durable state | `agents/ic.py` (nodes) · `runtime/graph_factory.py` (compile) |
| **L8** Observability | trace every action | `runtime/observability.py` |
| **L9** Governance | audit log + OWASP checklist | `runtime/audit.py` · `governance/owasp-agentic-checklist.md` |
| **L10** Memory | recall/store incident patterns | `runtime/memory.py` |
| **L11** Commerce | N/A (no payments in incident response) | — (by design) |
| **L12** Identity | per-agent workload tokens | `runtime/identity.py` |
| **L13** Capability | self-describing agents (Agent Cards) | `runtime/a2a_server.py` (`AgentCard`) · `agent_registry.yaml` |

---

## 4. Read the code in THIS order (fastest path to understanding)

1. **`agents/ic.py`** — the heart. Start at `run()` / `run_hitl()`, then the
   `ICAgent` node methods (`investigate_node` → `route_after_investigate` →
   `delegate_node` → `rca_node` → `summarize_node` → `execute_remediation_node`).
   This is the whole orchestration in one file.
2. **`runtime/graph_factory.py`** — how those nodes wire into a LangGraph
   (conditional edge + interrupt). ~80 lines.
3. **`agents/base.py`** — config loading (`AgentConfig`), registry resolution
   (`resolve_mcp_servers`, with the file↔gateway swap). The contract every agent shares.
4. **`agents/specialist.py`** — the shared worker loop. DBOps/NetOps/DeployOps are
   3-line files (`agents/dbops.py` etc.) + a YAML config (`agents/configs/*.yaml`).
5. **`agents/tool_loop.py`** — the 4-layer small-model defense (the hard-won bit;
   see §6) + the OpenAI tool-shape helpers.
6. **`runtime/mcp_session.py`** — the single tool-call chokepoint (and where
   audit + tracing hook in for *every* tool call).
7. **`runtime/{a2a_server,a2a_client}.py`** — the agent-to-agent seam (Agent Card
   + `/a2a/message` + identity token attach/verify).
8. **`runtime/{audit,identity,observability,memory}.py`** — the cross-cutting
   planes; each is small + single-purpose.
9. **`web.py` + `frontend/index.html`** — the human-in-the-loop browser layer.
10. **`tests/`** — every concept has a no-LLM test; the test names are a spec.

---

## 5. The patterns worth stealing (the actual learning)

- **Supervisor = deterministic edges + LLM nodes.** The graph *routing* is plain
  Python (`route_after_investigate`); the *work* in each node is an LLM call.
  That split is what makes multi-agent systems debuggable. (`agents/ic.py`)
- **Config-driven agents.** Everything that varies between agents (model, tools,
  prompt, A2A port, scope) is YAML; the code is shared. Adding a specialist =
  a config + a 3-line entrypoint. (`agents/specialist.py` + `configs/*.yaml`)
- **One interface, swappable backing.** Registry (file→gateway), memory
  (SQLite→Letta), checkpointer (SQLite→Postgres), traces (console→Phoenix),
  identity (dev-JWT→Authelia) — each is an interface with a local backing now
  and a production backing by config. Agent code never changes. This is the
  "replicable at a bank" thesis, made literal.
- **Stateful MCP sessions need a live client.** `open_log_session` returns an id
  that later `grep`/`tail` calls reuse — only works if the server subprocess
  stays alive (`MCPLiveSession`), not spawn-per-call. (test proves the contrast)
- **Cross-cutting planes wrap one chokepoint.** Audit + tracing live in
  `MCPLiveSession.call` + the A2A client, so *every* agent's actions are covered
  without touching each agent.
- **HITL = interrupt + checkpointer.** The graph pauses before the side-effecting
  node; state persists; a separate request resumes it. Durable human approval.

---

## 6. The small-model defense (why agents on local models are fragile)

`agents/tool_loop.py` carries a 4-layer defense, earned over L27-L28 because
local 7-8B models don't reliably follow the tool-calling contract:

1. **Tool rotation** — after a tool is used, drop it from the next turn's tool
   list → forces the loop to converge.
2. **Same-tool-same-args refusal** — return a NOTE instead of re-executing.
3. **Content-rescue parser** — recognize a tool-call emitted as plain `content`.
4. **Bare-args inference** — recognize bare arguments (no tool name) by matching
   the key-set to a tool's parameters.

On a frontier model (Grok/Gemini/Claude) these are mostly unnecessary — which is
why the model is one config line away. This pattern is documented as a reusable
reference in the learning playbook.

---

## 7. Where things live (directory map)

```
src/lunasre/
  agents/      ic.py (supervisor) · specialist.py (shared worker) ·
               dbops/netops/deployops/rca.py (thin) · base.py · tool_loop.py · configs/*.yaml
  mcp_servers/ mock_datadog · mock_logs (stateful) · mock_traces · mock_pg
  registries/  mcp_registry.py · agent_registry.py · gateway.py
  runtime/     graph_factory · mcp_session · a2a_server · a2a_client ·
               memory · audit · identity · observability
  web.py       FastAPI AG-UI server
infra/registries/  mcp_registry.yaml · agent_registry.yaml · gateway_scopes.yaml
mock_data/   alerts.json · logs/*.jsonl · traces/*.json
governance/  owasp-agentic-checklist.md
frontend/    index.html (vanilla AG-UI console)
tests/       11 files, ~84 tests, no LLM needed
```

That's the whole system. Run the pilot, skim `agents/ic.py`, and the rest falls
into place.
