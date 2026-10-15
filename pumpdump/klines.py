"""1-minute bar modelling, baseline statistics, and confirmation signals.

Binance kline rows are positional:

====  ==============================  ==================================
idx   field                           notes
====  ==============================  ==================================
0     open time (ms)                  bar start
1     open
2     high
3     low
4     close
5     base-asset volume
6     close time (ms)
7     quote-asset volume              <- the liquidity measure we use
8     number of trades
9     taker buy base-asset volume     <- aggression, buy side
10    taker buy quote-asset volume
11    unused
====  ==============================  ==================================
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .common import clamp, mean, median, pct_change, safe_div, stdev


@dataclass
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float          # base asset
    close_time: int
    quote_volume: float    # quote asset (USD-ish for USDT pairs)
    trades: int
    taker_buy_base: float
    taker_buy_quote: float

    @property
    def is_up(self) -> bool:
        return self.close >= self.open

    @property
    def range_pct(self) -> float:
        """High-low range as a fraction of the low."""
        return safe_div(self.high - self.low, self.low)

    @property
    def body_pct(self) -> float:
        return safe_div(self.close - self.open, self.open)

    @property
    def taker_buy_ratio(self) -> float:
        """Share of base volume that was taker buying, in [0, 1]."""
        return clamp(safe_div(self.taker_buy_base, self.volume, 0.5), 0.0, 1.0)

    @property
    def close_position(self) -> float:
        """Where the close sits inside the bar range: 1 = at the high."""
        span = self.high - self.low
        if span <= 0:
            return 0.5
        return clamp((self.close - self.low) / span, 0.0, 1.0)

    @property
    def upper_wick_ratio(self) -> float:
        """Upper wick as a share of the total range (exhaustion signal)."""
        span = self.high - self.low
        if span <= 0:
            return 0.0
        top = max(self.open, self.close)
        return clamp((self.high - top) / span, 0.0, 1.0)

    @property
    def lower_wick_ratio(self) -> float:
        span = self.high - self.low
        if span <= 0:
            return 0.0
        bottom = min(self.open, self.close)
        return clamp((bottom - self.low) / span, 0.0, 1.0)


def parse_klines(rows: Sequence[Sequence[Any]]) -> List[Bar]:
    """Convert raw kline arrays to ``Bar`` objects, skipping malformed rows."""
    bars: List[Bar] = []
    for row in rows:
        if not row or len(row) < 11:
            continue
        try:
            bars.append(
                Bar(
                    open_time=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time=int(row[6]),
                    quote_volume=float(row[7]),
                    trades=int(row[8]),
                    taker_buy_base=float(row[9]),
                    taker_buy_quote=float(row[10]),
                )
            )
        except (TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b.open_time)
    return bars


@dataclass
class Baseline:
    """Robust "normal" statistics from the historical part of a kline window."""

    quote_volume_median: float = 0.0
    quote_volume_mean: float = 0.0
    quote_volume_stdev: float = 0.0
    trades_median: float = 0.0
    sigma_1m: float = 0.0          # stdev of 1m returns (fraction)
    range_pct_median: float = 0.0
    sample_size: int = 0
    taker_buy_ratio_mean: float = 0.5

    @property
    def is_usable(self) -> bool:
        return self.sample_size >= 5 and self.quote_volume_median > 0

    def sigma_for_horizon(self, minutes: float) -> float:
        """Scale 1m volatility to an N-minute horizon (sqrt-time rule)."""
        if self.sigma_1m <= 0:
            return 0.0
        return self.sigma_1m * (max(minutes, 0.0) ** 0.5)


def compute_baseline(bars: Sequence[Bar], *, exclude_last: bool = True) -> Baseline:
    """Baseline stats over historical bars.

    ``exclude_last`` drops the newest bar because Binance's final kline is the
    *in-progress* minute: its volume is a partial sample that would otherwise
    poison the very comparison we are about to make.
    """
    hist = list(bars[:-1] if (exclude_last and len(bars) > 1) else bars)
    if not hist:
        return Baseline()

    volumes = [b.quote_volume for b in hist if b.quote_volume > 0]
    trades = [float(b.trades) for b in hist]
    returns = [
        r for r in (
            pct_change(hist[i].close, hist[i - 1].close) for i in range(1, len(hist))
        ) if r is not None
    ]
    ranges = [b.range_pct for b in hist if b.range_pct > 0]
    takers = [b.taker_buy_ratio for b in hist]

    return Baseline(
        quote_volume_median=median(volumes),
        quote_volume_mean=mean(volumes),
        quote_volume_stdev=stdev(volumes),
        trades_median=median(trades),
        sigma_1m=stdev(returns),
        range_pct_median=median(ranges),
        sample_size=len(hist),
        taker_buy_ratio_mean=mean(takers) if takers else 0.5,
    )


@dataclass
class ConfirmSignals:
    """Everything we learn from a fresh kline fetch, used to accept/reject."""

    symbol: str
    price: float
    bars: List[Bar] = field(default_factory=list)
    baseline: Baseline = field(default_factory=Baseline)

    # current (possibly partial) minute
    current: Optional[Bar] = None
    minute_fraction: float = 0.0
    volume_so_far: float = 0.0
    volume_projected: float = 0.0
    vol_mult: float = 0.0
    vol_mult_prev: float = 0.0
    vol_z: float = 0.0
    trades_mult: float = 0.0
    taker_buy_ratio: float = 0.5
    close_position: float = 0.5
    upper_wick_ratio: float = 0.0
    lower_wick_ratio: float = 0.0

    # multi-bar horizon returns
    ret_5m: Optional[float] = None
    ret_15m: Optional[float] = None
    ret_30m: Optional[float] = None
    ret_60m: Optional[float] = None

    # absolute context
    quote_volume_24h: float = 0.0
    recent_quote_volume: float = 0.0
    range_pct: float = 0.0
    sigma_1m: float = 0.0
    bar_high: float = 0.0
    bar_low: float = 0.0

    @property
    def usable(self) -> bool:
        return self.current is not None and self.baseline.is_usable

    def to_dict(self) -> Dict[str, float]:
        return {
            "price": self.price,
            "vol_mult": self.vol_mult,
            "vol_mult_prev": self.vol_mult_prev,
            "vol_z": self.vol_z,
            "trades_mult": self.trades_mult,
            "taker_buy_ratio": self.taker_buy_ratio,
            "close_position": self.close_position,
            "upper_wick_ratio": self.upper_wick_ratio,
            "lower_wick_ratio": self.lower_wick_ratio,
            "ret_5m": self.ret_5m,
            "ret_15m": self.ret_15m,
            "ret_30m": self.ret_30m,
            "ret_60m": self.ret_60m,
            "volume_so_far": self.volume_so_far,
            "volume_projected": self.volume_projected,
            "recent_quote_volume": self.recent_quote_volume,
            "minute_fraction": self.minute_fraction,
            "sigma_1m": self.sigma_1m,
            "range_pct": self.range_pct,
        }


def build_confirm_signals(
    symbol: str,
    bars: Sequence[Bar],
    *,
    now_ms_value: Optional[int] = None,
) -> ConfirmSignals:
    """Turn a kline window into confirm/reject signals.

    Volume is measured two ways and both are reported:

    * ``vol_mult`` -- projected full-minute volume over the baseline *median*
      minute. This is the number that fires alerts; it is a projection because
      the newest bar is still forming.
    * ``vol_mult_prev`` -- the last *completed* minute over baseline, which is
      slower but exact. Alerts carry it for context.
    """
    signals = ConfirmSignals(symbol=symbol, bars=list(bars))
    if not bars:
        return signals

    now = now_ms_value if now_ms_value is not None else int(time.time() * 1000)
    current = bars[-1]
    signals.current = current
    signals.price = current.close
    signals.bar_high = current.high
    signals.bar_low = current.low
    signals.range_pct = current.range_pct
    signals.baseline = compute_baseline(bars, exclude_last=True)

    base = signals.baseline
    signals.sigma_1m = base.sigma_1m

    # How far into the current minute are we? Binance's kline close time is the
    # last millisecond of the interval, so elapsed = 60000 - (close_time - now).
    span = max(current.close_time - current.open_time, 1)
    elapsed = clamp(now - current.open_time, 0, span)
    fraction = clamp(elapsed / span, 0.0, 1.0)
    signals.minute_fraction = fraction

    signals.volume_so_far = current.quote_volume
    # Project to a full minute, but never divide by a tiny fraction: a handful
    # of seconds of trades can extrapolate absurdly. 20% (12s) is the floor.
    signals.volume_projected = safe_div(current.quote_volume, max(fraction, 0.2))
    signals.taker_buy_ratio = current.taker_buy_ratio
    signals.close_position = current.close_position
    signals.upper_wick_ratio = current.upper_wick_ratio
    signals.lower_wick_ratio = current.lower_wick_ratio

    if base.is_usable:
        signals.vol_mult = safe_div(signals.volume_projected, base.quote_volume_median)
        signals.trades_mult = safe_div(
            safe_div(current.trades, max(fraction, 0.2)), base.trades_median
        )
        if base.quote_volume_stdev > 0:
            signals.vol_z = safe_div(
                signals.volume_projected - base.quote_volume_mean,
                base.quote_volume_stdev,
            )
        elif base.quote_volume_mean > 0:
            signals.vol_z = signals.vol_mult

        if len(bars) >= 2:
            prev = bars[-2]
            signals.vol_mult_prev = safe_div(prev.quote_volume, base.quote_volume_median)

    # Multi-bar horizon returns, anchored on the current bar's close.
    def ret_over(minutes: int) -> Optional[float]:
        """Return over the last N closed minutes, if the window is covered."""
        span_bars = max(1, minutes)
        if len(bars) < span_bars + 1:
            return None
        anchor = bars[-(span_bars + 1)]
        return pct_change(current.close, anchor.close)

    signals.ret_5m = ret_over(5)
    signals.ret_15m = ret_over(15)
    signals.ret_30m = ret_over(30)
    signals.ret_60m = ret_over(60)

    lookback = bars[-6:-1] if len(bars) > 6 else bars[:-1]
    signals.recent_quote_volume = sum(b.quote_volume for b in lookback) if lookback else 0.0

    # ``quote_volume_24h`` is deliberately left for the caller to fill from the
    # 24h ticker: a 60-bar window can only ever account for one hour.
    return signals
