#!/usr/bin/env bash
# Single-use Investigation Workbench demo — starts the LangGraph+xAI backend and the React UI.
#   ./run-demo.sh           then open http://localhost:5180
# Stop with Ctrl-C (kills both). Requires: engine-backend/.env with XAI_API_KEY, node/npm, the venv.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # node/npm aren't on the default non-interactive PATH

# ── backend (FastAPI + LangGraph engine + LLM planner) on :8088 ───────────────
( cd "$HERE/engine-backend"
  set -a; source .env; set +a
  export PYTHONPATH=src
  exec .venv/bin/python -m engine.api.serve ) &
BACK=$!

# ── frontend (Vite React workbench-ui) on :5180 ───────────────────────────────
( cd "$HERE/workbench-ui"
  exec npm run dev -- --port 5180 --strictPort ) &
FRONT=$!

trap 'kill $BACK $FRONT 2>/dev/null' EXIT INT TERM
echo
echo "  Investigation Workbench demo"
echo "  backend  → http://127.0.0.1:8088   (xAI model: \$XAI_MODEL, default grok-3)"
echo "  frontend → http://localhost:5180   ← open this"
echo "  Ctrl-C to stop both."
echo
wait
