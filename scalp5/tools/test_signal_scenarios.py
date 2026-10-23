#!/usr/bin/env python3
"""Signal-scenario tests — one symbol, faked market regimes, expected signals.

The idea: a book trades ONE symbol (TESTUSDT). We fake that symbol's market —
a bull breakout, a bear breakdown, a flat range, a forced-selling crash, a
funding extreme — run the model over it, and check the signal is the one we
think it should be.

    python3 tools/test_signal_scenarios.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tactics
tactics.require_enabled = lambda *a, **k: None

import models10
import alpha

SYM = "TESTUSDT"


# --- faked market data ------------------------------------------------------
def ranging(n=30, mid=100.0):
    """Flat closes inside a narrow band — no trend."""
    return [{"t": i * 60000, "o": mid, "h": mid + 1.0, "l": mid - 1.0,
             "c": mid, "v": 100.0} for i in range(n)]


def bull_breakout():
    """A range that breaks UP: the last bar closes at a fresh high."""
    bars = ranging()
    hi = max(b["h"] for b in bars)
    bars[-1]["c"] = bars[-1]["h"] = hi + 0.5
    return bars


def bear_breakdown():
    """A range that breaks DOWN: the last bar closes at a fresh low."""
    bars = ranging()
    lo = min(b["l"] for b in bars)
    bars[-1]["c"] = bars[-1]["l"] = lo - 0.5
    return bars


def crash():
    """A quiet market, then one -3% 1m bar on 5x volume (forced selling)."""
    bars = ranging()
    bars.append({"t": 30 * 60000, "o": 100.0, "h": 100.0, "l": 97.0,
                 "c": 97.0, "v": 500.0})
    return bars


def funding_rate(rate):
    """Fake the premium index for the one symbol (what r_funding_fade reads)."""
    return {"lastFundingRate": "%s" % rate}


def vol_breakout_buy():
    """A high-volume bar closing near its top — accumulation breakout."""
    bars = ranging()
    bars[-1] = {"t": 30 * 60000, "o": 100.0, "h": 103.0, "l": 99.0, "c": 102.5, "v": 400.0}
    return bars


def vol_breakout_sell():
    """A high-volume bar closing near its bottom — distribution breakdown."""
    bars = ranging()
    bars[-1] = {"t": 30 * 60000, "o": 100.0, "h": 103.0, "l": 99.0, "c": 99.5, "v": 400.0}
    return bars


def vwap_below():
    """Price sits below its volume-weighted average — oversold."""
    bars = ranging()
    bars[-1]["c"] = 98.0
    return bars


def vwap_above():
    """Price sits above its volume-weighted average — overbought."""
    bars = ranging()
    bars[-1]["c"] = 102.0
    return bars


def main():
    checks = []

    def ok(name, got, want):
        checks.append((name, got, want))

    # --- momentum (donchian55): trade the breakout direction ---
    ok("bull breakout  -> BUY", models10.r_donchian55(bull_breakout()), "buy")
    ok("bear breakdown -> SELL", models10.r_donchian55(bear_breakdown()), "sell")
    ok("flat range     -> HOLD", models10.r_donchian55(ranging()), None)

    # --- cascade rebound: buy the forced-selling flush ---
    ok("crash (3% drop, 5x vol) -> BUY the rebound", alpha.cascade_signal(crash()), True)
    ok("quiet market              -> no cascade signal", alpha.cascade_signal(ranging()), False)

    # --- funding: fade the crowd that is paying to hold ---
    models10.jget = lambda u, t=8.0: funding_rate(0.0010)
    ok("funding +10bps -> fade SHORT (sell)", models10.r_funding_fade(SYM), "sell")
    models10.jget = lambda u, t=8.0: funding_rate(-0.0010)
    ok("funding -10bps -> fade LONG (buy)", models10.r_funding_fade(SYM), "buy")
    models10.jget = lambda u, t=8.0: funding_rate(0.0001)
    ok("funding 1bps (inside band) -> HOLD", models10.r_funding_fade(SYM), None)

    # the same funding read through the alpha helper (sell positive, buy negative)
    ok("alpha funding_side: +10bps -> sell", alpha.funding_side(0.0010), "sell")
    ok("alpha funding_side: -10bps -> buy", alpha.funding_side(-0.0010), "buy")
    ok("alpha funding_side: inside 2bps -> none", alpha.funding_side(0.0001), None)

    # --- volume-confirmed breakout (r_vol_break): a move needs volume ---
    ok("vol breakout (4x vol, close near top) -> BUY",
       models10.r_vol_break(vol_breakout_buy()), "buy")
    ok("vol breakdown (4x vol, close near bottom) -> SELL",
       models10.r_vol_break(vol_breakout_sell()), "sell")
    ok("same close but NO volume spike -> no signal",
       models10.r_vol_break(ranging()), None)

    # --- VWAP reversion (r_vwap_revert): volume-weighted mean ---
    ok("price below VWAP -> BUY", models10.r_vwap_revert(vwap_below()), "buy")
    ok("price above VWAP -> SELL", models10.r_vwap_revert(vwap_above()), "sell")

    # --- report ---
    bad = 0
    for name, got, want in checks:
        passed = got == want
        bad += 0 if passed else 1
        print(("PASS " if passed else "FAIL ") + name +
              ("" if passed else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
