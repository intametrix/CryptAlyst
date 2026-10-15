"""Technical indicators, implemented from their original definitions.

Written against Wilder's own smoothing (not a simple moving average) because
that is what the Turtle system's ``N`` actually is, and because EMA-vs-Wilder
differences compound over a 20-period average and shift stop distances.

Every function is a pure function over ``Bar`` sequences and returns a list
aligned index-for-index with the input, using ``None`` where the indicator is
not yet defined. That alignment is what lets the signal layer ask questions
like "was this bar a 20-bar breakout *and* was ADX above 25 on the same bar?"
without index bookkeeping.

The Turtles traded daily bars, but nothing here is timeframe-specific; the
caller chooses the interval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .klines import Bar

# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

def true_range(bars: Sequence[Bar]) -> List[float]:
    """Wilder's True Range: the greatest of the three gaps.

    The first bar has no previous close, so it uses its own high-low.
    """
    out: List[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            out.append(bar.high - bar.low)
            continue
        previous_close = bars[index - 1].close
        out.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return out


def sma(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            out[index] = running / period
    return out


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Standard EMA, seeded with the SMA of the first ``period`` values."""
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    multiplier = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = (values[index] - previous) * multiplier + previous
        out[index] = previous
    return out


def wilder_smooth(values: Sequence[float], period: int) -> List[Optional[float]]:
    """Wilder's smoothing (the RMA): ``prev + (value - prev) / period``.

    Seeded with the simple mean of the first ``period`` values, which is what
    Wilder specifies and what makes ``atr`` reproduce published ATR values.
    """
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    previous = seed
    for index in range(period, len(values)):
        previous = previous + (values[index] - previous) / period
        out[index] = previous
    return out


# --------------------------------------------------------------------------
# Volatility: N
# --------------------------------------------------------------------------

def atr(bars: Sequence[Bar], period: int = 20) -> List[Optional[float]]:
    """Average True Range, Wilder-smoothed. This is the Turtles' ``N``."""
    if not bars:
        return []
    return wilder_smooth(true_range(bars), period)


def atr_percent(bars: Sequence[Bar], period: int = 20) -> List[Optional[float]]:
    """ATR as a fraction of close -- the comparable across symbols."""
    values = atr(bars, period)
    out: List[Optional[float]] = []
    for bar, value in zip(bars, values):
        if value is None or bar.close <= 0:
            out.append(None)
        else:
            out.append(value / bar.close)
    return out


# --------------------------------------------------------------------------
# Breakout channels
# --------------------------------------------------------------------------

@dataclass
class Donchian:
    """Rolling channel plus the level a breakout must clear.

    ``upper``/``lower`` include the current bar and describe the channel you
    would draw. ``breakout_upper``/``breakout_lower`` **exclude** the current
    bar, which is the level the Turtles actually placed their entry stop above
    (comparing a bar's high against a channel that includes that same bar is a
    tautology -- it always equals the high).
    """

    upper: List[Optional[float]]
    lower: List[Optional[float]]
    breakout_upper: List[Optional[float]]
    breakout_lower: List[Optional[float]]
    period: int


def donchian(bars: Sequence[Bar], period: int = 20) -> Donchian:
    """Donchian channel over ``period`` bars, with prior-bar breakout levels."""
    count = len(bars)
    upper: List[Optional[float]] = [None] * count
    lower: List[Optional[float]] = [None] * count
    breakout_upper: List[Optional[float]] = [None] * count
    breakout_lower: List[Optional[float]] = [None] * count
    if period <= 0:
        return Donchian(upper, lower, breakout_upper, breakout_lower, period)

    for index in range(count):
        # Channel including the current bar.
        start = index - period + 1
        if start >= 0:
            window = bars[start : index + 1]
            upper[index] = max(b.high for b in window)
            lower[index] = min(b.low for b in window)
        # Breakout level: the highest high of the *prior* `period` bars.
        prior_start = index - period
        if prior_start >= 0:
            prior = bars[prior_start:index]
            breakdown_high = max(b.high for b in prior)
            breakdown_low = min(b.low for b in prior)
            # The Turtles bought at a stop one tick above the prior high; we
            # keep the raw level and let the caller apply a tick buffer.
            breakout_upper[index] = breakdown_high
            breakout_lower[index] = breakdown_low
    return Donchian(upper, lower, breakout_upper, breakout_lower, period)


# --------------------------------------------------------------------------
# Momentum / trend strength
# --------------------------------------------------------------------------

def rsi(bars: Sequence[Bar], period: int = 14) -> List[Optional[float]]:
    """Wilder's RSI."""
    count = len(bars)
    out: List[Optional[float]] = [None] * count
    if count < period + 1:
        return out

    gains: List[float] = [0.0]
    losses: List[float] = [0.0]
    for index in range(1, count):
        change = bars[index].close - bars[index - 1].close
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_value(avg_gain, avg_loss)

    for index in range(period + 1, count):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
        out[index] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@dataclass
class AdxResult:
    adx: List[Optional[float]]
    plus_di: List[Optional[float]]
    minus_di: List[Optional[float]]
    period: int


def adx(bars: Sequence[Bar], period: int = 14) -> AdxResult:
    """Wilder's ADX / DMI.

    The original Turtles did not use ADX, but it is the standard way to ask the
    question their entry filter was really about -- "is this market trending or
    chopping?" -- and it is the single most useful addition to a raw breakout
    system, which otherwise bleeds in ranges.
    """
    count = len(bars)
    adx_out: List[Optional[float]] = [None] * count
    plus_di_out: List[Optional[float]] = [None] * count
    minus_di_out: List[Optional[float]] = [None] * count
    if count < period * 2:
        return AdxResult(adx_out, plus_di_out, minus_di_out, period)

    tr_values = true_range(bars)
    plus_dm: List[float] = [0.0] * count
    minus_dm: List[float] = [0.0] * count
    for index in range(1, count):
        up_move = bars[index].high - bars[index - 1].high
        down_move = bars[index - 1].low - bars[index].low
        if up_move > down_move and up_move > 0:
            plus_dm[index] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[index] = down_move

    smoothed_tr = wilder_smooth(tr_values, period)
    smoothed_plus = wilder_smooth(plus_dm, period)
    smoothed_minus = wilder_smooth(minus_dm, period)

    dx_values: List[Optional[float]] = [None] * count
    for index in range(count):
        tr_s = smoothed_tr[index]
        plus_s = smoothed_plus[index]
        minus_s = smoothed_minus[index]
        if tr_s is None or plus_s is None or minus_s is None or tr_s == 0:
            continue
        plus_di = 100.0 * plus_s / tr_s
        minus_di = 100.0 * minus_s / tr_s
        plus_di_out[index] = plus_di
        minus_di_out[index] = minus_di
        denominator = plus_di + minus_di
        if denominator > 0:
            dx_values[index] = 100.0 * abs(plus_di - minus_di) / denominator

    # ADX is Wilder's smoothing of DX, starting once DX has `period` values.
    first_dx = next((i for i, v in enumerate(dx_values) if v is not None), None)
    if first_dx is None:
        return AdxResult(adx_out, plus_di_out, minus_di_out, period)
    seed_index = first_dx + period - 1
    if seed_index >= count:
        return AdxResult(adx_out, plus_di_out, minus_di_out, period)
    seed_window = [v for v in dx_values[first_dx : seed_index + 1] if v is not None]
    if len(seed_window) < period:
        return AdxResult(adx_out, plus_di_out, minus_di_out, period)

    previous = sum(seed_window) / len(seed_window)
    adx_out[seed_index] = previous
    for index in range(seed_index + 1, count):
        value = dx_values[index]
        if value is None:
            continue
        previous = (previous * (period - 1) + value) / period
        adx_out[index] = previous
    return AdxResult(adx_out, plus_di_out, minus_di_out, period)


# --------------------------------------------------------------------------
# Statistics helpers
# --------------------------------------------------------------------------

def returns(bars: Sequence[Bar]) -> List[Optional[float]]:
    """Simple bar-to-bar fractional returns, aligned to the later bar."""
    out: List[Optional[float]] = [None] * len(bars)
    for index in range(1, len(bars)):
        previous = bars[index - 1].close
        if previous > 0:
            out[index] = (bars[index].close - previous) / previous
    return out


def correlation(left: Sequence[float], right: Sequence[float]) -> float:
    """Pearson correlation over the overlapping tail. 0.0 when undefined."""
    size = min(len(left), len(right))
    if size < 3:
        return 0.0
    a = list(left[-size:])
    b = list(right[-size:])
    mean_a = sum(a) / size
    mean_b = sum(b) / size
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return cov / math.sqrt(var_a * var_b)


def percentile_rank(values: Sequence[float], value: float) -> float:
    """Where ``value`` sits in ``values``, as a 0-1 fraction."""
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if not clean:
        return 0.5
    below = sum(1 for v in clean if v <= value)
    return below / len(clean)


def highest(values: Sequence[Optional[float]], period: int) -> Optional[float]:
    window = [v for v in values[-period:] if v is not None]
    return max(window) if window else None


def lowest(values: Sequence[Optional[float]], period: int) -> Optional[float]:
    window = [v for v in values[-period:] if v is not None]
    return min(window) if window else None
