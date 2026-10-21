#!/usr/bin/env bash
# boot.sh — start / stop / status for the core Go services that make the
# lake: recorder, envelope explorer, health publisher, and the reference
# data registry. Python engines are platform.sh's business; the market-data
# backend is backend/'s business. This script is the missing boot sequence
# for core/bin services.
#
#   ./boot.sh start     start every core service (skips ones already up)
#   ./boot.sh stop      stop them
#   ./boot.sh status    what is up
#   ./boot.sh build     rebuild every binary in bin/
#
# Services and their home arguments (must match what is already running —
# recorder and explorer currently share lake3):
#
#   recorder    8801   --data ../state/lake3
#   envelopeapi 8802   --dir ../state/lake3
#   healthpub   (STOMP publisher, no HTTP port)
#   refdata     8790   --data ../state/refdata   (seed applied at boot, idempotent)

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1

LOGS="$(cd "$HERE/.." && pwd)/logs"
mkdir -p "$LOGS" ../state/refdata
GO="${GO:-go}"

up() { curl -fsS --max-time 2 -o /dev/null "http://127.0.0.1:$1/" 2>/dev/null; }

# Liveness is service-specific: recorder serves no "/" (any HTTP probe 404s
# even while healthy), refdata speaks /healthz, the explorer serves "/".
alive() { # name
  case "$1" in
    recorder)    pgrep -f "bin/recorder"    >/dev/null 2>&1 ;;
    envelopeapi) up 8802 ;;
    healthpub)   pgrep -f "bin/healthpub"   >/dev/null 2>&1 ;;
    refdata)     curl -fsS --max-time 2 -o /dev/null "http://127.0.0.1:8790/healthz" 2>/dev/null ;;
  esac
}

ensure_bin() { # cmd binary
  local cmd="$1" bin="$2"
  if [ ! -x "$bin" ]; then
    echo "  building $bin"
    "$GO" build -o "$bin" "./cmd/$cmd" || return 1
  fi
}

build_all() {
  for cmd in recorder envelopeapi healthpub refdata; do
    echo "  building bin/$cmd"
    "$GO" build -o "bin/$cmd" "./cmd/$cmd" || return 1
  done
}

start_all() {
  echo "starting core services"
  ensure_bin recorder bin/recorder || exit 1
  ensure_bin envelopeapi bin/envelopeapi || exit 1
  ensure_bin healthpub bin/healthpub || exit 1
  ensure_bin refdata bin/refdata || exit 1

  if alive recorder; then
    echo "  recorder    already up :8801"
  else
    nohup ./bin/recorder --port 8801 --data ../state/lake3 > "$LOGS/recorder.log" 2>&1 &
    sleep 1; echo "  recorder    started  :8801"
  fi

  if alive envelopeapi; then
    echo "  envelopeapi already up :8802"
  else
    nohup ./bin/envelopeapi --port 8802 --dir ../state/lake3 > "$LOGS/envelopeapi.log" 2>&1 &
    sleep 1; echo "  envelopeapi started  :8802"
  fi

  if alive healthpub; then
    echo "  healthpub   already up"
  else
    nohup ./bin/healthpub --every 5s > "$LOGS/healthpub.log" 2>&1 &
    sleep 1; echo "  healthpub   started"
  fi

  if alive refdata; then
    echo "  refdata     already up :8790"
  else
    # -seed is an idempotent upsert; re-applying at every boot keeps the
    # registry converged on the committed seed.
    nohup ./bin/refdata --addr 127.0.0.1:8790 --data ../state/refdata \
      --seed ./cmd/refdata/seed.json > "$LOGS/refdata.log" 2>&1 &
    sleep 1
    if alive refdata; then
      echo "  refdata     started  :8790"
    else
      echo "  refdata     FAILED to start — see $LOGS/refdata.log"
    fi
  fi
}

stop_all() {
  echo "stopping core services"
  pkill -f "bin/refdata"     2>/dev/null && echo "  refdata     stopped" || echo "  refdata     not running"
  pkill -f "bin/healthpub"   2>/dev/null && echo "  healthpub   stopped" || echo "  healthpub   not running"
  pkill -f "bin/envelopeapi" 2>/dev/null && echo "  envelopeapi stopped" || echo "  envelopeapi not running"
  pkill -f "bin/recorder"    2>/dev/null && echo "  recorder    stopped" || echo "  recorder    not running"
}

status_all() {
  echo "core services"
  for svc in recorder envelopeapi refdata healthpub; do
    printf '  %-12s ' "$svc"; alive "$svc" && echo up || echo down
  done
  if alive refdata; then
    echo
    curl -fsS --max-time 3 "http://127.0.0.1:8790/v1/stats" | python3 -c '
import json,sys
d=json.load(sys.stdin)["data"]
print("  refdata: %(backend)s — %(instruments)d instruments, %(documents)d docs, %(edges)d edges" % d)'
  fi
}

case "${1:-status}" in
  start)   start_all ;;
  stop)    stop_all ;;
  restart) stop_all; sleep 2; start_all ;;
  build)   build_all ;;
  status)  status_all ;;
  *) echo "usage: $0 {start|stop|restart|build|status}" ;;
esac
