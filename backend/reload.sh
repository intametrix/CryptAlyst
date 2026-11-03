#!/usr/bin/env bash
# reload — hand the dashboard over to a new build without downtime.
#
# The server binds with SO_REUSEPORT, so the new process can take connections
# while the old one is still serving. This script builds, starts the new one,
# waits until it answers /health, and only then stops the old process. If the new
# build never becomes healthy it is killed and the old one keeps serving — a bad
# build cannot take the platform down.
set -uo pipefail
cd "$(dirname "$0")"

ADDR="${ADDR:-127.0.0.1:8787}"
WEB_DIR="${WEB_DIR:-}"
LOG="${LOG:-/tmp/server.log}"
ARGS_FILE=".server-args"

if [ ! -f "$ARGS_FILE" ]; then
  echo "reload: no $ARGS_FILE — write the server flags into it first (one line)." >&2
  exit 2
fi

echo "reload: building"
if ! go build -o bin/server.new ./cmd/server; then
  echo "reload: build failed — old server untouched" >&2
  exit 1
fi

OLD_PID="$(pgrep -f "bin/server " | head -1)"

EXTRA=""
[ -n "$WEB_DIR" ] && EXTRA="--web-dir $WEB_DIR"

echo "reload: starting new instance"
nohup ./bin/server.new $(cat "$ARGS_FILE") $EXTRA >>"$LOG" 2>&1 &
NEW_PID=$!

for i in $(seq 1 120); do
  # Both instances answer on the shared port during the handover, so the check is
  # that the NEW pid is the one replying — not merely that something replied.
  BODY="$(curl -fsS --max-time 1 "http://${ADDR}/health" 2>/dev/null || true)"
  if echo "$BODY" | grep -q "\"pid\":$NEW_PID"; then
    echo "reload: new instance serving after ${i}s (pid $NEW_PID)"
    if [ -n "$OLD_PID" ] && [ "$OLD_PID" != "$NEW_PID" ]; then
      echo "reload: stopping old pid $OLD_PID"
      kill "$OLD_PID" 2>/dev/null || true
    fi
    mv bin/server.new bin/server
    exit 0
  fi
  if ! kill -0 "$NEW_PID" 2>/dev/null; then
    echo "reload: new instance died — old server still serving" >&2
    tail -5 "$LOG" >&2
    exit 1
  fi
  sleep 1
done

echo "reload: new instance never became healthy — killing it, old server still serving" >&2
kill "$NEW_PID" 2>/dev/null || true
exit 1
