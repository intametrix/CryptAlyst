#!/bin/bash
# supervise-cryptalas.sh — run the CryptALAS agent forever.
#
#   ./supervise-cryptalas.sh [cryptalas flags...]
#
# 1. If a self-update was staged (<exe>.new), swap it in before launching.
# 2. Run the agent. Whatever the exit code, restart it after a short backoff.
#
# The agent is therefore self-updating (it downloads cryptalas.new and the
# supervisor swaps it) and never dies (a crash is restarted, not final).
set -u
cd "$(dirname "$0")" || exit 1

EXE=./bin/cryptalas
BACKOFF=2

echo "supervisor: watching $EXE (pid $$)"
while true; do
  if [ -f "$EXE.new" ]; then
    echo "supervisor: swapping in $EXE.new"
    mv "$EXE.new" "$EXE"
    chmod +x "$EXE"
  fi
  "$EXE" "$@"
  code=$?
  echo "supervisor: cryptalas exited ($code) — restarting in ${BACKOFF}s"
  sleep "$BACKOFF"
done
