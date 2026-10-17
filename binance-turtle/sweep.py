#!/usr/bin/env python3
"""Sensitivity sweep for the Turtle backtest (plan s5).

"An edge that exists only at one parameter point is not an edge -- demand a
plateau."  This re-runs the identical sim across the breakout length, the
volume-confirmation multiple and the per-unit risk, and reports MAR and
expectancy for every cell.

    python3 sweep.py
"""

import itertools
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest as bt
from binance_turtle.binance_api import BinancePublic
from binance_turtle.config import Config

MAJORS = """BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,ADAUSDT,SOLUSDT,DOGEUSDT,LTCUSDT,
LINKUSDT,DOTUSDT,BCHUSDT,ETCUSDT,XLMUSDT,TRXUSDT,EOSUSDT,ATOMUSDT,NEOUSDT,
DASHUSDT,ZECUSDT,VETUSDT,ALGOUSDT,FILUSDT,AVAXUSDT,UNIUSDT,AAVEUSDT,SUSHIUSDT,
COMPUSDT,MKRUSDT,SNXUSDT,CRVUSDT,YFIUSDT,THETAUSDT,MANAUSDT,SANDUSDT,AXSUSDT,
GALAUSDT,NEARUSDT,INJUSDT,APTUSDT,SUIUSDT""".replace("\n", "").split(",")


def main():
    start_ms = int(time.mktime(time.strptime("2019-01-01", "%Y-%m-%d")) * 1000)
    cfg0 = Config()
    api = BinancePublic(cache_dir="cache", verbose=False)
    print("loading %d symbols (cached)..." % len(MAJORS), flush=True)
    data = bt.load_universe(api, cfg0, 0, start_ms, explicit=MAJORS, min_bars=1200)
    print("loaded %d\n" % len(data), flush=True)

    rows = []
    grid = list(itertools.product([40, 55, 70], [1.0, 1.5, 2.0], [0.0025, 0.005, 0.01]))
    for breakout, volmult, risk in grid:
        cfg = Config()
        cfg.primary_entry_bars = breakout
        cfg.vol_mult = volmult
        cfg.risk_per_unit = risk
        res = bt.run_backtest(data, cfg, start_equity=10_000.0)
        m = bt.metrics(res, cfg)
        rows.append({"breakout": breakout, "vol_mult": volmult, "risk": risk,
                     **{k: m.get(k) for k in
                        ("trades", "win_rate", "expectancy_r", "total_r",
                         "max_dd_pct", "cagr_pct", "mar", "return_pct")}})

    rows.sort(key=lambda r: -(r["mar"] or -99))
    print("%-9s %-8s %-6s %6s %6s %8s %8s %7s %6s" %
          ("breakout", "volx", "risk", "trade", "win%", "exp_R", "maxDD%", "MAR", "CAGR%"))
    print("-" * 76)
    for r in rows:
        print("%-9d %-8.1f %-6.3f %6d %6.1f %+8.3f %8.1f %7s %6.1f" % (
            r["breakout"], r["vol_mult"], r["risk"], r["trades"], r["win_rate"],
            r["expectancy_r"], r["max_dd_pct"], r["mar"], r["cagr_pct"]))

    # plateau test: how many of the 27 cells keep a positive expectancy?
    pos = sum(1 for r in rows if (r["expectancy_r"] or 0) > 0)
    print()
    print("cells with positive expectancy: %d/%d" % (pos, len(rows)))
    med = sorted(r["expectancy_r"] for r in rows)[len(rows) // 2]
    print("median expectancy across the grid: %+.3f R" % med)
    with open("sweep_result.json", "w") as fh:
        json.dump(rows, fh, indent=2)
    print("wrote sweep_result.json")


if __name__ == "__main__":
    main()
