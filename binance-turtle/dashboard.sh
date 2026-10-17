#!/usr/bin/env bash
# Launcher for the Binance Turtle dashboard.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
exec "$PY" -u -m binance_turtle.dashboard "$@"
