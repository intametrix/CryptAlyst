#!/usr/bin/env bash
# Bridges the comms channel to KIMI's terminal.
#
# The channel delivers messages. It does NOT wake the agent -- a message sits in
# inbox.jsonl until someone reads it, and KIMI parked on `wait` or sitting at an
# idle prompt will not notice on its own. That gap meant messages were "sent"
# and never seen.
#
# This watches KIMI's inbox and presses Enter in its terminal when something is
# unread, so delivery and notification are the same event.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOX="$ROOT/.comms"
TTY="${KIMI_TTY:-/dev/ttys003}"
INTERVAL="${BRIDGE_INTERVAL:-10}"
STATE="$BOX/.bridge.kimi.last"

mkdir -p "$BOX"
echo "[bridge] watching $BOX/kimi.inbox.jsonl -> $TTY every ${INTERVAL}s"

while true; do
  total=$(wc -l < "$BOX/kimi.inbox.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
  [ -z "$total" ] && total=0
  last=$(cat "$STATE" 2>/dev/null || echo 0)
  [ -z "$last" ] && last=0

  if [ "$total" -gt "$last" ]; then
    # New mail. Summarise the newest message and nudge the terminal.
    summary=$(tail -n 1 "$BOX/kimi.inbox.jsonl" | python3 -c "
import json,sys
try:
    m=json.load(sys.stdin)
    print('%s: %s' % (m.get('subject') or 'message', (m.get('body') or '')[:160]))
except Exception:
    print('new message')
")
    osascript "$ROOT/tools/kimi_send.applescript" \
      "NEW MESSAGE ON THE CHANNEL (${total} total). Run: tools/comms.sh read kimi    Latest subject -> ${summary}    Then do the work, post the acceptance output with COMMS_FROM=kimi tools/comms.sh send deepseek, and immediately park on tools/comms.sh wait kimi 1800 so you are never idle." \
      >/dev/null 2>&1
    echo "$total" > "$STATE"
    echo "[bridge $(date +%H:%M:%S)] woke KIMI for message $total"
  fi
  sleep "$INTERVAL"
done
