#!/usr/bin/env bash
# mutual_watch.sh — one monitor, both agents. Restarts a dead poll loop via
# watch_inbox.sh (which supervises), and chases flagged mail older than 10 min
# with an UNACKNOWLEDGED note in the shared log. It cannot type into an agent
# window; when a chase is due it also appends WAKE-NEEDED so setup-comms.sh's
# report shows the gap plainly.
set -uo pipefail
cd "$(dirname "$0")/.."
BOX="$PWD/.comms"
while true; do
  sleep 60
  for a in deepseek kimi; do
    pgrep -f "comms.sh poll $a" >/dev/null 2>&1 || \
      echo "$(date +%H:%M:%S) $a poll DEAD — supervisor should restart; if not, run ./setup-comms.sh" >> "$BOX/mutual_watch.log"
    if [ -f "$BOX/ATTENTION.$a" ]; then
      age=$(( $(date +%s) - $(stat -f %m "$BOX/ATTENTION.$a" 2>/dev/null || echo 0) ))
      if [ "$age" -gt 600 ]; then
        echo "$(date +%H:%M:%S) UNACKNOWLEDGED ${age}s for $a — WAKE-NEEDED (human click or Accessibility grant)" >> "$BOX/mutual_watch.log"
      fi
    fi
  done
done
