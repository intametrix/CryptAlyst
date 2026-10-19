#!/usr/bin/env bash
# Inter-agent comms channel between the DeepSeek agent and KIMI.
#
# A file-backed mailbox, because the two agents do not share a process, a
# socket, or a language runtime -- but they do share a filesystem.
#
#   tools/comms.sh send <to> "<message>" [subject]
#   tools/comms.sh read <who>          # print UNREAD and mark them read
#   tools/comms.sh peek <who>          # print unread without marking
#   tools/comms.sh wait <who> [secs]   # block until ONE message arrives, then exit
#   tools/comms.sh poll <who> [secs]   # poll FOREVER - this is the standing idle state
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
# polled_file tracks what the poll loop has ANNOUNCED, separately from what has
# been READ. Keeping them apart is what stops polling from consuming mail.
polled_file() { printf '%s/%s.polled' "$BOX" "$1"; }

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
  # Durable record first, so a crash between the two writes loses nothing:
  # the file is the source of truth and the FIFO is only the nudge.
  printf '%s\n' "$line" >> "$(box "$to")"
  printf '%s\n' "$line" >> "$BOX/all.jsonl"

  # Instant delivery over IPC, best effort. Exit code 2 means nobody is
  # listening, which is normal and harmless -- the mailbox still holds it.
  python3 "$(dirname "${BASH_SOURCE[0]}")/ipc.py" send "$BOX/$to.fifo" "$line" 2>/dev/null || true
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

# cmd_poll is the standing idle state for both agents.
#
# `wait` blocks until ONE message arrives and then exits, which means an agent
# that has been woken is idle again the moment it replies. Polling is the fix:
# this loop runs forever, printing new mail as it lands, so an agent sitting
# here is never unreachable and never has to be noticed by anyone else.
#
# A heartbeat line is printed periodically so a human glancing at the terminal
# can tell the difference between "polling, nothing to do" and "crashed".
cmd_poll() {
  local who="${1:?usage: poll <who> [interval_secs]}"
  local interval="${2:-5}"
  local beats=0
  echo "[poll] $who mailbox: watching every ${interval}s. Ctrl-C to stop."
  while true; do
    # Announce only what this loop has not already announced, tracked in its
    # OWN offset file. The READ offset is deliberately untouched: polling must
    # never consume a message, because a message consumed into a background
    # job's output is a message nobody reads.
    local total polled fresh
    total=$(wc -l < "$(box "$who")" 2>/dev/null | tr -d ' ' || echo 0)
    [ -z "$total" ] && total=0
    polled=$(cat "$(polled_file "$who")" 2>/dev/null || echo 0)
    [ -z "$polled" ] && polled=0
    if [ "$total" -gt "$polled" ]; then
      echo "[poll] $(date +%H:%M:%S) *** MAIL ARRIVED ***"
      awk -v start="$polled" 'NR>start' "$(box "$who")" | while IFS= read -r line; do
        python3 - "$line" <<'PYIN'
import json,sys,datetime
try:
    m=json.loads(sys.argv[1])
except Exception:
    raise SystemExit
ts=datetime.datetime.fromtimestamp(m["ts_ms"]/1000).strftime("%H:%M:%S")
print(f'   [{ts}] {m["from"]} -> {m["to"]}' + (f'  ({m["subject"]})' if m.get("subject") else ''))
print('      ' + (m.get("body") or "")[:400])
PYIN
      done
      echo "$total" > "$(polled_file "$who")"
      echo "[poll] STILL UNREAD until you run: tools/comms.sh read $who"
      beats=0
    else
      beats=$((beats+1))
      # A heartbeat every ~60s. Silence should be visibly deliberate.
      # Log EVERY check. A heartbeat proves liveness eventually; a per-check
      # line proves it for the exact second in question, which is what "show me
      # you are polling" actually asks for. The log is bounded by rotating it
      # when it grows, so verbosity cannot become an unbounded file.
      # Count for real. An earlier version of this line hardcoded "unread=0",
      # which meant the log confidently reported an empty mailbox while the
      # inbox held two messages -- a monitoring line that lies is worse than no
      # monitoring line, because it is trusted.
      _total=$(wc -l < "$(box "$who")" 2>/dev/null | tr -d ' ' || echo 0)
      _off=$(cat "$(offset_file "$who")" 2>/dev/null || echo 0); [ -z "$_off" ] && _off=0
      echo "[poll] $(date +%H:%M:%S) checking $who - unread=$((_total-_off)) total=${_total}"
      if [ -f "$(box "$who")" ] && [ "$(wc -l < "$(box "$who")" | tr -d ' ')" -gt 4000 ]; then
        tail -n 1000 "$(box "$who")" > "$(box "$who").tmp" && mv "$(box "$who").tmp" "$(box "$who")"
        echo 1000 > "$(offset_file "$who")"
      fi
    fi
    sleep "$interval"
  done
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
  # [!!FORCE] -- the human's keyword. A forced message must be ACTED ON,
  # not queued behind current work. Same transport as urgent deliberately:
  # what makes it forceful is the keyword and the ATTENTION flag, so it
  # cannot be lost by a rarer code path.
  force)
    shift
    to="$1"; body="$2"; shift 2
    case "$body" in "[!!FORCE]"*) ;; *) body="[!!FORCE] $body" ;; esac
    COMMS_PRIO="urgent" cmd_send "$to" "$body" "$@" ;;
  forced) shift; COMMS_PRIO="urgent" cmd_send "$@" ;;
  poll)   shift; cmd_poll "$@" ;;
  listen) shift; python3 "$(dirname "${BASH_SOURCE[0]}")/ipc.py" listen "$BOX/${1:?usage: listen <agent>}.fifo" ;;
  ensure-ipc) for w in deepseek kimi; do python3 "$(dirname "${BASH_SOURCE[0]}")/ipc.py" ensure "$BOX/$w.fifo"; done; echo "FIFOs ready" ;;
  status) cmd_status ;;
  log)    shift; cmd_log "$@" ;;
  human)  shift; cmd_human "$@" ;;
  *) sed -n '2,25p' "$0"; exit 2 ;;
esac

