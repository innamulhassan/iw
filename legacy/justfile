# LunaSRE — task runner + service entry points. Run `just` (no args) to list everything.
# Every recipe is a uv command; `just --list` is your menu of entry points.

default:
    @just --list

# ─── setup / quality ─────────────────────────────────────────────────────────
install:                      # install runtime + dev deps + create .venv
    uv sync
lint:                         # lint + format check
    uv run ruff check
    uv run ruff format --check
fix:                          # auto-fix lint + format
    uv run ruff check --fix
    uv run ruff format
types:                        # type check
    uv run pyright
test:                         # run all tests
    uv run pytest -v

# ─── the pilot (boots everything + runs one incident) ────────────────────────
pilot alert="8472":           # one-command demo: just pilot   /   just pilot 8473
    bash scripts/pilot.sh {{alert}}

# ─── L1: model gateway (start FIRST — everything depends on it) ───────────────
litellm:                      # LiteLLM proxy on :4000 (OpenAI-compatible over Ollama)
    uv run litellm --config infra/litellm_config.yaml --port 4000

# ─── the agents ──────────────────────────────────────────────────────────────
ic alert="8472":              # IC supervisor — the orchestrator (NOT a server; runs one incident)
    uv run python -m lunasre.agents.ic --alert-id {{alert}}
ic-debug alert="8472":        # IC with full step log
    uv run python -m lunasre.agents.ic --alert-id {{alert}} --debug

# ─── A2A specialist + RCA servers (long-running; each its own port) ───────────
serve-all:                    # start all 4 A2A servers in the background
    bash scripts/serve_specialists.sh
dbops:                        # DBOps specialist  → A2A server on :8003
    uv run python -m lunasre.agents.dbops --serve
netops:                       # NetOps specialist → A2A server on :8004
    uv run python -m lunasre.agents.netops --serve
deployops:                    # DeployOps specialist → A2A server on :8005
    uv run python -m lunasre.agents.deployops --serve
rca:                          # RCA synthesis agent → A2A server on :8002
    uv run python -m lunasre.agents.rca --serve

# ─── L5: web UI (AG-UI SSE server) ───────────────────────────────────────────
web:                          # browser console on :8080 (http://localhost:8080)
    uv run uvicorn lunasre.web:app --port 8080

# ─── MCP servers (normally spawned by agents over stdio; run standalone to inspect) ──
mcp-run name:                 # e.g. `just mcp-run mock_datadog` (mock_logs / mock_traces / mock_pg)
    uv run python -m lunasre.mcp_servers.{{name}}.server

# ─── debug single agents (no IC, in-process) ─────────────────────────────────
smoke-datadog:                # call mock_datadog tools directly (no MCP transport, no LLM)
    uv run python -c "from lunasre.mcp_servers.mock_datadog.server import drill_into_alert, tail_logs; import json; print(json.dumps(drill_into_alert('8472'), indent=2)); print('---'); print(json.dumps(tail_logs('payments-api'), indent=2))"
dbops-debug service="payments-api":   # run DBOps alone, in-process (no HTTP)
    uv run python -m lunasre.agents.dbops --debug-investigate --service {{service}}
rca-debug:                    # run RCA synthesis alone, in-process
    uv run python -m lunasre.agents.rca --debug-synthesize
