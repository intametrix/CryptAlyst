"""Indicators for the Turtle stack.

All functions return lists aligned 1:1 with the input bars, with ``None``
where the indicator is not yet defined.  Nothing here looks ahead: a value at
index ``i`` is computed only from bars ``<= i``, and the Donchian helpers
deliberately use bars strictly *before* ``i`` (a bar never grades its own
homework -- answers s2).

Stdlib only.
"""

import math


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def median(values):
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def stdev(values):
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n < 2:
        return None
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    return math.sqrt(var)


# --------------------------------------------------------------------------
# volatility
# --------------------------------------------------------------------------
def true_range(highs, lows, closes):
    n = len(closes)
    out = [None] * n
    for i in range(n):
        if i == 0:
            out[i] = highs[i] - lows[i]
        else:
            out[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
    return out


def atr_wilder(highs, lows, closes, period=20):
    """Wilder's ATR -- the plan keeps Wilder smoothing (`indicators.py` parity)."""
    n = len(closes)
    out = [None] * n
    if n < period:
        return out
    tr = true_range(highs, lows, closes)
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def realized_vol(closes, window=20, periods_per_year=365):
    """Annualized close-to-close realized vol (crypto trades 365 days)."""
    n = len(closes)
    out = [None] * n
    rets = [None] * n
    for i in range(1, n):
        if closes[i - 1]:
            rets[i] = math.log(closes[i] / closes[i - 1])
    for i in range(window + 1, n):
        window_rets = rets[i - window + 1: i + 1]
        sd = stdev(window_rets)
        if sd is not None:
            out[i] = sd * math.sqrt(periods_per_year)
    return out


# --------------------------------------------------------------------------
# channels
# --------------------------------------------------------------------------
def donchian_prior(highs, lows, period):
    """(upper, lower) over the ``period`` bars strictly before index ``i``."""
    n = len(highs)
    upper = [None] * n
    lower = [None] * n
    for i in range(period, n):
        window_h = highs[i - period:i]
        window_l = lows[i - period:i]
        upper[i] = max(window_h)
        lower[i] = min(window_l)
    return upper, lower


def highest(values, period):
    n = len(values)
    out = [None] * n
    for i in range(period, n):
        out[i] = max(values[i - period:i])
    return out


def lowest(values, period):
    n = len(values)
    out = [None] * n
    for i in range(period, n):
        out[i] = min(values[i - period:i])
    return out


# --------------------------------------------------------------------------
# trend
# --------------------------------------------------------------------------
def adx_dmi(highs, lows, closes, period=14):
    """Wilder ADX/+DI/-DI.  Returns three aligned lists."""
    n = len(closes)
    adx = [None] * n
    pdi = [None] * n
    mdi = [None] * n
    if n <= period + 1:
        return adx, pdi, mdi

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    s_tr = sum(tr[1:period + 1])
    s_p = sum(plus_dm[1:period + 1])
    s_m = sum(minus_dm[1:period + 1])

    dxs = []

    def emit(i, s_tr, s_p, s_m):
        if s_tr <= 0:
            p = m = 0.0
        else:
            p = 100.0 * s_p / s_tr
            m = 100.0 * s_m / s_tr
        pdi[i] = p
        mdi[i] = m
        return 100.0 * abs(p - m) / (p + m) if (p + m) else 0.0

    dxs.append(emit(period, s_tr, s_p, s_m))
    for i in range(period + 1, n):
        s_tr = s_tr - s_tr / period + tr[i]
        s_p = s_p - s_p / period + plus_dm[i]
        s_m = s_m - s_m / period + minus_dm[i]
        dxs.append(emit(i, s_tr, s_p, s_m))

    if len(dxs) >= period:
        val = sum(dxs[:period]) / period
        adx[2 * period - 1] = val
        for j in range(period, len(dxs)):
            val = (val * (period - 1) + dxs[j]) / period
            adx[period + j] = val
    return adx, pdi, mdi


def rsi_wilder(closes, period=14):
    n = len(closes)
    out = [None] * n
    if n <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag = gains / period
    al = losses / period
    out[period] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(period + 1, n):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(ch, 0.0)) / period
        al = (al * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def sma(values, period):
    n = len(values)
    out = [None] * n
    if n < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, n):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------
def percentile_rank(values, window):
    """Fraction of the prior ``window`` values strictly below the current one."""
    n = len(values)
    out = [None] * n
    for i in range(window, n):
        prior = [v for v in values[i - window:i] if v is not None]
        cur = values[i]
        if cur is None or not prior:
            continue
        out[i] = sum(1 for v in prior if v < cur) / float(len(prior))
    return out


def volume_confirmed(quote_volumes, i, cfg):
    """Exact rule from answers s2.

    ``medianVol`` = median of the quote volumes of the 20 completed bars
    strictly before ``i`` (i excluded).  Confirmed iff

        quoteVol(i) >= 1.5 * max(medianVol, $20k)  AND  quoteVol(i) >= $150k

    Fails closed: a dust bar can never confirm.
    """
    start = i - cfg.vol_median_bars
    if start < 0:
        return False, None
    med = median(quote_volumes[start:i])
    if med is None:
        return False, None
    floor = max(med, cfg.vol_median_floor)
    vol = quote_volumes[i]
    ok = vol >= cfg.vol_mult * floor and vol >= cfg.vol_abs_floor
    return bool(ok), med
