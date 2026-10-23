#!/usr/bin/env python3
"""Realistic scenario harness — fabricate a symbol's inputs and check the models.

One symbol (BTCUSDT). Each scenario fabricates realistic inputs:
  - OHLCV bars (real price levels, wicks, volume)
  - funding rate (the premium index a funding model reads)
  - order book (bids/asks, for the maker quote)
  - an alert (kind/severity/metrics) where a detector would fire one
Then runs the models and prints ACTUAL vs EXPECTED — "did the model do the
correct thing?"

    python3 tools/test_realistic_scenarios.py
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
P0 = 60000.0   # realistic price level


# --- realistic input fabricators -------------------------------------------
def trend_bars(n=60, start=P0, step_pct=0.002, vol_base=800.0, last_break=True):
    """A steady trend with realistic wicks and varying volume. last_break makes
    the final bar close at a fresh extreme on 3x volume (the decision bar)."""
    bars = []
    px = start
    for i in range(n):
        c = px * (1 + step_pct)
        h = c * 1.0012          # small upper wick
        l = px * 0.9988         # small lower wick
        v = vol_base * (1 + 0.15 * (i % 7))
        bars.append({"t": i * 60000, "o": round(px, 1), "h": round(h, 1),
                     "l": round(l, 1), "c": round(c, 1), "v": round(v, 1)})
        px = c
    if last_break:
        if step_pct >= 0:
            # break UP: close at a fresh high, on a clear volume spike
            bars[-1]["c"] = bars[-1]["h"] = round(bars[-1]["c"] * 1.004, 1)
        else:
            # break DOWN: close at a fresh low, on a clear volume spike
            bars[-1]["c"] = bars[-1]["l"] = round(bars[-1]["c"] * 0.996, 1)
        bars[-1]["v"] = round(vol_base * 5, 1)   # >3x the varying average
    return bars


def range_bars(n=60, mid=P0, width_pct=0.003, vol_base=500.0):
    """A flat, mean-reverting range."""
    bars = []
    px = mid
    up = True
    for i in range(n):
        px = mid * (1 + width_pct if up else 1 - width_pct)
        up = not up
        bars.append({"t": i * 60000, "o": round(mid, 1), "h": round(px * 1.001, 1),
                     "l": round(px * 0.999, 1), "c": round(px, 1),
                     "v": round(vol_base, 1)})
    return bars


def flash_crash():
    """A quiet range, then one -5% bar on 8x volume (forced selling)."""
    bars = range_bars()
    bars.append({"t": 60 * 60000, "o": P0, "h": P0, "l": P0 * 0.95,
                 "c": P0 * 0.95, "v": 4000.0})
    return bars


def pump():
    """A quiet range, then one +5% bar on 8x volume with heavy taker buying."""
    bars = range_bars()
    bars.append({"t": 60 * 60000, "o": P0, "h": P0 * 1.05, "l": P0,
                 "c": P0 * 1.05, "v": 4000.0})
    return bars


def funding_rate(rate):
    """The premium-index reply a funding model reads."""
    return {"lastFundingRate": "%s" % rate}


def order_book(mid=P0, spread_bps=1.0):
    """A realistic 5-level book around mid."""
    half = mid * spread_bps / 10000.0
    return {"bids": [[str(round(mid - half, 1)), "1.5"], [str(round(mid - half * 2, 1)), "2.0"],
                     [str(round(mid - half * 3, 1)), "2.5"]],
            "asks": [[str(round(mid + half, 1)), "1.5"], [str(round(mid + half * 2, 1)), "2.0"],
                     [str(round(mid + half * 3, 1)), "2.5"]]}


# --- the transcript ---------------------------------------------------------
def main():
    results = []

    def expect(label, got, want):
        results.append((label, got, want))

    # 1. bull trend — momentum should chase up, reversion should fade the stretch
    up = trend_bars(step_pct=+0.002)
    expect("bull: donchian55 breakout -> buy", models10.r_donchian55(up), "buy")
    expect("bull: vol_break (3x vol, close high) -> buy", models10.r_vol_break(up), "buy")
    expect("bull: vwap_revert fades the stretch -> sell", models10.r_vwap_revert(up), "sell")

    # 2. bear trend — momentum short, reversion buys the flush
    down = trend_bars(step_pct=-0.002)
    expect("bear: donchian55 breakdown -> sell", models10.r_donchian55(down), "sell")
    expect("bear: vwap_revert buys the flush -> buy", models10.r_vwap_revert(down), "buy")

    # 3. flat range — nothing to do
    rng = range_bars()
    expect("range: donchian55 -> hold", models10.r_donchian55(rng), None)
    expect("range: vol_break (no spike) -> hold", models10.r_vol_break(rng), None)

    # 4. flash crash — cascade buys the rebound; momentum sees a breakdown
    crash = flash_crash()
    expect("crash: cascade buys the flush", alpha.cascade_signal(crash), True)
    expect("crash: donchian55 -> sell", models10.r_donchian55(crash), "sell")

    # 5. pump — flow_follow rides the taker flow; sweep_reversal fades a violent burst
    p = pump()
    alert = {"kind": "PUMP", "severity": "HIGH",
             "metrics": {"taker_buy_ratio": 0.8, "burst_multiple": 8.0}}
    expect("pump: flow_follow rides taker flow -> buy",
           crypto_strategies.flow_follow(alert, {}, set())[0], "buy")
    crit = {"kind": "PUMP", "severity": "CRITICAL", "metrics": {"burst_z": 12.0}}
    expect("pump(CRITICAL): sweep_reversal fades the sweep -> sell",
           crypto_strategies.sweep_reversal(crit, {}, set())[0], "sell")

    # 6. funding extreme — fade the crowd paying to hold
    models10.jget = lambda u, t=8.0: funding_rate(0.0015)
    expect("funding +15bps: fade short (sell)", models10.r_funding_fade(SYM), "sell")
    models10.jget = lambda u, t=8.0: funding_rate(-0.0015)
    expect("funding -15bps: fade long (buy)", models10.r_funding_fade(SYM), "buy")

    # 7. the maker quote on a fabricated book — joins the bid, never crosses
    maker.book = lambda s: order_book()
    px, prob, _ = maker.quote(SYM, "buy", 5000.0)
    expect("maker: quote joins the bid, never crosses", px <= P0, True)
    expect("maker: fill probability in [0.05, 0.95]", 0.05 <= prob <= 0.95, True)

    # report
    bad = 0
    for label, got, want in results:
        passed = got == want
        bad += 0 if passed else 1
        print(("PASS " if passed else "FAIL ") + label +
              ("" if passed else "   got %r  should be %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(results), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
