#!/usr/bin/env bash
# Supervisor for the CryptALAS GPU training agent.
#
# Keeps the agent alive: if its /health endpoint stops answering, the agent is
# restarted and the event is logged. Only an explicit STOP file ends it — the
# same discipline as watch_inbox.sh, so a dead supervisor and a quiet agent are
# never indistinguishable.
#
# Usage: tools/watch_cryptalas.sh [addr] [device] [ollama]
#   addr    listen address            (default 127.0.0.1:8810)
#   device  auto|cpu|gpu|cuda|metal   (default auto — falls back to CPU)
#   ollama  Ollama base URL           (default http://localhost:11434)
set -uo pipefail

ADDR="${1:-127.0.0.1:8810}"
DEVICE="${2:-auto}"
OLLAMA="${3:-http://localhost:11434}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/backend/bin/cryptalas"
SRC="$ROOT/backend/cmd/cryptalas/main.go"
LOG="$ROOT/backend/cryptalas.watch.log"
STOP="$ROOT/backend/STOP.cryptalas"
mkdir -p "$ROOT/backend/bin"

touch "$LOG"
echo "[watch] supervising CryptALAS at http://$ADDR (device=$DEVICE)" | tee -a "$LOG"
echo "[watch] log: $LOG" | tee -a "$LOG"
echo "[watch] to stop: touch $STOP" | tee -a "$LOG"

# Build once if the binary is missing or the source is newer.
if [ ! -x "$BIN" ] || [ "$SRC" -nt "$BIN" ]; then
  echo "[watch] building cryptalas…" | tee -a "$LOG"
  (cd "$ROOT/backend" && go build -o "$BIN" ./cmd/cryptalas/) >> "$LOG" 2>&1 || {
    echo "[watch] build failed — see $LOG" | tee -a "$LOG"
    exit 1
  }
fi

while true; do
  # An explicit stop file is the ONLY thing that ends this.
  if [ -f "$STOP" ]; then
    echo "[watch] $(date +%H:%M:%S) STOP file present — supervisor exiting" | tee -a "$LOG"
    exit 0
  fi

  # Health check: the agent answers /health when it is up.
  if curl -sS --max-time 3 "http://$ADDR/health" >/dev/null 2>&1; then
    sleep 10
    continue
  fi

  echo "[watch] $(date +%H:%M:%S) health check failed — (re)starting cryptalas" | tee -a "$LOG"
  "$BIN" -addr "$ADDR" -device "$DEVICE" -ollama "$OLLAMA" \
    -state "$ROOT/backend/cryptalas_state" >> "$LOG" 2>&1 &
  echo "[watch] $(date +%H:%M:%S) cryptalas started (pid $!)" | tee -a "$LOG"
  sleep 10
done
