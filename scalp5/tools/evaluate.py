#!/usr/bin/env python3
"""Evaluate the 3 alpha books every 5 seconds.

Prints one line per cycle: total realised PnL and, per book, equity, closed
trades and open positions, plus the last exit reason.

    python3 tools/evaluate.py
"""
import glob
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")


def books():
    out = []
    for f in sorted(glob.glob(os.path.join(STATE, "alpha_*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        tr = d.get("trades") or []
        out.append({
            "engine": d.get("engine"),
            "equity": d.get("equity") or 5000,
            "realised": (d.get("equity") or 5000) - 5000,
            "trades": len(tr),
            "open": len(d.get("positions") or []),
            "last": (tr[-1].get("reason") + " " + tr[-1].get("symbol") + " %+.2f%%" % (tr[-1].get("net_pct") or 0)) if tr else "",
        })
    return out


def main():
    while True:
        bs = books()
        total = sum(b["realised"] for b in bs)
        parts = ["%s %+.0f %dt %do %s" % (b["engine"], b["realised"], b["trades"], b["open"], b["last"]) for b in bs]
        print("%s  pnl %+.2f | %s" % (time.strftime("%H:%M:%S"), total, " | ".join(parts)), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
