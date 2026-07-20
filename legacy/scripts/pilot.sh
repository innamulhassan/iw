#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# LunaSRE PILOT — one command boots the whole system, runs one incident
# end-to-end through all 14 layers, and shows you the evidence.
#
#   bash scripts/pilot.sh           # default: alert 8472 (db-failure)
#   bash scripts/pilot.sh 8473      # network-partition
#   bash scripts/pilot.sh 8474      # deploy-regression
#
# What it does:
#   1. ensures the LiteLLM model gateway is up (:4000)
#   2. starts the 4 specialist/RCA A2A servers (:8002-:8005) + web UI (:8080)
#   3. clears prior run state (audit / memory / checkpoints) for a clean demo
#   4. runs the Incident Commander on the alert (supervisor → specialist → RCA → report)
#   5. prints the AUDIT TRAIL (who-did-what across agent processes = governance + identity)
#   6. tells you to open the browser UI for the human-approval step
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

ALERT="${1:-8472}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

say() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()  { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }

mkdir -p .litellm

say "1/6  Model gateway (LiteLLM :4000)"
if curl -s --max-time 2 http://localhost:4000/health/liveliness >/dev/null 2>&1; then
  ok "already up"
else
  nohup uv run litellm --config infra/litellm_config.yaml --port 4000 > .litellm/proxy.log 2>&1 &
  for _ in $(seq 1 40); do
    curl -s --max-time 2 http://localhost:4000/health/liveliness >/dev/null 2>&1 && break; sleep 1
  done
  ok "started"
fi

say "2/6  Specialist + RCA A2A servers (:8002-:8005) + web UI (:8080)"
bash scripts/serve_specialists.sh >/dev/null 2>&1 || true
for port in 8002 8003 8004 8005; do
  for _ in $(seq 1 25); do
    curl -s --max-time 2 "http://localhost:${port}/healthz" | grep -q '"ok"' && break; sleep 1
  done
done
ok "dbops:8003  netops:8004  deployops:8005  rca:8002"
if ! curl -s --max-time 2 http://localhost:8080/healthz | grep -q '"ok"' 2>/dev/null; then
  nohup uv run uvicorn lunasre.web:app --port 8080 > .litellm/web.log 2>&1 &
  for _ in $(seq 1 25); do
    curl -s --max-time 2 http://localhost:8080/healthz | grep -q '"ok"' && break; sleep 1
  done
fi
ok "web UI:8080"

say "3/6  Clear prior run state (clean demo)"
rm -f .lunasre/audit.db .lunasre/hitl_checkpoints.db
ok "audit + checkpoints cleared (memory kept — recall is part of the demo)"

say "4/6  Run the Incident Commander on alert ${ALERT}  (~1-2 min: 3 agents, local model)"
uv run python -m lunasre.agents.ic --alert-id "${ALERT}"

say "5/6  AUDIT TRAIL — who did what (governance L9 + verified identity L12)"
uv run python - <<'PY'
from lunasre.runtime.audit import AuditLog
log = AuditLog()
print(f"  {log.count()} entries across the agent processes:\n")
print(f"  {'agent_id':<15} {'action':<16} target")
print("  " + "-"*64)
for e in reversed(log.recent(40)):
    print(f"  {e['agent_id']:<15} {e['action']:<16} {e['target']}")
PY

say "6/6  Human-in-the-loop — open the browser console"
ok "open http://localhost:8080  → pick an alert → Investigate → Approve/Reject the remediation"
printf "\n\033[1;32mPilot complete.\033[0m  Read DEMO.md (run) + WALKTHROUGH.md (understand the code).\n\n"
