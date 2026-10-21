#!/usr/bin/env bash
# setup-comms.sh — rebuild the KIMI<->DeepSeek comms link from a clean slate.
#
# Run from the repo root:   ./setup-comms.sh
# Idempotent: safe to re-run at any time; it tears the old link down first.
#
# What it does:
#   1. Removes OLD comms processes (stale pollers, watchers, FIFO listeners,
#      duplicate monitors) — the "old comms link".
#   2. Re-creates FIFOs and mailbox state files if missing (read offsets are
#      preserved; nothing is marked read, no mail is lost).
#   3. Starts the supervised watchers (watch_inbox.sh) for BOTH agents — the
#      standing idle state per COMMS_PROTOCOL Rule 0b/0d.
#   4. Starts FIFO listeners for instant nudge delivery (Rule 0f layer).
#   5. Starts one mutual liveness monitor for both agents.
#   6. Verifies both mailboxes TICK and prints the wake-path status.
#
# What it cannot do (said plainly): an idle AGENT process is not woken by any
# filesystem event. If the wake-path check shows SendKeys blocked, a human must
# either click the agent's window once, or grant Accessibility once — the exact
# steps are printed at the end.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
BOX="$ROOT/.comms"
LOG="$BOX/setup.log"
mkdir -p "$BOX"
touch "$LOG"

step() { printf '\n== %s\n' "$*"; }

# --- 1. remove the old comms link --------------------------------------------
step "1. Remove old comms processes (polls, watchers, listeners, monitors)"
pkill -f "comms.sh poll " 2>/dev/null || true
pkill -f "watch_inbox.sh " 2>/dev/null || true
pkill -f "comms.sh listen " 2>/dev/null || true
pkill -f "inbox_stream.sh " 2>/dev/null || true
pkill -f "mutual_watch.sh " 2>/dev/null || true
sleep 1
# Deliberate STOP files are respected, not cleared — report if present.
for a in deepseek kimi; do
  [ -f "$BOX/STOP.$a" ] && echo "  NOTE: $BOX/STOP.$a exists — watcher for $a will exit until it is removed"
done

# --- 2. mailbox + IPC state ---------------------------------------------------
step "2. Mailbox and FIFO state (offsets preserved, no mail lost)"
tools/comms.sh ensure-ipc
for a in deepseek kimi; do
  touch "$BOX/$a.inbox.jsonl"
  [ -s "$BOX/$a.offset" ] || echo 0 > "$BOX/$a.offset"
  [ -s "$BOX/$a.polled" ] || echo 0 > "$BOX/$a.polled"
done
echo "  mailboxes: $(wc -l < "$BOX/deepseek.inbox.jsonl" | tr -d ' ') deepseek / $(wc -l < "$BOX/kimi.inbox.jsonl" | tr -d ' ') kimi lines"

# --- 3. supervised watchers, both agents --------------------------------------
step "3. Supervised watchers (self-restarting, logged) — 3s cadence"
nohup tools/watch_inbox.sh deepseek 3 >>"$LOG" 2>&1 &
nohup tools/watch_inbox.sh kimi 3 >>"$LOG" 2>&1 &
echo "  started: watch_inbox.sh deepseek 3 | watch_inbox.sh kimi 3"

# --- 4. FIFO listeners ---------------------------------------------------------
step "4. FIFO listeners (instant nudge path)"
nohup tools/comms.sh listen deepseek >>"$BOX/deepseek.fifo.log" 2>&1 &
nohup tools/comms.sh listen kimi >>"$BOX/kimi.fifo.log" 2>&1 &
echo "  started: comms.sh listen deepseek | comms.sh listen kimi"

# --- 5. mutual liveness monitor -------------------------------------------------
step "5. Mutual liveness monitor (60s cycle, both agents, chase on flag age)"
cat > tools/mutual_watch.sh <<'EOF'
#!/usr/bin/env bash
# mutual_watch.sh — one monitor, both agents. Restarts a dead poll loop via
# watch_inbox.sh (which supervises), and chases flagged mail older than 10 min
# with an UNACKNOWLEDGED note in the shared log. It cannot type into an agent
# window; when a chase is due it also appends WAKE-NEEDED so setup-comms.sh's
# report shows the gap plainly.
set -uo pipefail
cd "$(dirname "$0")/.."
BOX="$PWD/.comms"
while true; do
  sleep 60
  for a in deepseek kimi; do
    pgrep -f "comms.sh poll $a" >/dev/null 2>&1 || \
      echo "$(date +%H:%M:%S) $a poll DEAD — supervisor should restart; if not, run ./setup-comms.sh" >> "$BOX/mutual_watch.log"
    if [ -f "$BOX/ATTENTION.$a" ]; then
      age=$(( $(date +%s) - $(stat -f %m "$BOX/ATTENTION.$a" 2>/dev/null || echo 0) ))
      if [ "$age" -gt 600 ]; then
        echo "$(date +%H:%M:%S) UNACKNOWLEDGED ${age}s for $a — WAKE-NEEDED (human click or Accessibility grant)" >> "$BOX/mutual_watch.log"
      fi
    fi
  done
done
EOF
chmod +x tools/mutual_watch.sh
nohup tools/mutual_watch.sh >>"$BOX/mutual_watch.log" 2>&1 &
echo "  started: mutual_watch.sh (log: .comms/mutual_watch.log)"

# --- 6. verify ------------------------------------------------------------------
step "6. Verify both sides TICK"
sleep 4
tools/inbox_tick.sh deepseek 3 2>/dev/null || true
tools/inbox_tick.sh kimi 3 2>/dev/null || true
tools/comms.sh status

# --- 7. wake-path report ---------------------------------------------------------
step "7. Wake-path status (the part a script cannot grant)"
if osascript -e 'tell application "System Events" to keystroke ""' >/dev/null 2>&1; then
  echo "  SendKeys: PERMITTED — automatic UI wake is available to scripts."
else
  cat <<'MSG'
  SendKeys: BLOCKED by macOS Accessibility. One-time human fix, then any
  script can wake an idle agent window:
    System Settings -> Privacy & Security -> Accessibility
      -> enable  Terminal   (if already on, toggle off/on)
  Until then: an idle DeepSeek Harness app needs one click + any keystroke,
  and an idle kimi terminal needs one keystroke. Filesystem delivery itself
  is unaffected — mail, flags and FIFO nudges all work either way.
MSG
fi
step "Done. Logs: .comms/setup.log, .comms/mutual_watch.log, .comms/*.watch.log"
