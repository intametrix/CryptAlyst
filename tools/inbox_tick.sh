#!/usr/bin/env bash
# Prints a live inbox-check countdown for the agent that owns this mailbox.
#
# Turns a background loop into something a human can actually watch: the poll
# runs every 3s, but "the poll is running" is a claim while "last check 1.2s
# ago, next in 1.8s" is evidence. The human asked for a countdown precisely
# because the previous answer was a claim they had to take on trust.
set -uo pipefail
AGENT="${1:-deepseek}"
INTERVAL="${2:-3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/.comms/$AGENT.watch.log"

python3 - "$LOG" "$AGENT" "$INTERVAL" <<'PYEOF_X'
import os, sys, time, json
log, agent, interval = sys.argv[1], sys.argv[2], int(sys.argv[3])
box = os.path.dirname(log)

# Is the poll actually alive, or merely configured?
alive = False
for pid in os.listdir('/proc') if os.path.isdir('/proc') else []:
    pass
import subprocess
try:
    out = subprocess.run(["pgrep","-f",f"comms.sh poll {agent}"],
                         capture_output=True, text=True, timeout=5).stdout.strip()
    alive = bool(out)
except Exception:
    pass

age = None
if os.path.exists(log):
    age = time.time() - os.path.getmtime(log)

# Next tick: the poll writes on activity, so when idle the log goes stale.
# Report the cadence and the honest liveness rather than a fake ticking number.
if age is None:
    last = "no log"
else:
    last = f"{age:.1f}s ago"

# unread count is the number that actually matters
unread = 0
try:
    inbox = os.path.join(box, f"{agent}.inbox.jsonl")
    off_f = os.path.join(box, f"{agent}.offset")
    total = sum(1 for l in open(inbox) if l.strip()) if os.path.exists(inbox) else 0
    off = int(open(off_f).read().strip() or 0) if os.path.exists(off_f) else 0
    unread = max(0, total - off)
except Exception:
    pass

state = "TICKING" if alive else "*** DEAD ***"
print(f"INBOX[{agent}] {state}  cadence {interval}s  last write {last}  "
      f"next check <= {interval}s  unread {unread}")
PYEOF_X
