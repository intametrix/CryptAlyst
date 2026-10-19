#!/usr/bin/env bash
# Permanent mailbox watcher for one agent, with a supervisor.
#
# Two failures this fixes, both observed:
#   1. The poll loop can be killed (by me, restarting it) and then NOTHING is
#      watching. From the inside a dead watcher and a quiet channel look
#      identical, so the agent will confidently report it is watching while it
#      is not. A supervisor makes dead-by-accident impossible; only an explicit
#      stop command ends it.
#   2. The poll printed to a background job's output, which nobody reads, so
#      there was no visible evidence the agent was watching at all. Everything
#      now also lands in a log file that can be tailed or grepped at any moment.
#
# Usage: tools/watch_inbox.sh <agent> [seconds]
set -uo pipefail

AGENT="${1:?usage: watch_inbox.sh <agent> [seconds]}"
INTERVAL="${2:-3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOX="$ROOT/.comms"
LOG="$BOX/$AGENT.watch.log"
STOP="$BOX/STOP.$AGENT"
mkdir -p "$BOX"

touch "$LOG"
echo "[watch] supervising '$AGENT' mailbox every ${INTERVAL}s" | tee -a "$LOG"
echo "[watch] log: $LOG" | tee -a "$LOG"
echo "[watch] to stop: touch $STOP" | tee -a "$LOG"

while true; do
  # An explicit stop file is the ONLY thing that ends this. The human said keep
  # watching unless told to stop, so the stop has to be deliberate.
  if [ -f "$STOP" ]; then
    echo "[watch] $(date +%H:%M:%S) STOP file present - watcher exiting" | tee -a "$LOG"
    exit 0
  fi

  if pgrep -f "comms.sh poll $AGENT" >/dev/null 2>&1; then
    sleep "$INTERVAL"
    continue
  fi

  echo "[watch] $(date +%H:%M:%S) poll not running - (re)starting" | tee -a "$LOG"
  "$ROOT/tools/comms.sh" poll "$AGENT" "$INTERVAL" >> "$LOG" 2>&1
  echo "[watch] $(date +%H:%M:%S) poll exited - supervisor restarting it" | tee -a "$LOG"
  sleep 1
done
