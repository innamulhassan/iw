#!/usr/bin/env bash
# Start all LunaSRE specialist + RCA A2A servers in the background.
# LiteLLM proxy must already be running on :4000.
set -e
cd "$(dirname "$0")/.."
mkdir -p .litellm
for spec in dbops:8003 netops:8004 deployops:8005 rca:8002; do
  name="${spec%%:*}"; port="${spec##*:}"
  if curl -s --max-time 1 "http://localhost:${port}/healthz" >/dev/null 2>&1; then
    echo "  ${name} already up on :${port}"
  else
    nohup uv run python -m "lunasre.agents.${name}" --serve > ".litellm/${name}.log" 2>&1 &
    echo "  started ${name} on :${port} (pid $!)"
  fi
done
