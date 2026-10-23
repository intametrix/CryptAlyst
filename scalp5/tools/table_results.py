#!/usr/bin/env python3
"""Consolidated results table: what each model DID vs what it SHOULD do.

One command that re-runs every scenario and prints the comparison, so after any
parameter tweak we can see in one screen whether the models still behave.

    python3 tools/table_results.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tactics
tactics.require_enabled = lambda *a, **k: None

import models10
import alpha
import crypto_strategies
import maker

SYM = "BTCUSDT"
P0 = 60000.0


# --- fabricators (same shapes the scenario tests use) -----------------------
def ranging(n=30, mid=P0):
    return [{"t": i * 60000, "o": mid, "h": mid * 1.001, "l": mid * 0.999,
             "c": mid, "v": 500.0} for i in range(n)]


def trend(n=60, step_pct=0.002):
    bars, px = [], P0
    for i in range(n):
        c = px * (1 + step_pct)
        bars.append({"t": i * 60000, "o": round(px, 1), "h": round(c * 1.0012, 1),
                     "l": round(px * 0.9988, 1), "c": round(c, 1),
                     "v": round(500 * (1 + 0.15 * (i % 7)), 1)})
        px = c
    if step_pct >= 0:
        bars[-1]["c"] = bars[-1]["h"] = round(bars[-1]["c"] * 1.004, 1)
    else:
        bars[-1]["c"] = bars[-1]["l"] = round(bars[-1]["c"] * 0.996, 1)
    bars[-1]["v"] = 2500.0
    return bars


def crash():
    bars = ranging()
    bars.append({"t": 30 * 60000, "o": P0, "h": P0, "l": P0 * 0.95, "c": P0 * 0.95, "v": 4000.0})
    return bars


def main():
    # (model, scenario, actual, expected)
    rows = []

    up = trend(60, +0.002)
    dn = trend(60, -0.002)
    rng = ranging()
    cr = crash()

    def add(model, scenario, got, want):
        rows.append((model, scenario, got, want))

    # momentum / reversion on trend + range
    add("donchian55", "bull breakout", models10.r_donchian55(up), "buy")
    add("donchian55", "bear breakdown", models10.r_donchian55(dn), "sell")
    add("donchian55", "flat range", models10.r_donchian55(rng), None)
    add("vol_break", "bull 3x-vol close-high", models10.r_vol_break(up), "buy")
    add("vol_break", "no volume spike", models10.r_vol_break(rng), None)
    add("vwap_revert", "bull stretched", models10.r_vwap_revert(up), "sell")
    add("vwap_revert", "bear flushed", models10.r_vwap_revert(dn), "buy")
    add("cascade", "flash crash", alpha.cascade_signal(cr), True)
    add("cascade", "quiet market", alpha.cascade_signal(rng), False)

    # funding
    models10.jget = lambda u, t=8.0: {"lastFundingRate": "0.0015"}
    add("funding_fade", "funding +15bps", models10.r_funding_fade(SYM), "sell")
    models10.jget = lambda u, t=8.0: {"lastFundingRate": "-0.0015"}
    add("funding_fade", "funding -15bps", models10.r_funding_fade(SYM), "buy")
    models10.jget = lambda u, t=8.0: {"lastFundingRate": "0.0001"}
    add("funding_fade", "funding 1bps", models10.r_funding_fade(SYM), None)
    add("funding_side", "+15bps", alpha.funding_side(0.0015), "sell")
    add("funding_side", "-15bps", alpha.funding_side(-0.0015), "buy")
    add("funding_side", "inside 2bps", alpha.funding_side(0.0001), None)

    # alerts
    alert = {"kind": "PUMP", "severity": "HIGH",
             "metrics": {"taker_buy_ratio": 0.8, "burst_multiple": 8.0}}
    add("flow_follow", "pump + taker flow", crypto_strategies.flow_follow(alert, {}, set())[0], "buy")
    crit = {"kind": "PUMP", "severity": "CRITICAL", "metrics": {"burst_z": 12.0}}
    add("sweep_reversal", "CRITICAL pump", crypto_strategies.sweep_reversal(crit, {}, set())[0], "sell")
    add("model_stack", "2 buys agree", crypto_strategies.model_stack({}, {"a": 1, "b": 1}, set())[0], "buy")
    add("model_stack", "2 sells agree", crypto_strategies.model_stack({}, {"a": -1, "b": -1}, set())[0], "sell")
    add("model_stack", "split vote", crypto_strategies.model_stack({}, {"a": 1, "b": -1}, set())[0], None)

    # maker quote
    maker.book = lambda s: {"bids": [["59999.0", "1.5"]], "asks": [["60001.0", "1.5"]]}
    px, prob, _ = maker.quote(SYM, "buy", 5000.0)
    add("maker.quote", "join the bid", "yes" if px <= P0 else "no", "yes")
    add("maker.quote", "prob bounded", "yes" if 0.05 <= prob <= 0.95 else "no", "yes")

    # print the table
    w = max(len(r[0]) for r in rows)
    ws = max(len(r[1]) for r in rows)
    hdr = "%-*s  %-*s  %-8s  %-8s  %s" % (w, "MODEL", ws, "SCENARIO", "ACTUAL", "EXPECTED", "RESULT")
    print(hdr)
    print("-" * len(hdr))
    bad = 0
    for model, scenario, got, want in rows:
        ok = got == want
        bad += 0 if ok else 1
        print("%-*s  %-*s  %-8s  %-8s  %s" % (
            w, model, ws, scenario, str(got), str(want), "PASS" if ok else "FAIL"))
    print("-" * len(hdr))
    print("%d rows, %d failed" % (len(rows), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
