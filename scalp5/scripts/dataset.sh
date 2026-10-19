#!/bin/bash
# Build a training dataset from the lakes and train a model on it.
#   ./scripts/dataset.sh [bar_seconds] [horizon_bars]
set -eu
cd "$(dirname "$0")/.." || exit 1
BAR=${1:-60}
HORIZON=${2:-3}
LAKES="state/lake,state/lake2,state/lake3,state/lake4"
# prefer the runtime interpreter: it carries numpy and pandas
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in /Users/users1/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3 python3; do
    if "$c" -c "import numpy" 2>/dev/null; then PY="$c"; break; fi
  done
fi

echo "· historian: aggregating $LAKES into ${BAR}s bars"
./core/bin/historian --dir "$LAKES" --out datasets --bar "$BAR" | tail -12

echo "· trainer: horizon ${HORIZON} bars, time-based split"
"$PY" tools/train.py --csv datasets/features.csv --horizon "$HORIZON" | tail -20
