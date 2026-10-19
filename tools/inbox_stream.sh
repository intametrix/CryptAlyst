#!/usr/bin/env bash
# Streams every inbox check with a timestamp, so "I am polling" becomes a
# visible sequence of events rather than a claim.
#
# The human asked to SEE the checking happen. A background loop that writes to a
# file nobody reads is exactly the failure mode this session kept hitting, so
# this prints each cycle in the foreground where it can be watched.
#
# Usage: tools/inbox_stream.sh [agent] [seconds] [cycles]
set -uo pipefail
AGENT="${1:-deepseek}"
INTERVAL="${2:-3}"
CYCLES="${3:-6}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOX="$ROOT/.comms"

prev=$(wc -l < "$BOX/$AGENT.inbox.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
for i in $(seq 1 "$CYCLES"); do
  ts=$(date '+%H:%M:%S')
  # Liveness read from the process table, never assumed.
  if pgrep -f "comms.sh poll $AGENT" >/dev/null 2>&1; then state="TICKING"; else state="DEAD"; fi
  total=$(wc -l < "$BOX/$AGENT.inbox.jsonl" 2>/dev/null | tr -d ' ' || echo 0)
  off=$(cat "$BOX/$AGENT.offset" 2>/dev/null || echo 0); [ -z "$off" ] && off=0
  unread=$((total-off))
  new=""
  if [ "$total" -gt "$prev" ]; then new="  *** NEW MAIL ($((total-prev))) ***"; fi
  printf '%s  checking[%d/%d]  %-7s  unread=%s  total=%s%s\n' \
    "$ts" "$i" "$CYCLES" "$state" "$unread" "$total" "$new"
  prev=$total
  [ "$i" -lt "$CYCLES" ] && sleep "$INTERVAL"
done
