#!/usr/bin/env python3
"""Regression test: slippage measurement from the fills ledger.

Pins the adverse-slippage convention (buy filled above signal = positive bps,
sell filled below signal = positive bps) and the untracked-vs-measured split.
Pure, offline: it reads a temp JSONL fixture, no network, no live state.

    python3 tools/test_slippage.py      # exit 0 on pass
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import slippage


def main():
    checks = []

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "fills.jsonl")
        rows = [
            {"engine": "a", "side": "buy", "entry": 100.5, "signal_price": 100.0},   # +50 bps adverse
            {"engine": "a", "side": "sell", "entry": 99.5, "signal_price": 100.0},   # +50 bps adverse
            {"engine": "a", "side": "buy", "entry": 100.0, "signal_price": 100.0},   # 0 bps
            {"engine": "a", "side": "buy", "entry": 101.0},                           # untracked (no signal)
        ]
        with open(p, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

        m = slippage.measure(p)
        checks.append(("measured", m["measured"] == 3, True))
        checks.append(("untracked", m["untracked"] == 1, True))
        checks.append(("p50", m["p50_bps"] == 50.0, True))
        checks.append(("p90", m["p90_bps"] == 50.0, True))
        checks.append(("mean", m["mean_bps"] == 33.33, True))  # (50+50+0)/3

    # empty ledger -> measured 0, no bps
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "empty.jsonl")
        open(p, "w").close()
        m = slippage.measure(p)
        checks.append(("empty measured", m["measured"] == 0, True))
        checks.append(("empty mean", m["mean_bps"] is None, True))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
