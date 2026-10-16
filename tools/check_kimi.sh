#!/usr/bin/env bash
# Says "I am checking KIMI INBOX" out loud, every 3 seconds, and reports what it
# found. The human asked to SEE the check rather than be told it happens, so this
# prints the announcement and the result together -- an announcement without a
# result is the claim this whole channel was built to stop making.
set -uo pipefail
INTERVAL="${1:-3}"
CYCLES="${2:-8}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOX="$ROOT/.comms"

for i in $(seq 1 "$CYCLES"); do
  ts=$(date '+%H:%M:%S')
  total=$(wc -l < "$BOX/deepseek.inbox.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
  off=$(cat "$BOX/deepseek.offset" 2>/dev/null || echo 0); [ -z "$off" ] && off=0
  unread=$((total - off))
  if pgrep -f 'comms.sh poll deepseek' >/dev/null 2>&1; then live="timer TICKING"; else live="*** TIMER DEAD ***"; fi
  if [ "$unread" -gt 0 ]; then
    mark="  <-- $unread UNREAD FROM KIMI"
  else
    mark=""
  fi
  printf '%s  I am checking KIMI INBOX  [%d/%d]  %s  total=%s unread=%s%s\n' \
    "$ts" "$i" "$CYCLES" "$live" "$total" "$unread" "$mark"
  if [ "$i" -lt "$CYCLES" ]; then sleep "$INTERVAL"; fi
done
exit 0
