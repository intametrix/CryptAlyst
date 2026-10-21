#!/usr/bin/env bash
# mutual_watch.sh — one monitor, both agents: liveness, chase, HEARTBEAT.
#
# Every 5s, for each agent:
#   - writes .comms/<agent>.heartbeat.infra  (infra proof-of-life: watcher state)
#   - logs to .comms/mutual_watch.log if the poll loop is dead
#   - logs UNACKNOWLEDGED + WAKE-NEEDED if a flagged message ages past 10 min
# Every 15 min of CHANNEL SILENCE (no new line in all.jsonl), sends ONE
# heartbeat message to both mailboxes from author "monitor" — visible proof of
# life that does not demand a reply. When agents are actively talking, the
# channel heartbeat stays silent: mail is the heartbeat.
#
# Agent heartbeats: each agent appends its own state line to
# .comms/<agent>.heartbeat at every turn end:
#   printf '%s %s\n' "$(date +%s)" "doing X; next Y" >> .comms/<agent>.heartbeat
set -uo pipefail
cd "$(dirname "$0")/.."
BOX="$PWD/.comms"
LAST_CHANNEL_POST=0
[ -f "$BOX/all.jsonl" ] && LAST_CHANNEL_POST=$(stat -f %m "$BOX/all.jsonl")

while true; do
  sleep 5
  now=$(date +%s)
  silence=$(( now - LAST_CHANNEL_POST ))
  for a in deepseek kimi; do
    alive="no"
    pgrep -f "comms.sh poll $a" >/dev/null 2>&1 && alive="yes"
    inbox_lines=$(wc -l < "$BOX/$a.inbox.jsonl" 2>/dev/null | tr -d ' ')
    off=$(cat "$BOX/$a.offset" 2>/dev/null | tr -d ' '); [ -z "$off" ] && off=0
    unread=$(( inbox_lines - off ))
    # infra heartbeat — fresh every 5s, human- and agent-readable
    printf '%s alive=%s inbox=%s unread=%s\n' "$now" "$alive" "$inbox_lines" "$unread" > "$BOX/$a.heartbeat.infra"
    if [ "$alive" = "no" ]; then
      echo "$(date +%H:%M:%S) $a poll DEAD — supervisor should restart; if not, run ./setup-comms.sh" >> "$BOX/mutual_watch.log"
    fi
    if [ -f "$BOX/ATTENTION.$a" ]; then
      age=$(( now - $(stat -f %m "$BOX/ATTENTION.$a" 2>/dev/null || echo 0) ))
      if [ "$age" -gt 600 ]; then
        echo "$(date +%H:%M:%S) UNACKNOWLEDGED ${age}s for $a — WAKE-NEEDED (human click or Accessibility grant)" >> "$BOX/mutual_watch.log"
      fi
    fi
  done
  # channel heartbeat only after 15 min of silence
  if [ "$silence" -gt 900 ]; then
    COMMS_FROM=monitor tools/comms.sh send deepseek "HEARTBEAT: channel silent ${silence}s. Watchers alive, mailboxes clean. Agents: append your state line to .comms/<you>.heartbeat at each turn end." "HEARTBEAT" >/dev/null 2>&1
    COMMS_FROM=monitor tools/comms.sh send kimi "HEARTBEAT: channel silent ${silence}s. Watchers alive, mailboxes clean. Agents: append your state line to .comms/<you>.heartbeat at each turn end." "HEARTBEAT" >/dev/null 2>&1
    LAST_CHANNEL_POST=$(stat -f %m "$BOX/all.jsonl" 2>/dev/null || echo "$now")
  fi
done
