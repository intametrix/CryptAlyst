#!/bin/bash
# Stop everything the start script launched, in reverse order.
cd "$(dirname "$0")/.." || exit 1
for p in envelopeapi master.py supervisor.py signals.py forecast tad busconsumer healthpub signalbus recorder marketdata \
         "alpha_rank.py" "models10.py" "alpha_signals.py" "crypto_pack.py" "executor.py" "skim_pump.py" \
         "hft.py" "movers.py" "grid.py" "curve_rider.py"; do
  pkill -f "$p" >/dev/null 2>&1
done
echo "stopped"
