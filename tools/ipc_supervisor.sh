#!/usr/bin/env bash
# Keeps an agent's IPC listener alive.
#
# A bare `comms.sh listen` is a long-blocking process with no restart path: if
# it dies, the FIFO silently falls back to nobody listening, writes still
# succeed at the file level, and the agent looks reachable while receiving
# nothing instantly. That is the dead-watcher-looks-idle failure again, so the
# listener gets the same supervision as the poll.
#
# Usage: tools/ipc_supervisor.sh <agent>
set -uo pipefail
AGENT="${1:?usage: ipc_supervisor.sh <agent>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOX="$ROOT/.comms"
LOG="$BOX/$AGENT.ipc.log"
STOP="$BOX/STOP.ipc.$AGENT"
mkdir -p "$BOX"
"$ROOT/tools/ipc.py" ensure "$BOX/$AGENT.fifo"

echo "[ipc-sup] supervising listener for $AGENT" | tee -a "$LOG"
while true; do
  if [ -f "$STOP" ]; then
    echo "[ipc-sup] $(date +%H:%M:%S) STOP file present - exiting" | tee -a "$LOG"
    exit 0
  fi
  if pgrep -f "ipc.py listen .*$AGENT.fifo" >/dev/null 2>&1; then
    sleep 3
    continue
  fi
  echo "[ipc-sup] $(date +%H:%M:%S) listener not running - starting" | tee -a "$LOG"
  "$ROOT/tools/ipc.py" listen "$BOX/$AGENT.fifo" >> "$LOG" 2>&1
  echo "[ipc-sup] $(date +%H:%M:%S) listener exited - restarting" | tee -a "$LOG"
  sleep 1
done
