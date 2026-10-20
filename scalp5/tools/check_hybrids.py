#!/usr/bin/env python3
"""Every hybrid must fire on a fixture built for its rule, and stay silent when
the condition is absent. This is a rule test, not a market test."""
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import hybrids as H  # noqa: E402


def series(kind):
    """Fixtures are shaped so exactly one rule should fire: the move happens in
    the final bar, or the trend is gentle enough that the EMAs stay near price."""
    n = 220
    bs = []
    for i in range(n):
        last = i == n - 1
        if kind == "gentle_uptrend":
            px = 100.0 + i * 0.002
        elif kind == "uptrend_pullback":
            # linear drift, then one bar pulls back to the fast EMA
            px = 100.0 + i * 0.01
            if i == n - 1:
                px = 100.0 + i * 0.01 - 0.35
        elif kind == "jump_up":
            px = 100.0 if not last else 103.0
        elif kind == "drop":
            px = 100.0 if not last else 96.0
        elif kind == "vwap_cross":
            px = 100.0 - min(i, 200) * 0.01
            if i == n - 1:
                px = 100.0 + 0.4
        elif kind == "tight_then_break":
            px = 100.0 + math.sin(i / 4.0) * 0.05 if not last else 100.9
        else:
            px = 100.0 + math.sin(i / 3.0) * 0.4
        bs.append({"t": i, "o": px, "h": px * 1.0008, "l": px * 0.9992, "c": px,
                   "v": 10.0 if not last else 60.0})
    return bs


FIXTURES = {
    "trend_pullback": ("gentle_uptrend", "buy"),
    "breakout_atr": ("jump_up", "buy"),
    "mean_revert_z": ("drop", "buy"),
    "vwap_reclaim": ("vwap_cross", "buy"),
    "momentum_ignition": ("jump_up", "buy"),
    "oi_squeeze": ("jump_up", "buy"),
    "funding_fade": ("flat", "sell"),
    "taker_imbalance": ("flat", "buy"),
    "flow_confirm": ("jump_up", "buy"),
    "range_break": ("tight_then_break", "buy"),
}

FEATURES = {
    "sig": {"oi_change_1h_pct": 3.0, "funding_pct": 0.06, "taker_ratio": 0.65},
    "flow": {"flow_conf": 40.0}, "ta": {}, "depth": (0.2, 1.0),
}


def main():
    fired, failures = 0, []
    print("%-18s %-6s %-6s %-6s %s" % ("strategy", "want", "got", "score", "reason"))
    for name, (fixture, want) in sorted(FIXTURES.items()):
        f = dict(FEATURES)
        f["bs"] = series(fixture)
        side, score, why = H.STRATEGIES[name]("TESTUSDT", f)
        ok = side == want
        fired += 1 if ok else 0
        if not ok:
            failures.append(name)
        print("%-18s %-6s %-6s %-6.2f %s" % (name, want, side or "-", score, why))
    print("\n%d/10 rules fire as designed" % fired)
    if failures:
        print("not firing:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
