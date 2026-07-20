# LunaSRE — Services & Entry Points

It's a standard **uv** project. `uv run <cmd>` runs inside the project venv (no
manual activation). `just` (no args) lists every recipe; each recipe is the
`uv run` command shown here. From the project root:

```
cd practice/2026-05-27-multi-agent-sre-toy
```

---

## Start order (dependencies)

```
1. LiteLLM proxy (:4000)        ← model gateway; everything needs it
2. A2A servers (:8002-:8005)    ← specialists + RCA; IC delegates to them
3. Web UI (:8080)  AND/OR  IC   ← the entry the human/CLI drives
```

`bash scripts/pilot.sh` does all of this for you. The list below is for running
each piece by hand.

---

## Every service / entry point

| # | Service / entry | Port | `uv run` command | `just` |
|---|---|---|---|---|
| 1 | **LiteLLM proxy** (L1 model gateway) | 4000 | `uv run litellm --config infra/litellm_config.yaml --port 4000` | `just litellm` |
| 2 | **DBOps** specialist (A2A server) | 8003 | `uv run python -m lunasre.agents.dbops --serve` | `just dbops` |
| 3 | **NetOps** specialist (A2A server) | 8004 | `uv run python -m lunasre.agents.netops --serve` | `just netops` |
| 4 | **DeployOps** specialist (A2A server) | 8005 | `uv run python -m lunasre.agents.deployops --serve` | `just deployops` |
| 5 | **RCA** synthesis agent (A2A server) | 8002 | `uv run python -m lunasre.agents.rca --serve` | `just rca` |
| 6 | **all 4 A2A servers** (background) | 8002-8005 | `bash scripts/serve_specialists.sh` | `just serve-all` |
| 7 | **Web UI** (AG-UI SSE server) | 8080 | `uv run uvicorn lunasre.web:app --port 8080` | `just web` |
| 8 | **IC supervisor** (orchestrator — *client*, not a server; runs one incident) | — | `uv run python -m lunasre.agents.ic --alert-id 8472` | `just ic 8472` |
| 9 | **the pilot** (boots 1-7 + runs 8) | — | `bash scripts/pilot.sh 8472` | `just pilot 8472` |

**MCP servers** (`mock_datadog` / `mock_logs` / `mock_traces` / `mock_pg`) are
**not** started by hand in normal use — each agent spawns the one it needs as a
**stdio subprocess** (see `runtime/mcp_session.py`). To run one standalone for
inspection:

```bash
uv run python -m lunasre.mcp_servers.mock_logs.server     #  just mcp-run mock_logs
```

---

## IC flags (the orchestrator)

```bash
uv run python -m lunasre.agents.ic --alert-id 8472        # run one incident (8472/8473/8474)
uv run python -m lunasre.agents.ic --alert-id 8472 --debug      # full step log
uv run python -m lunasre.agents.ic --alert-id 8472 --no-memory  # skip incident memory recall/store
uv run python -m lunasre.agents.ic --alert-id 8472 --durable    # compile with the SQLite checkpointer
```

The human-in-the-loop pause (`run_hitl` / `resume_hitl`) is driven by the **web
UI** (`just web` → http://localhost:8080) or programmatically (see `DEMO.md`).

---

## Debug single agents (no IC, in-process — no servers needed except LiteLLM)

```bash
uv run python -m lunasre.agents.dbops --debug-investigate --service payments-api   # just dbops-debug
uv run python -m lunasre.agents.rca   --debug-synthesize                           # just rca-debug
just smoke-datadog        # call mock_datadog tools directly — no MCP transport, no LLM
```

---

## Setup / quality

```bash
uv sync               # install deps + venv          (just install)
uv run pytest -v      # all 84 tests                 (just test)
uv run ruff check     # lint                         (just lint)
uv run pyright        # type check                   (just types)
```

---

## Environment knobs (config swaps — no code change)

| Env var | Effect |
|---|---|
| `XAI_API_KEY` | set, then flip a config `model:` to `grok-3` → agents use Grok instead of Ollama |
| `GOOGLE_API_KEY` | set, then flip RCA's `model:` to `gemini-2.5-pro` |
| `LUNASRE_OTLP_ENDPOINT` | send OTel traces to Phoenix/Datadog instead of console |
| `LUNASRE_TRACE_CONSOLE=1` | print OTel spans to the console |
| `LUNASRE_ENFORCE_IDENTITY=1` | strict zero-trust: A2A rejects unauthenticated calls |

Registry backing (file ↔ MCP Gateway) is a config flip in an agent's
`registries.kind: file|gateway` — see `agents/configs/*.yaml`.
