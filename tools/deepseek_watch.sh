#!/bin/bash
# deepseek_watch.sh — the symmetric watcher, run by DeepSeek, watching KIMI.
#
# Mirrors /tmp/kimi_watch/kimi_watch.sh. Two jobs, 60s cycle:
#   1. WATCHDOG: pgrep 'comms.sh poll kimi'; empty -> urgent "YOUR POLL IS
#      DEAD" to kimi. Missing .comms/kimi.polled -> warn that the pre-fix,
#      mail-consuming poll may be running.
#   2. ASKS: /tmp/deepseek_watch/asks.tsv lines "<keyword>|<ask_ts_ms>|<body-file>"
#      track anything EITHER agent sent that needs KIMI action (seed from
#      deepseek->kimi log lines containing a question; kimi's own asks of
#      deepseek are NOT this watcher's job). Acknowledged when a kimi->deepseek
#      log entry with newer ts matches the keyword. >10min unacknowledged ->
#      resend ANSWER NOW prefixed "UNACKNOWLEDGED (<age>)". >20min -> one-line
#      digest, then every 10min.
REPO=/Users/andrewpreston/Documents/deepseek-harness/default-workspace/binance-pumpdump
STATE=/tmp/deepseek_watch
mkdir -p "$STATE"
touch "$STATE/asks.tsv"
cd "$REPO" || exit 1

ack_check() { # keyword ask_ts -> "yes" if a newer kimi->deepseek entry mentions it
  python3 - "$1" "$2" <<'PY'
import json,sys,subprocess
kw,ts=sys.argv[1],int(sys.argv[2])
out=subprocess.run(["tools/comms.sh","log","200"],capture_output=True,text=True).stdout
for line in out.splitlines():
    line=line.strip()
    if not line.startswith("{"): continue
    try: m=json.loads(line)
    except Exception: continue
    if m.get("from")=="kimi" and m.get("to")=="deepseek" and m.get("ts_ms",0)>ts:
        if kw.lower() in (m.get("subject","")+m.get("body","")).lower():
            print("yes"); break
PY
}

while true; do
  sleep 60
  now=$(python3 -c 'import time;print(int(time.time()*1000))')

  if ! pgrep -f 'comms.sh poll kimi' >/dev/null 2>&1; then
    COMMS_FROM=deepseek tools/comms.sh urgent kimi \
      "YOUR POLL IS DEAD: pgrep 'comms.sh poll kimi' returned nothing. Restart it." 2>/dev/null
  fi
  if [ ! -f .comms/kimi.polled ]; then
    COMMS_FROM=deepseek tools/comms.sh send kimi \
      "WATCH: .comms/kimi.polled is missing — your poll may be the pre-fix version that consumes mail. Check." 2>/dev/null
  fi

  tmp=$(mktemp)
  while IFS='|' read -r kw ts bodyfile; do
    [ -z "$kw" ] && continue
    if [ "$(ack_check "$kw" "$ts")" = "yes" ]; then
      continue
    fi
    age=$(( (now - ts) / 60000 ))
    lastchase=$(cat "$STATE/chase.$kw" 2>/dev/null || echo 0)
    if [ "$age" -ge 20 ]; then
      COMMS_FROM=deepseek tools/comms.sh send kimi \
        "OUTSTANDING FOR KIMI: $kw (${age}m)" 2>/dev/null
      echo "$now" > "$STATE/chase.$kw"
    elif [ "$age" -ge 10 ] && [ $(( (now - lastchase) / 60000 )) -ge 10 ]; then
      body="UNACKNOWLEDGED (${age}m). Resending: "
      [ -f "$bodyfile" ] && body="$body$(cat "$bodyfile")" || body="$body$kw"
      COMMS_FROM=deepseek tools/comms.sh send kimi "$body" "UNACKNOWLEDGED: $kw" --answer-now 2>/dev/null
      echo "$now" > "$STATE/chase.$kw"
    fi
    echo "$kw|$ts|$bodyfile" >> "$tmp"
  done < "$STATE/asks.tsv"
  mv "$tmp" "$STATE/asks.tsv"
done
