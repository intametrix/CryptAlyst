"""Shared analysis for the Turtle family (MAs, ATR, Donchian, setup)."""

import time

BASE = "https://api.binance.com"
KLINES = BASE + "/api/v3/klines?interval=%s&limit=%d&symbol=%s"

import gofeed
from swing_turtle import jget, all_prices, universe, log


def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
    return e


def atr_wilder(h, l, c, period=14):
    trs = []
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    if len(trs) < period:
        return None
    v = sum(trs[:period]) / period
    for tr in trs[period:]:
        v = (v * (period - 1) + tr) / period
    return v


def median(vals):
    v = sorted(vals)
    return v[len(v) // 2] if v else None


def analyse(symbol, interval="15m", entry_bars=20, exit_bars=10):
    """Bars AND indicators come from the Go backend (it already computes them)."""
    try:
        f = gofeed.bars(symbol, interval, 200)
    except Exception:
        return None
    c, h, l, qv = f["close"], f["high"], f["low"], f["quote"]
    if len(c) < 60:
        return None
    a = gofeed.indicator(f, "atr20") or atr_wilder(h, l, c, 14)
    if not a:
        return None
    upper = max(h[-entry_bars - 1:-1])
    lower = min(l[-entry_bars - 1:-1])
    exit_low = min(l[-exit_bars - 1:-1])
    exit_high = max(h[-exit_bars - 1:-1])
    med_vol = gofeed.indicator(f, "volume_median_20") or median(qv[-21:-1])
    return {
        "symbol": symbol, "price": c[-1], "atr": a,
        "ema50": gofeed.indicator(f, "ema50") or ema(c, 50),
        "upper": upper, "lower": lower,
        "exit_low": exit_low, "exit_high": exit_high,
        "vol_ratio": (qv[-1] / med_vol) if med_vol else 0.0,
        "atr_pct": 100.0 * a / c[-1] if c[-1] else 0.0,
    }


def setup(x, vol_mult=1.5, tol=0.002):
    """Breakout with a NEAR-CHANNEL TOLERANCE.

    Requiring price already through the channel starved the engine: measured
    live, 0 of 24 symbols qualified while 6 sat within 0.2%% of their high.
    Within `tol` of the level now counts as a breakout in progress.
    """
    if not x:
        return None
    if x["vol_ratio"] < vol_mult:
        return None
    long_break = x["price"] >= x["upper"] * (1.0 - tol) and x["price"] > x["ema50"]
    short_break = x["price"] <= x["lower"] * (1.0 + tol) and x["price"] < x["ema50"]
    if not (long_break or short_break):
        return None
    side = "buy" if long_break else "sell"
    two_n = 2.0 * x["atr"]
    if side == "buy":
        stop = x["price"] - two_n
        trail = x["exit_low"]
    else:
        stop = x["price"] + two_n
        trail = x["exit_high"]
    return {"side": side, "entry": x["price"], "stop": stop, "trail": trail,
            "n": x["atr"], "vol_ratio": round(x["vol_ratio"], 2),
            "atr_pct": round(x["atr_pct"], 2)}


