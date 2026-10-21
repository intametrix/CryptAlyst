#!/usr/bin/env python3
"""clean_profit - close only the positions that are in profit.

The owner's clean: bank what is green, leave what is red alone. Nothing here closes a
losing position; the risk plane's stop is the only thing that may, and only at its
threshold.

    python3 tools/clean_profit.py           # bank the green positions
    python3 tools/clean_profit.py --dry     # show what it would bank
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import stop_loss  # prices(), rows()

DRY = "--dry" in sys.argv


def main():
    px = stop_loss.prices()
    if not px:
        print("no prices - refusing to act blind")
        return
    import master
    banked, kept = [], []
    for engine, d, f in stop_loss.rows():
        for p in list(d.get("positions") or []):
            sym = (p.get("symbol") or "").upper()
            entry = float(p.get("entry") or 0)
            mark = float(px.get(sym) or 0)
            if not entry or not mark:
                kept.append("%s %s unpriced" % (engine, sym))
                continue
            chg = (mark / entry - 1.0) * 100.0
            if str(p.get("side") or "buy").lower() in ("sell", "short"):
                chg = -chg
            if chg <= 0:
                kept.append("%s %s %+.2f%% (left open)" % (engine, sym, chg))
                continue
            if DRY:
                print("  would bank %-16s %-10s %+.2f%%" % (engine, sym, chg))
                continue
            out = master.flatten_position(engine, sym, reason="profit_clean", prices=px)
            banked.append("%s %s %+.2f%% -> %s" % (engine, sym, chg, out.get("realised", out)))
    for b in ([] if DRY else banked):
        print("  banked", b)
    print("\nbanked %d · left open %d" % (len(banked), len(kept)))
    for k in kept[:10]:
        print("  kept:", k)


if __name__ == "__main__":
    main()
