# LunaSRE — Demo / Run Guide

A multi-agent SRE incident-investigation system that exercises **all 14 layers of
the agentic-AI stack** (MCP, A2A, AG-UI, LangGraph, OpenTelemetry, OWASP-Agentic,
OAuth identity, …). Runs locally on Ollama — no API keys required.

> **New here? Two files:** this one (how to RUN) and `WALKTHROUGH.md` (how to
> UNDERSTAND the code). `ARCHITECTURE.md` is the deep design reference.

---

## One command (the pilot)

```bash
cd practice/2026-05-27-multi-agent-sre-toy
bash scripts/pilot.sh            # alert 8472 (db-failure); or pass 8473 / 8474
```

The pilot boots everything, runs one incident end-to-end, and prints the audit
trail. Takes ~1–2 minutes (3 agents, each calling a local 8B model). Then open
**http://localhost:8080** for the human-approval step.

Prereqs: `uv`, Python 3.12, Ollama running with `ministral-3:8b`. (Docker only
needed for the optional real backends — see the swap table below.)

---

## What you'll see the pilot do

1. **Model gateway** (LiteLLM :4000) — one OpenAI-compatible endpoint over Ollama.
2. **5 agents come up** as A2A peers: IC (supervisor) + DBOps/NetOps/DeployOps (specialists, :8003-:8005) + RCA (:8002).
3. **The Incident Commander runs:** investigates the alert with its own tools → routes to the right specialist by alert type → the specialist gathers deeper evidence → RCA synthesizes a root cause → IC writes the report → **pauses for human approval**.
4. **The audit trail prints** — every tool call + every agent-to-agent hop, attributed to a *verified* agent identity, across three separate processes.
5. **The browser console** lets you watch it live and click Approve/Reject.

---

## Run it different ways

```bash
# all 84 tests + lint
uv run pytest -q
uv run ruff check

# one incident, full step log
uv run python -m lunasre.agents.ic --alert-id 8472 --debug

# the human-in-the-loop gate, headless (pause → approve → execute)
uv run python - <<'PY'
import asyncio; from lunasre.agents.ic import run_hitl, resume_hitl
async def m():
    tid, paused = await run_hitl("8472")
    print("paused; proposed remediation:", (paused["proposed_remediation"] or "")[:80])
    final = await resume_hitl("8472", tid, approved=True)
    print("executed:", final["executed"])
asyncio.run(m())
PY

# the audit trail from the last run
uv run python -c "from lunasre.runtime.audit import AuditLog as A; [print(e['agent_id'],e['action'],e['target']) for e in reversed(A().recent())]"

# see the gateway enforce per-agent tool scope (no LLM)
uv run python -c "from lunasre.registries import load_gateway_registry as g; r=g('infra/registries/mcp_registry.yaml','ic-agent','infra/registries/gateway_scopes.yaml'); print('IC scope:', [e.name for e in r.all()])"

# one specialist alone
uv run python -m lunasre.agents.dbops --debug-investigate --service payments-api

# start the servers manually (the pilot does this for you)
bash scripts/serve_specialists.sh
uv run uvicorn lunasre.web:app --port 8080
```

---

## The 3 mock incidents

| Alert | Type | Service | Specialist | Evidence source |
|---|---|---|---|---|
| 8472 | db-failure | payments-api | DBOps | mock_logs (connection-pool exhaustion → OOM → reaper-thread blocked) |
| 8473 | network-partition | user-service-cross-region | NetOps | mock_traces (cross-AZ timeouts us-east-1b) |
| 8474 | deploy-regression | search-api | DeployOps | mock_logs (5xx surge ↔ deploy 4f3a2e1, NPE in analyzer.v2) |

---

## "Local now, production by config swap" — the core thesis

Every local backing has a documented one-line swap. **Agent code never changes.**

| Layer | Local (what runs in the pilot) | Production swap |
|---|---|---|
| L1 model | LiteLLM → Ollama | flip `model:` in a config YAML → Grok / Gemini / Claude / Bedrock |
| L3 tool registry | file `mcp_registry.yaml` | `registries.kind: gateway` → real MCP Gateway |
| L8 traces | console / in-memory | `LUNASRE_OTLP_ENDPOINT` → Arize Phoenix / Datadog |
| L10 memory | SQLite (`MemoryStore`) | Letta / Mem0 / Zep behind the same interface |
| L12 identity | local dev JWT | Authelia / Okta OIDC (same verify path) |
| L7 durability | SQLite checkpointer | Postgres checkpointer |

Set `LUNASRE_ENFORCE_IDENTITY=1` to make the A2A layer reject unauthenticated calls (strict zero-trust).
