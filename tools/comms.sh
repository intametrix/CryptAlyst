#!/usr/bin/env bash
# Inter-agent comms channel between the DeepSeek agent and KIMI.
#
# A file-backed mailbox, because the two agents do not share a process, a
# socket, or a language runtime -- but they do share a filesystem.
#
#   tools/comms.sh send <to> "<message>" [subject]
#   tools/comms.sh read <who>          # print UNREAD and mark them read
#   tools/comms.sh peek <who>          # print unread without marking
#   tools/comms.sh wait <who> [secs]   # block until a message arrives, then print it
#   tools/comms.sh status              # unread counts for everyone
#   tools/comms.sh log [n]             # last n messages, both directions
#
# <to>/<who> is an agent name: deepseek | kimi
#
# `wait` is the important one: it lets an idle agent block on its inbox instead
# of sitting at a prompt doing nothing. The DeepSeek side runs it as a
# background job; the KIMI side runs it with a shell escape and gets woken the
# moment work appears. That is what makes this a channel rather than a log.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOX="$ROOT/.comms"
mkdir -p "$BOX"

now_ms() { python3 -c 'import time;print(int(time.time()*1000))'; }
box()  { printf '%s/%s.inbox.jsonl' "$BOX" "$1"; }
offset_file() { printf '%s/%s.offset' "$BOX" "$1"; }

cmd_send() {
  local prio="${COMMS_PRIO:-normal}"
  local args=()
  for a in "$@"; do
    case "$a" in
      --urgent)     prio="urgent" ;;
      --answer-now) prio="ANSWER NOW" ;;
      *) args+=("$a") ;;
    esac
  done
  set -- "${args[@]}"
  local to="${1:?usage: send <to> <message> [subject] [--urgent|--answer-now]}"; shift
  local body="${1:?usage: send <to> <message> [subject]}"; shift || true
  local subject="${1:-}"
  local from="${COMMS_FROM:-deepseek}"
  local line
  line="$(python3 - "$from" "$to" "$subject" "$body" "$prio" <<'PY'
import json,sys,time
frm,to,subject,body,prio = sys.argv[1:6]
print(json.dumps({"ts_ms":int(time.time()*1000),"from":frm,"to":to,
                  "subject":subject,"priority":prio,"body":body}, ensure_ascii=False))
PY
)"
  printf '%s\n' "$line" >> "$(box "$to")"
  printf '%s\n' "$line" >> "$BOX/all.jsonl"
  # An imperative is ALSO written to a single flag file. A JSONL line is easy to
  # skim past when scanning directory state; a flag file is not.
  case "$prio" in
    "ANSWER NOW"|urgent)
      printf '%s\n' "$line" > "$BOX/ATTENTION.$to"
      echo "sent -> $to [$prio] - FLAGGED in .comms/ATTENTION.$to" ;;
    *) echo "sent -> $to" ;;
  esac
}

# Prints unread messages as "<lineno>\t<json>".
unread() {
  local who="$1" total last
  total=$(wc -l < "$(box "$who")" 2>/dev/null | tr -d ' ' || echo 0)
  last=$(cat "$(offset_file "$who")" 2>/dev/null || echo 0)
  [ -z "$last" ] && last=0
  if [ "$total" -gt "$last" ]; then
    awk -v start="$last" 'NR>start' "$(box "$who")"
  fi
}

cmd_read() {
  local who="${1:?usage: read <who>}" mark="${2:-mark}"
  local total
  total=$(wc -l < "$(box "$who")" 2>/dev/null | tr -d ' ' || echo 0)
  local found
  found="$(unread "$who")"
  if [ -z "$found" ]; then echo "(no new messages)"; return 0; fi
  printf '%s\n' "$found" | while IFS= read -r line; do
    python3 - "$line" <<'PY'
import json,sys,datetime
m=json.loads(sys.argv[1])
ts=datetime.datetime.fromtimestamp(m["ts_ms"]/1000).strftime("%H:%M:%S")
print(f'[{ts}] {m["from"]} -> {m["to"]}' + (f'  ({m["subject"]})' if m.get("subject") else ''))
print('   ' + m["body"])
PY
  done
  if [ "$mark" = "mark" ]; then
    echo "$total" > "$(offset_file "$who")"
    rm -f "$BOX/ATTENTION.$who"
  fi
}

cmd_wait() {
  local who="${1:?usage: wait <who> [secs]}"; local secs="${2:-900}"
  local waited=0
  while [ "$waited" -lt "$secs" ]; do
    local found
    found="$(unread "$who")"
    if [ -n "$found" ]; then cmd_read "$who" mark; return 0; fi
    sleep 2; waited=$((waited+2))
  done
  echo "(no message within ${secs}s)"; return 1
}

cmd_status() {
  for who in deepseek kimi; do
    [ -f "$BOX/ATTENTION.$who" ] && echo "  *** $who HAS AN UNANSWERED IMPERATIVE (see .comms/ATTENTION.$who) ***"
  done
  for who in deepseek kimi; do
    [ -f "$(box "$who")" ] || { echo "  $who: 0 total, 0 unread"; continue; }
    local total last
    total=$(wc -l < "$(box "$who")" | tr -d ' ')
    last=$(cat "$(offset_file "$who")" 2>/dev/null || echo 0); [ -z "$last" ] && last=0
    echo "  $who: $total total, $((total-last)) unread"
  done
}

cmd_log() { tail -n "${1:-10}" "$BOX/all.jsonl" 2>/dev/null || echo "(empty)"; }

# cmd_human delivers a HUMAN instruction to every agent at once.
#
# It always carries the "HUMAN instructs" subject and ANSWER NOW priority: an
# instruction from the person is never a suggestion and never waits for an
# agent to finish what it was doing.
cmd_human() {
  local body="${1:?usage: human \"<instruction>\"}"
  local line
  line="$(python3 - "$body" <<'PYEOF_INNER'
import json,sys,time
body=sys.argv[1]
print(json.dumps({"ts_ms":int(time.time()*1000),"from":"HUMAN","to":"all",
                  "subject":"HUMAN instructs","priority":"ANSWER NOW",
                  "body":body}, ensure_ascii=False))
PYEOF_INNER
)"
  printf '%s\n' "$line" >> "$(box deepseek)"
  printf '%s\n' "$line" >> "$(box kimi)"
  printf '%s\n' "$line" >> "$BOX/all.jsonl"
  printf '%s\n' "$line" > "$BOX/ATTENTION.deepseek"
  printf '%s\n' "$line" > "$BOX/ATTENTION.kimi"
  echo "HUMAN instructs -> deepseek + kimi [ANSWER NOW]"
}

case "${1:-status}" in
  send)   shift; cmd_send "$@" ;;
  read)   shift; cmd_read "${1:-deepseek}" mark ;;
  peek)   shift; cmd_read "${1:-deepseek}" nomark ;;
  wait)   shift; cmd_wait "$@" ;;
  answer-now) shift; COMMS_PRIO="ANSWER NOW" cmd_send "$@" ;;
  urgent) shift; COMMS_PRIO="urgent" cmd_send "$@" ;;
  status) cmd_status ;;
  log)    shift; cmd_log "$@" ;;
  human)  shift; cmd_human "$@" ;;
  *) sed -n '2,25p' "$0"; exit 2 ;;
esac
