#!/usr/bin/env bash
# platform.sh — start / stop / status for the paper-trading platform.
#
#   ./platform.sh status      what is running and how the books look
#   ./platform.sh start       start every service (skips ones already up)
#   ./platform.sh stop        stop the python engines and dashboards
#   ./platform.sh stop:all    also stop the Go backend
#   ./platform.sh restart     stop + start
#   ./platform.sh reset       wipe state and ledgers, then start fresh
#
# Everything is PAPER. No API keys are read, no orders are ever placed.

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1

BACKEND_BIN="${BACKEND_BIN:-/tmp/pumpdump-run/pumpdump70}"
BACKEND_ADDR="127.0.0.1:8787"
MASTER_PORT=8799
TURTLE_PORT=8798
LAB_PORT=8801
LOGS="$HERE/logs"
mkdir -p "$LOGS" state ledger

up() { curl -fsS --max-time 2 -o /dev/null "http://127.0.0.1:$1/" 2>/dev/null; }

start_backend() {
  if up 8787; then echo "  backend      already up"; return; fi
  if [ ! -x "$BACKEND_BIN" ]; then
    echo "  backend      MISSING $BACKEND_BIN — build it first"; return
  fi
  nohup "$BACKEND_BIN" --addr "$BACKEND_ADDR" \
    --timeframes 1d,4h,1h,30m \
    --min-liq 500000 --min-burst-mult 2 --min-burst-z 1.5 \
    --min-move-pct 0.03 --min-sigma-mult 1.0 --min-flow-usd 500 \
    --min-trades 3 --min-coverage 0.5 --min-severity WATCH \
    --alert-history "$HERE/alerts.jsonl" \
    --ledger "$HERE/ledger/backend.jsonl" \
    --turtle-log "$HERE/turtle_sessions.jsonl" \
    --calibration "$HERE/calibration.json" \
    > "$LOGS/backend.log" 2>&1 &
  sleep 2; echo "  backend      started :8787"
}

start_engine() {  # name, port-check-cmd
  local name="$1"; shift
  if pgrep -f "$name.py" >/dev/null 2>&1; then echo "  $name$  already up"; return; fi
  nohup python3 -u "$@" > "$LOGS/$name.log" 2>&1 &
  sleep 1; echo "  $name  started"
}

start_all() {
  echo "starting paper trading"
  start_backend

  pgrep -f "swing_v2.py" >/dev/null || \
    nohup python3 -u swing_v2.py --seconds 86400 --tick 0.5 --scan-every 0.5 \
      --max-positions 16 --max-per-side 16 --basis confidence --min-confidence 0.72 \
      --min-liquidity 10000000 --model-gate veto --cap-pct 1.0 --stop-pct 1.0 \
      --lock-trade-pct 0.25 --trail-ratio 0.30 --trail-arm-pct 0.6 \
      --max-open-risk-pct 80 --rebalance --evict-margin 0.05 \
      --lock-profit-pct 1.0 --lock-cooldown 30 --stale-after 600 --stale-pct -0.5 \
      --risk-pct 1.0 --conviction-cap 2 --watch-min-volume 1000000 --watch-max 400 \
      --equity 2000 > "$LOGS/swing.log" 2>&1 &
  pgrep -f "multi_models.py" >/dev/null || \
    nohup python3 -u multi_models.py --seconds 86400 --universe 300 --min-volume 20000000 \
      --interval 5m --min-agree 3 --tp-pct 1.0 --sl-pct 1.0 --max-hold 600 --lock-usd 20 \
      --scan-every 30 --max-positions 10 --risk-pct 1.0 --equity 2000 \
      > "$LOGS/multimodels.log" 2>&1 &
  pgrep -f "stats_models.py" >/dev/null || \
    nohup python3 -u stats_models.py --seconds 86400 --universe 300 --min-volume 20000000 \
      --interval 5m --min-agree 2 --tp-pct 1.0 --sl-pct 1.0 --max-hold 600 --lock-usd 20 \
      --scan-every 30 --max-positions 10 --risk-pct 1.0 --equity 2000 \
      > "$LOGS/statsmodels.log" 2>&1 &
  # the 10s-2m timeframes are OFF by default: they pay a 0.2% round trip on a
  # 0.0085% median move, so they cannot win. enable with WITH_TIMEFRAMES=1
  if [ "${WITH_TIMEFRAMES:-0}" = "1" ]; then
    up $TURTLE_PORT || \
      nohup python3 -u turtle_multi.py --universe 200 --min-volume 5000000 \
        --tp-pct 1.0 --vol-mult 1.0 --tolerance 0.002 --max-positions 4 --risk-pct 0.5 \
        --min-stop-frac 0.0015 --max-leverage 5 --equity 2000 --port $TURTLE_PORT \
        > "$LOGS/turtle.log" 2>&1 &
    echo "  timeframes  started (opt-in)"
  else
    echo "  timeframes  skipped (set WITH_TIMEFRAMES=1 to enable)"
  fi
  pgrep -f "crypto_strategies.py" >/dev/null || \
    nohup python3 -u crypto_strategies.py --seconds 86400 > "$LOGS/strategies.log" 2>&1 &
  pgrep -f "darwin.py" >/dev/null || \
    nohup python3 -u darwin.py --every 600 > "$LOGS/darwin.log" 2>&1 &
  pgrep -f "sentinel.py" >/dev/null || \
    nohup python3 -u sentinel.py --every 120 --trial 600 > "$LOGS/sentinel.log" 2>&1 &
  pgrep -f "optimizer.py" >/dev/null || \
    nohup python3 -u optimizer.py --every 300 > "$LOGS/optimizer.log" 2>&1 &
  sleep 1
  pgrep -f "master.py --port" >/dev/null || \
    nohup python3 -u master.py --port $MASTER_PORT > "$LOGS/master.log" 2>&1 &
  up $LAB_PORT || \
    nohup python3 -u -m http.server $LAB_PORT --bind 127.0.0.1 > "$LOGS/lab.log" 2>&1 &
  sleep 2
  echo
  echo "  dashboard    http://127.0.0.1:$MASTER_PORT"
  echo "  chart        http://127.0.0.1:$MASTER_PORT/chart"
  echo "  chart lab    http://127.0.0.1:$LAB_PORT/chart_simple.html"
}

stop_all() {
  echo "stopping engines"
  pkill -f "swing_v2.py"     2>/dev/null && echo "  swing      stopped"
  pkill -f "multi_models.py" 2>/dev/null && echo "  multimodels stopped"
  pkill -f "stats_models.py" 2>/dev/null && echo "  statsmodels stopped"
  pkill -f "turtle_multi.py" 2>/dev/null && echo "  turtle     stopped"
  pkill -f "darwin.py"       2>/dev/null && echo "  darwin     stopped"
  pkill -f "crypto_strategies.py" 2>/dev/null && echo "  strategies stopped"
  pkill -f "sentinel.py"     2>/dev/null && echo "  sentinel   stopped"
  pkill -f "optimizer.py"    2>/dev/null && echo "  optimiser  stopped"
  pkill -f "master.py --port" 2>/dev/null && echo "  master     stopped"
  pkill -f "http.server $LAB_PORT" 2>/dev/null && echo "  chart lab  stopped"
  if [ "${1:-}" = "all" ]; then
    pkill -f "pumpdump70" 2>/dev/null && echo "  backend    stopped"
  fi
}

status() {
  echo "services"
  for p in 8787 $TURTLE_PORT $MASTER_PORT $LAB_PORT; do
    printf '  :%-5s ' "$p"; up "$p" && echo up || echo down
  done
  echo
  if ! up $MASTER_PORT; then
    echo "  dashboard is down — run: $0 start"; return
  fi
  curl -fsS --max-time 8 "http://127.0.0.1:$MASTER_PORT/api/all" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("%-12s %10s %9s %8s %8s %6s %5s" % ("ENGINE","EQUITY","RETURN","REALISED","UNREAL","CLOSED","OPEN"))
tot=0.0; n=0
for e in d["engines"]:
    if e.get("equity") is None: continue
    tot+=e["equity"]; n+=1
    print("%-12s %10.2f %+8.2f%% %8.2f %8.2f %6s %5d" % (
      e["key"], e["equity"], e.get("return_pct") or 0, e.get("realised") or 0,
      e.get("unrealised") or 0, e.get("closed"), len(e.get("positions") or [])))
print()
print("live books: %d   combined equity: $%.2f" % (n, tot))
'
}

reset_all() {
  echo "wiping paper state and ledgers"
  rm -f state/*.json ledger/*.jsonl alerts.jsonl turtle_sessions.jsonl 2>/dev/null
  echo "  done — books start from \$2,000 each"
}

case "${1:-status}" in
  start)     start_all ;;
  stop)      stop_all ;;
  stop:all)  stop_all all ;;
  restart)   stop_all; sleep 2; start_all ;;
  reset)     stop_all; sleep 1; reset_all; start_all ;;
  status)    status ;;
  *) echo "usage: $0 {start|stop|stop:all|restart|reset|status}" ;;
esac
