#!/bin/bash
# ⚡ Quick-demo launcher — double-click this file.
# Starts the local server (if needed) and opens the worked walkthrough INC-2256 in your browser.
cd "$(dirname "$0")" || exit 1
PORT=8000
URL="http://localhost:$PORT/viewer.html?incident=INC-2256"

# already running? just open it.
if curl -s "http://localhost:$PORT/viewer.html" >/dev/null 2>&1; then
  echo "Server already running — opening INC-2256."
  open "$URL"
  exit 0
fi

# open the URL as soon as the server is up
( for i in $(seq 1 40); do
    curl -s "http://localhost:$PORT/viewer.html" >/dev/null 2>&1 && { open "$URL"; break; }
    sleep 0.25
  done ) &

echo "Investigation Workbench — serving on http://localhost:$PORT"
echo "Opening INC-2256… (leave this window open; close it to stop the demo)"
python3 -m http.server "$PORT"
