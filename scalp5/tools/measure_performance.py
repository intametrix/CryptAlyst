#!/usr/bin/env python3
"""Per-book performance baseline: hit-rate, expectancy, realised PnL.

Reads state/fills.jsonl (net_pct is already fee-inclusive) and reports, per
engine, the trades, hit-rate, mean net %, and realised $ PnL — sorted by PnL.
This is the "measure" step before any tuning.

    python3 tools/measure_performance.py [fills.jsonl]
"""
import json
import sys
import os
from collections import defaultdict

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state", "fills.jsonl")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    agg = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "net": 0.0,
                               "win_pnl": 0.0, "loss_pnl": 0.0, "held": 0.0})
    total = 0
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            e = d.get("engine") or "?"
            net = d.get("net_pct")
            pnl = d.get("pnl_usd") or 0.0
            if net is None:
                continue
            a = agg[e]
            a["n"] += 1
            a["pnl"] += pnl
            a["net"] += net
            a["held"] += d.get("held_s") or 0.0
            if net > 0:
                a["wins"] += 1
                a["win_pnl"] += pnl
            else:
                a["loss_pnl"] += pnl
            total += 1

    rows = []
    for e, a in agg.items():
        n = a["n"]
        rows.append((a["pnl"], e, n, a["wins"], n - a["wins"],
                     100.0 * a["wins"] / n if n else 0.0,
                     a["net"] / n if n else 0.0,
                     a["win_pnl"], a["loss_pnl"], a["held"] / n if n else 0.0))

    rows.sort(reverse=True)
    print("ENGINE              trades  win%   avg_net%   realised$     avg_hold_s")
    print("-" * 78)
    for pnl, e, n, w, l, wr, avgnet, wpnl, lpnl, held in rows:
        print("%-18s %6d  %5.1f  %8.3f  %10.2f  %8.1f" % (e, n, wr, avgnet, pnl, held))
    print("-" * 78)
    sumpnl = sum(r[0] for r in rows)
    sumn = sum(r[2] for r in rows)
    print("TOTAL  %d trades, realised $%.2f across %d books" % (sumn, sumpnl, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
