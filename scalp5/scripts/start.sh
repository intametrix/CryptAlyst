#!/bin/bash
# Bring CryptAlyst up from a clean slate, in dependency order.
#
#   0 · kill anything left from a previous run, and wait for the ports
#   1 · dependencies: broker, binaries, directories
#   2 · market data -> recorder -> bus services -> intel -> dashboard -> explorer
#   3 · books last, and only when asked for
#
# Every stage either proves itself or names what failed. A stage that cannot be
# proven stops the run: a half-started platform is worse than none.
#
#   ./scripts/start.sh              platform up, trading OFF
#   ./scripts/start.sh --trade      platform up, books running
#   ./scripts/start.sh --keep       do not kill what is already running
set -u
cd "$(dirname "$0")/.." || exit 1

TRADE=0
KILL=1
for arg in "$@"; do
  case "$arg" in
    --trade) TRADE=1 ;;
    --keep)  KILL=0 ;;
    --help|-h)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

ROOT=$(pwd)
CORE="$ROOT/core"
LOGS="$ROOT/logs"
LAKE="$ROOT/state/lake3"
PORTS="8796 8798 8799 8801 8802 8803 8804 8805 8806"
mkdir -p "$LOGS" "$LAKE"

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }
warn(){ printf '  \033[1;33m!\033[0m %s\n' "$*"; }
die() { bad "$*"; echo; echo "  stopped at this stage; nothing further was started."; exit 1; }

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

wait_http() { # wait_http <url> <seconds> <name>
  local url=$1 secs=$2 name=$3 i=0
  while [ $i -lt "$secs" ]; do
    if curl -sS --max-time 2 "$url" >/dev/null 2>&1; then ok "$name"; return 0; fi
    sleep 1; i=$((i+1))
  done
  return 1
}

wait_port() { # wait_port <port> <seconds>
  local p=$1 secs=$2 i=0
  while [ $i -lt "$secs" ]; do
    port_busy "$p" || return 0
    sleep 1; i=$((i+1))
  done
  return 1
}

stomp_ok() {
  (printf 'CONNECT\naccept-version:1.2\nhost:/\nlogin:cryptalyst\npasscode:cryptalyst\n\n\x00' \
     > /dev/tcp/127.0.0.1/61613) 2>/dev/null
}

# ----------------------------------------------------------------- 0 · kill
if [ "$KILL" = "1" ]; then
  say "0 · clearing the previous run"
  KILLED=0
  for pat in \
    supervisor.py models10.py crypto_pack.py executor.py hybrids.py alpha_rank.py \
    alpha_signals.py skim_pump.py curve_rider.py churn.py multi_models.py stats_models.py \
    sympathy_lag.py slow_edges.py crypto_edges.py turtle_multi.py swing_v2.py swing_turtle.py \
    scalp_fast.py skim.py skim_ai.py grid.py hft.py movers.py flash.py fast_ignition.py \
    market_agent.py signals.py heartbeat.py optimiser_svc.py master.py \
    bin/marketdata bin/recorder bin/busconsumer bin/healthpub bin/signalbus bin/envelopeapi
  do
    if pgrep -f "$pat" >/dev/null 2>&1; then
      pkill -f "$pat" 2>/dev/null && KILLED=$((KILLED+1))
    fi
  done
  sleep 2
  # anything still holding a platform port is escalated, not ignored
  for pat in bin/marketdata bin/recorder bin/busconsumer bin/healthpub bin/signalbus bin/envelopeapi master.py; do
    pkill -9 -f "$pat" 2>/dev/null
  done
  sleep 1
  busy=""
  for p in $PORTS; do
    wait_port "$p" 8 || busy="$busy $p"
  done
  [ -n "$busy" ] && die "ports still held after the kill:$busy"
  ok "platform processes stopped ($KILLED patterns matched), ports free"
else
  say "0 · leaving running processes alone (--keep)"
fi

# ------------------------------------------------------- 1 · dependencies
say "1 · dependencies"

# 1a · broker. Start it if it is not there: the bus is the platform's glue.
if stomp_ok; then
  ok "RabbitMQ STOMP reachable on :61613"
else
  warn "RabbitMQ not reachable — trying to start it"
  RMQ=""
  for candidate in "$(command -v rabbitmq-server 2>/dev/null)" \
                   /opt/homebrew/sbin/rabbitmq-server /usr/local/sbin/rabbitmq-server; do
    [ -n "$candidate" ] && [ -x "$candidate" ] && RMQ="$candidate" && break
  done
  if [ -z "$RMQ" ]; then
    die "RabbitMQ is not installed and not running; install it or start it by hand"
  fi
  nohup "$RMQ" >"$LOGS/broker.log" 2>&1 &
  ok "started $RMQ"
  i=0
  while [ $i -lt 45 ]; do
    stomp_ok && break
    sleep 1; i=$((i+1))
  done
  stomp_ok || die "broker did not accept STOMP within 45s (see logs/broker.log)"
  ok "RabbitMQ STOMP reachable on :61613"
fi

# 1b · binaries. Build what is missing rather than failing at exec time.
if [ ! -x "$CORE/bin/marketdata" ] || [ ! -x "$CORE/bin/recorder" ] || [ ! -x "$CORE/bin/envelopeapi" ]; then
  warn "core binaries missing — building"
  ( cd "$CORE" && /opt/homebrew/bin/go build -o bin/ ./cmd/... ) || die "go build failed"
  ok "built core binaries"
else
  ok "core binaries present"
fi
for b in marketdata recorder envelopeapi busconsumer healthpub signalbus; do
  [ -x "$CORE/bin/$b" ] || die "bin/$b is missing after the build"
done
ok "all six service binaries present"

# 1c · optional binaries are named when absent, not silently skipped
for b in tad forecast; do
  [ -x "$CORE/$b" ] && ok "$b present" || warn "$b not built in this tree — that producer stays off"
done

command -v python3 >/dev/null || die "python3 not found"
ok "python3 $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

touch "$LAKE/.write-test" 2>/dev/null && rm -f "$LAKE/.write-test" \
  || die "lake directory is not writable: $LAKE"
ok "lake writable: ${LAKE#$ROOT/}"

curl -sS --max-time 4 -o /dev/null https://api.binance.com/api/v3/ping 2>/dev/null \
  && ok "exchange reachable" || warn "exchange not reachable — the feed will retry"

# the stop flag goes down before any service, so a book that somehow starts
# finds trading already blocked
printf '{"stopped_at": %s, "actor": "start.sh", "reason": "platform start — trading off by default"}' \
  "$(date +%s)000" > "$ROOT/state/STOP"
ok "trading is OFF (state/STOP written)"

# ------------------------------------------------------------- 2 · core
say "2 · market data (the only process that talks to the exchange)"
(cd "$CORE" && nohup ./bin/marketdata --port 8796 --top 400 >"$LOGS/marketdata.log" 2>&1 &)
wait_http "http://127.0.0.1:8796/health" 25 "market data on :8796" || die "market data did not answer"

say "3 · recorder (the only consumer of the bus, the only writer of the lake)"
STORE_ARGS=""
[ -n "${STORE_KV:-}" ]   && STORE_ARGS="$STORE_ARGS --kv $STORE_KV"
[ -n "${STORE_SQL:-}" ]  && STORE_ARGS="$STORE_ARGS --sql $STORE_SQL"
[ -n "${STORE_LSM:-}" ]  && STORE_ARGS="$STORE_ARGS --lsm $STORE_LSM"
[ -n "${STORE_DOCS:-}" ] && STORE_ARGS="$STORE_ARGS --docs $STORE_DOCS"
[ -n "${MONGO_URI:-}" ]  && STORE_ARGS="$STORE_ARGS --mongo $MONGO_URI"
[ -n "${STORE_ROCKS:-}" ] && STORE_ARGS="$STORE_ARGS --rocksdb $STORE_ROCKS"
(cd "$CORE" && nohup ./bin/recorder --port 8801 --data ../state/lake3 $STORE_ARGS >"$LOGS/recorder.log" 2>&1 &)
wait_http "http://127.0.0.1:8801/stats" 20 "recorder on :8801" || die "recorder did not answer"

say "4 · bus services"
(cd "$CORE" && nohup ./bin/busconsumer --port 8798 >"$LOGS/busconsumer.log" 2>&1 &)
(cd "$CORE" && nohup ./bin/healthpub --every 5s >"$LOGS/healthpub.log" 2>&1 &)
(cd "$CORE" && nohup ./bin/signalbus --every 2s >"$LOGS/signalbus.log" 2>&1 &)
wait_http "http://127.0.0.1:8798/" 15 "bus consumer on :8798" || die "bus consumer did not answer"
ok "health publisher and alert intake started"

say "5 · intelligence producers"
nohup python3 -u signals.py --every 10 --top 30 >"$LOGS/signals.log" 2>&1 &
[ -x "$CORE/tad" ]      && (cd "$CORE" && nohup ./tad --addr 127.0.0.1:61613 --every 5s >"$LOGS/tad.log" 2>&1 &)
[ -x "$CORE/forecast" ] && (cd "$CORE" && nohup ./forecast --addr 127.0.0.1:61613 >"$LOGS/forecast.log" 2>&1 &)
sleep 3
pgrep -f "signals.py --every" >/dev/null && ok "signals publishing" || warn "signals.py did not stay up — see logs/signals.log"

say "6 · dashboard"
nohup python3 -u master.py --host 0.0.0.0 --port 8799 >"$LOGS/master.log" 2>&1 &
nohup python3 -u heartbeat.py >"$LOGS/heartbeat.log" 2>&1 &
wait_http "http://127.0.0.1:8799/api/health" 30 "dashboard on :8799" || die "dashboard did not answer"

say "7 · the Envelope Explorer"
(cd "$CORE" && nohup ./bin/envelopeapi --port 8802 --dir ../state/lake3 >"$LOGS/envelopeapi.log" 2>&1 &)
wait_http "http://127.0.0.1:8802/api/stats" 60 "explorer on :8802" || die "explorer did not answer"

# ------------------------------------------------------------- 3 · books
say "8 · books"
if [ "$TRADE" = "1" ]; then
  rm -f "$ROOT/state/STOP"
  ok "trading requested: state/STOP cleared"
  nohup python3 -u supervisor.py --every 60 --hours 8 >"$LOGS/supervisor.log" 2>&1 &
  sleep 5
  pgrep -f supervisor.py >/dev/null && ok "supervisor running, books starting" \
    || die "supervisor did not stay up — see logs/supervisor.log"
else
  ok "books not started (no --trade); the stop flag stays down"
fi

cat <<BANNER

  CryptAlyst is up.

  trading             $([ "$TRADE" = "1" ] && echo "ON — books running" || echo "OFF — stop flag set, no books")
  dashboard           http://127.0.0.1:8799
  envelope explorer   http://127.0.0.1:8802
  recorder            http://127.0.0.1:8801/stats
  market data         http://127.0.0.1:8796/health

  stop it with:       ./scripts/start.sh --help   or  pkill -f 'bin/(marketdata|recorder|envelopeapi)'

BANNER
