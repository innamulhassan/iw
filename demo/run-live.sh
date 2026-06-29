#!/usr/bin/env bash
# Single-use Investigation Workbench demo — the LangGraph+xAI backend serves the console too.
#   ./run-live.sh           then open http://127.0.0.1:8088/ux-console.html?incident=INC-LIVE
# One origin, one URL. Stop with Ctrl-C. Requires: engine-backend/.env with XAI_API_KEY.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PORT="${PORT:-8088}"   # the console hard-codes 127.0.0.1:8088; keep them in sync

# ── backend (FastAPI + LangGraph engine + LLM planner) — also serves ux-console.html ─────────
( cd "$HERE/engine-backend"
  set -a; source .env; set +a
  export PYTHONPATH=src
  exec .venv/bin/python -m engine.api.serve )

echo
echo "  Investigation Workbench"
echo "  → http://127.0.0.1:${PORT:-8088}/ux-console.html?incident=INC-LIVE   (Ctrl-C to stop)"
echo
