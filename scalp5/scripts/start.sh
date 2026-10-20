#!/bin/bash
# Start CryptAlyst in dependency order, then the Envelope Explorer.
#
#   bus (RabbitMQ) -> market data -> recorder -> intel producers -> books
#   -> dashboard -> explorer
#
# Every step waits for the previous one to answer, so a failure names the stage
# that failed instead of leaving a half-running platform.
set -u
cd "$(dirname "$0")/.." || exit 1

# Trading is OFF unless it is asked for. Starting the platform brings up market
# data, the recorder, the explorer and the dashboard; it never puts money at risk
# on its own. Pass --trade to start the books as well.
TRADE=0
for arg in "$@"; do [ "$arg" = "--trade" ] && TRADE=1; done
ROOT=$(pwd)
CORE="$ROOT/core"
LOGS="$ROOT/logs"
mkdir -p "$LOGS" "$ROOT/state/lake3"

say()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }
wait_for() { # wait_for <url> <seconds> <name>
  local url=$1 secs=$2 name=$3 i=0
  while [ $i -lt "$secs" ]; do
    if curl -sS --max-time 2 "$url" >/dev/null 2>&1; then ok "$name"; return 0; fi
    sleep 1; i=$((i+1))
  done
  bad "$name did not answer within ${secs}s ($url)"; return 1
}
started() { # started <name> <command...>
  local name=$1; shift
  if pgrep -f "$2" >/dev/null 2>&1; then ok "$name already running"; return 0; fi
  ( "$@" >"$LOGS/$name.log" 2>&1 & )
  sleep 1
}

say "1 · broker"
if (printf 'CONNECT\naccept-version:1.2\nhost:/\nlogin:cryptalyst\npasscode:cryptalyst\n\n\x00' > /dev/tcp/127.0.0.1/61613) 2>/dev/null; then
  ok "RabbitMQ STOMP reachable on :61613"
else
  bad "RabbitMQ is not running — start it before the platform"; exit 1
fi

# the stop flag is written before anything else, so a book that somehow starts
# finds trading already blocked
mkdir -p "$ROOT/state"
printf '{"stopped_at": %s, "actor": "start.sh", "reason": "platform start — trading off by default"}' \
  "$(date +%s)000" > "$ROOT/state/STOP"
ok "trading is OFF (state/STOP written) — pass --trade to start the books"

say "2 · market data (the only process that talks to the exchange)"
(cd "$CORE" && nohup ./bin/marketdata --port 8796 --top 400 >"$LOGS/marketdata.log" 2>&1 &)
wait_for "http://127.0.0.1:8796/health" 20 "market data on :8796" || exit 1

say "3 · recorder (the only consumer of the bus)"
(cd "$CORE" && nohup ./bin/recorder --port 8801 --data ../state/lake3 >"$LOGS/recorder.log" 2>&1 &)
wait_for "http://127.0.0.1:8801/stats" 15 "recorder on :8801" || exit 1

say "4 · bus services"
(cd "$CORE" && nohup ./bin/busconsumer --port 8798 >"$LOGS/busconsumer.log" 2>&1 &)
(cd "$CORE" && nohup ./bin/healthpub --every 5s >"$LOGS/healthpub.log" 2>&1 &)
(cd "$CORE" && nohup ./bin/signalbus --every 2s >"$LOGS/signalbus.log" 2>&1 &)
sleep 2; ok "bus consumer, health publisher, alert intake"

say "5 · intelligence producers"
pkill -f "signals.py --every" >/dev/null 2>&1
nohup python3 -u signals.py --every 10 --top 30 >"$LOGS/signals.log" 2>&1 &
(cd "$CORE" && nohup ./tad --addr 127.0.0.1:61613 --every 5s >"$LOGS/tad.log" 2>&1 &) 2>/dev/null
(cd "$CORE" && nohup ./forecast --addr 127.0.0.1:61613 >"$LOGS/forecast.log" 2>&1 &) 2>/dev/null
sleep 2; ok "signals · tad · forecast"

say "6 · books and the dashboard"
if [ "$TRADE" = "1" ]; then
  rm -f "$ROOT/state/STOP"
  ok "trading requested: state/STOP cleared"
  pgrep -f supervisor.py >/dev/null || nohup python3 -u supervisor.py --every 60 --hours 8 >"$LOGS/supervisor.log" 2>&1 &
else
  ok "books not started (no --trade)"
fi
pgrep -f "master.py --host" >/dev/null || nohup python3 -u master.py --host 0.0.0.0 --port 8799 >"$LOGS/master.log" 2>&1 &
wait_for "http://127.0.0.1:8799/api/health" 25 "dashboard on :8799" || true

say "7 · the Envelope Explorer"
pkill -f "bin/envelopeapi" >/dev/null 2>&1
(cd "$CORE" && nohup ./bin/envelopeapi --port 8802 --dir ../state/lake3 >"$LOGS/envelopeapi.log" 2>&1 &)
wait_for "http://127.0.0.1:8802/api/stats" 60 "explorer on :8802" || exit 1

cat <<BANNER

  CryptAlyst is up.

  trading             $([ "$TRADE" = "1" ] && echo "ON" || echo "OFF — stop flag set, no books running")
  dashboard           http://127.0.0.1:8799
  envelope explorer   http://127.0.0.1:8802
  recorder            http://127.0.0.1:8801/stats
  market data         http://127.0.0.1:8796/health

BANNER
