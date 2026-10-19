"""The Turtle trading system: rules, signals, position sizing, risk limits.

Adapted from the original Turtle rules (Dennis/Eckhardt):

===========================  ==========================================
original rule                implementation here
===========================  ==========================================
N = 20-day ATR               :func:`~pumpdump.indicators.atr`, Wilder-smoothed
System 1 entry: 20-day       ``entry_period`` breakout of the prior
breakout                     N-bar high (level excludes the current bar)
System 2 entry: 55-day       ``entry_period_slow`` breakout
System 1 exit: 10-day low    ``exit_period`` opposite channel
System 2 exit: 20-day low    ``exit_period_slow`` opposite channel
Stop = 2N                    ``stop_atr_multiple`` below/above entry
1 Unit = 1% equity / N       :func:`position_plan`
Pyramid every 1/2 N          ``pyramid_step_atr``, up to 4 units
4 units per market           ``max_units_per_market``
6 units per correlated group :class:`RiskBudget` + correlation clustering
10 units per direction       ``max_units_direction``
12 units total               ``max_units_total``
===========================  ==========================================

Two deliberate departures from the 1980s original:

1. **An ADX trend filter.** The Turtles did not use ADX, but a raw breakout
   system is a whipsaw machine in a range. ``adx_min`` is a tunable gate, and
   leaving it at 0 restores the pure original behaviour.
2. **A position notional cap.** The original sized purely from N, which on a
   low-volatility crypto pair can demand more notional than a spot account can
   fill. ``max_notional_pct`` caps that and reports when it binds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .common import clamp, pct_change, safe_div
from .indicators import (
    adx,
    atr,
    atr_percent,
    correlation,
    donchian,
    percentile_rank,
    returns,
    rsi,
)
from .klines import Bar


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------

@dataclass
class TurtleParams:
    """Every number a Turtle trade depends on."""

    entry_period: int = 20
    entry_period_slow: int = 55
    exit_period: int = 10
    exit_period_slow: int = 20
    atr_period: int = 20

    stop_atr_multiple: float = 2.0
    pyramid_step_atr: float = 0.5
    pyramid_max_units: int = 4

    max_units_per_market: int = 4
    max_units_correlated: int = 6
    max_units_direction: int = 10
    max_units_total: int = 12

    risk_per_unit: float = 0.01        # 1% of equity at 1N adverse move
    max_notional_pct: float = 0.25     # cap a single position at 25% of equity

    adx_period: int = 14
    adx_min: float = 20.0              # 0 disables the filter (pure original)

    #: Reject breakouts already this many N beyond the level (chasing).
    max_extension_atr: float = 2.0

    @property
    def minimum_bars(self) -> int:
        """Bars needed before the slowest indicator is meaningful."""
        return max(
            self.entry_period_slow + 5,
            self.atr_period + 5,
            self.adx_period * 3,
        )


# --------------------------------------------------------------------------
# Snapshot: one symbol, one timeframe, all Turtle quantities
# --------------------------------------------------------------------------

@dataclass
class TurtleSnapshot:
    symbol: str
    timeframe: str
    close: float
    bar_time_ms: int
    bars_available: int

    atr: Optional[float] = None                 # N
    atr_pct: Optional[float] = None
    atr_rank: float = 0.5                       # percentile within its own history

    upper_20: Optional[float] = None
    lower_20: Optional[float] = None
    upper_55: Optional[float] = None
    lower_55: Optional[float] = None
    exit_low_10: Optional[float] = None
    exit_low_20: Optional[float] = None
    exit_high_10: Optional[float] = None
    exit_high_20: Optional[float] = None

    breakout_20_up: bool = False
    breakout_55_up: bool = False
    breakout_20_down: bool = False
    breakout_55_down: bool = False
    exit_long: bool = False
    exit_short: bool = False

    adx: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    rsi: Optional[float] = None

    #: How far beyond the breakout level, measured in N (negative = inside).
    extension_up_atr: Optional[float] = None
    extension_down_atr: Optional[float] = None
    #: Bars since price first closed beyond the 20-bar level.
    bars_since_breakout: Optional[int] = None

    quote_volume_24h: float = 0.0

    @property
    def direction(self) -> int:
        """+1 long signal, -1 short signal, 0 none."""
        if self.breakout_55_up or self.breakout_20_up:
            return 1
        if self.breakout_55_down or self.breakout_20_down:
            return -1
        return 0

    @property
    def is_system2(self) -> bool:
        return self.breakout_55_up or self.breakout_55_down

    @property
    def trend_ok(self) -> bool:
        if self.adx is None:
            return False
        return self.adx >= 0.0

    def to_row(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "close": self.close,
            "atr": self.atr,
            "atr_pct": self.atr_pct,
            "atr_rank": round(self.atr_rank, 4),
            "upper_20": self.upper_20,
            "upper_55": self.upper_55,
            "lower_20": self.lower_20,
            "lower_55": self.lower_55,
            "breakout_20_up": self.breakout_20_up,
            "breakout_55_up": self.breakout_55_up,
            "breakout_20_down": self.breakout_20_down,
            "breakout_55_down": self.breakout_55_down,
            "exit_long": self.exit_long,
            "exit_short": self.exit_short,
            "adx": round(self.adx, 2) if self.adx is not None else None,
            "plus_di": round(self.plus_di, 2) if self.plus_di is not None else None,
            "minus_di": round(self.minus_di, 2) if self.minus_di is not None else None,
            "rsi": round(self.rsi, 2) if self.rsi is not None else None,
            "extension_up_atr": (
                round(self.extension_up_atr, 3)
                if self.extension_up_atr is not None
                else None
            ),
            "bars_since_breakout": self.bars_since_breakout,
            "quote_volume_24h": round(self.quote_volume_24h, 2),
        }


# --------------------------------------------------------------------------
# Computation
# --------------------------------------------------------------------------

def compute_snapshot(
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    params: TurtleParams,
    *,
    quote_volume_24h: float = 0.0,
) -> Optional[TurtleSnapshot]:
    """Compute every Turtle quantity for the final bar of ``bars``."""
    if len(bars) < 3:
        return None

    last = bars[-1]
    snapshot = TurtleSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        close=last.close,
        bar_time_ms=last.open_time,
        bars_available=len(bars),
        quote_volume_24h=quote_volume_24h,
    )

    atr_values = atr(bars, params.atr_period)
    atr_pct_values = atr_percent(bars, params.atr_period)
    snapshot.atr = atr_values[-1] if atr_values else None
    snapshot.atr_pct = _clean(atr_pct_values[-1] if atr_pct_values else None)

    clean_atr_pct = [v for v in atr_pct_values if v is not None]
    if clean_atr_pct and snapshot.atr_pct is not None:
        snapshot.atr_rank = percentile_rank(clean_atr_pct, snapshot.atr_pct)

    fast = donchian(bars, params.entry_period)
    slow = donchian(bars, params.entry_period_slow)
    exit_fast = donchian(bars, params.exit_period)
    exit_slow = donchian(bars, params.exit_period_slow)

    snapshot.upper_20 = _clean(fast.upper[-1])
    snapshot.lower_20 = _clean(fast.lower[-1])
    snapshot.upper_55 = _clean(slow.upper[-1])
    snapshot.lower_55 = _clean(slow.lower[-1])
    snapshot.exit_low_10 = _clean(exit_fast.lower[-1])
    snapshot.exit_high_10 = _clean(exit_fast.upper[-1])
    snapshot.exit_low_20 = _clean(exit_slow.lower[-1])
    snapshot.exit_high_20 = _clean(exit_slow.upper[-1])

    # Breakouts compare the close against the *prior* N-bar extreme.
    level20_up = _clean(fast.breakout_upper[-1])
    level20_down = _clean(fast.breakout_lower[-1])
    level55_up = _clean(slow.breakout_upper[-1])
    level55_down = _clean(slow.breakout_lower[-1])

    snapshot.breakout_20_up = level20_up is not None and last.close > level20_up
    snapshot.breakout_20_down = level20_down is not None and last.close < level20_down
    snapshot.breakout_55_up = level55_up is not None and last.close > level55_up
    snapshot.breakout_55_down = level55_down is not None and last.close < level55_down

    snapshot.exit_long = (
        snapshot.exit_low_10 is not None and last.close < snapshot.exit_low_10
    )
    snapshot.exit_short = (
        snapshot.exit_high_10 is not None and last.close > snapshot.exit_high_10
    )

    adx_result = adx(bars, params.adx_period)
    snapshot.adx = _clean(adx_result.adx[-1])
    snapshot.plus_di = _clean(adx_result.plus_di[-1])
    snapshot.minus_di = _clean(adx_result.minus_di[-1])

    rsi_values = rsi(bars)
    snapshot.rsi = _clean(rsi_values[-1] if rsi_values else None)

    if snapshot.atr and snapshot.atr > 0:
        if level20_up is not None:
            snapshot.extension_up_atr = (last.close - level20_up) / snapshot.atr
        if level20_down is not None:
            snapshot.extension_down_atr = (level20_down - last.close) / snapshot.atr

    snapshot.bars_since_breakout = _bars_since_breakout(
        bars, params.entry_period, direction=snapshot.direction
    )
    return snapshot


def _bars_since_breakout(
    bars: Sequence[Bar], period: int, *, direction: int
) -> Optional[int]:
    """How many bars ago price last closed beyond the prior ``period`` extreme.

    0 means "this bar". ``None`` means no breakout within the lookback.
    """
    if direction == 0:
        return None
    channel = donchian(bars, period)
    lookback = min(len(bars), period * 3)
    for offset in range(lookback):
        index = len(bars) - 1 - offset
        if index < 0:
            break
        bar = bars[index]
        if direction > 0:
            level = _clean(channel.breakout_upper[index])
            if level is not None and bar.close > level:
                continue
            return offset if offset > 0 else 0
        level = _clean(channel.breakout_lower[index])
        if level is not None and bar.close < level:
            continue
        return offset if offset > 0 else 0
    return None


def _clean(value) -> Optional[float]:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


# --------------------------------------------------------------------------
# Setup quality scoring
# --------------------------------------------------------------------------

@dataclass
class SetupScore:
    score: float
    components: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    rejected: Optional[str] = None


def score_setup(
    snapshot: TurtleSnapshot,
    params: TurtleParams,
    *,
    align_timeframes: Sequence[TurtleSnapshot] = (),
) -> SetupScore:
    """Rank a Turtle setup 0-100, or explain why it is not tradeable.

    Components: breakout extension, trend strength, multi-timeframe alignment,
    volatility quality, liquidity. A chase penalty applies past
    ``max_extension_atr``.
    """
    reasons: List[str] = []
    components: Dict[str, float] = {}

    direction = snapshot.direction
    if direction == 0:
        return SetupScore(0.0, rejected="no breakout")
    if snapshot.atr is None or snapshot.atr <= 0:
        return SetupScore(0.0, rejected="no ATR (N)")
    if snapshot.bars_available < params.minimum_bars:
        return SetupScore(
            0.0,
            rejected=f"only {snapshot.bars_available} bars, need {params.minimum_bars}",
        )

    # Trend filter.
    if params.adx_min > 0:
        if snapshot.adx is None:
            return SetupScore(0.0, rejected="ADX undefined")
        if snapshot.adx < params.adx_min:
            return SetupScore(
                0.0, rejected=f"ADX {snapshot.adx:.1f} < {params.adx_min:.1f} (range, not trend)"
            )

    extension = (
        snapshot.extension_up_atr if direction > 0 else snapshot.extension_down_atr
    )
    if extension is None:
        return SetupScore(0.0, rejected="breakout level undefined")
    if extension > params.max_extension_atr:
        return SetupScore(
            0.0,
            rejected=(
                f"already {extension:.2f}N beyond the level "
                f"(> {params.max_extension_atr:.2f}N): chasing"
            ),
        )

    # --- breakout quality (max 30) --------------------------------------
    # Rewards clearing the level decisively but not having run away.
    if extension <= 0:
        breakout_points = 5.0
    else:
        breakout_points = 30.0 * clamp(extension / 0.75, 0.0, 1.0)
    system_bonus = 8.0 if snapshot.is_system2 else 0.0
    components["breakout"] = round(clamp(breakout_points + system_bonus, 0.0, 30.0), 2)
    reasons.append(
        f"{'System 2 (55-bar)' if snapshot.is_system2 else 'System 1 (20-bar)'} breakout, "
        f"{extension:.2f}N beyond the level"
    )

    # --- trend strength (max 25) ----------------------------------------
    adx_points = 0.0
    if snapshot.adx is not None:
        adx_points = 25.0 * clamp((snapshot.adx - params.adx_min) / 25.0, 0.0, 1.0)
    di_aligned = 0.0
    if snapshot.plus_di is not None and snapshot.minus_di is not None:
        spread = snapshot.plus_di - snapshot.minus_di
        aligned = spread if direction > 0 else -spread
        di_aligned = 25.0 * clamp(aligned / 30.0, 0.0, 1.0)
    components["trend"] = round(clamp(max(adx_points, di_aligned), 0.0, 25.0), 2)
    if snapshot.adx is not None:
        reasons.append(
            f"ADX {snapshot.adx:.0f}, "
            f"+DI {snapshot.plus_di:.0f} / -DI {snapshot.minus_di:.0f}"
            if snapshot.plus_di is not None and snapshot.minus_di is not None
            else f"ADX {snapshot.adx:.0f}"
        )

    # --- multi-timeframe alignment (max 20) -----------------------------
    aligned = 0
    total = 0
    for other in align_timeframes:
        if other.timeframe == snapshot.timeframe:
            continue
        total += 1
        other_direction = other.direction
        if other_direction == direction:
            aligned += 1
        elif other_direction == -direction:
            aligned -= 1
    if total:
        ratio = clamp(aligned / total, -1.0, 1.0)
        components["alignment"] = round(clamp((ratio + 1.0) / 2.0, 0.0, 1.0) * 20.0, 2)
        reasons.append(
            f"{aligned}/{total} other timeframes agree"
            if aligned >= 0
            else f"{aligned}/{total} other timeframes agree (conflicting)"
        )
    else:
        components["alignment"] = 10.0  # neutral when only one timeframe is available

    # --- volatility quality (max 15) -------------------------------------
    # Prefer the middle of a symbol's own ATR% range: a breakout on
    # historically dead volatility is usually noise, and one on a volatility
    # spike is usually a blow-off.
    rank = snapshot.atr_rank
    volatility_points = 15.0 * (1.0 - abs(rank - 0.6) / 0.6)
    components["volatility"] = round(clamp(volatility_points, 0.0, 15.0), 2)
    if snapshot.atr_pct is not None:
        reasons.append(
            f"ATR {snapshot.atr_pct * 100:.2f}% of price "
            f"(rank {rank * 100:.0f}th pct)"
        )

    # --- liquidity (max 10) ---------------------------------------------
    liquidity_points = 10.0 * clamp(
        safe_div(math.log10(max(snapshot.quote_volume_24h, 1.0)) - 5.0, 2.0),
        0.0,
        1.0,
    )
    components["liquidity"] = round(liquidity_points, 2)
    reasons.append(f"24h volume ${snapshot.quote_volume_24h / 1e6:.1f}M")

    if snapshot.bars_since_breakout is not None:
        reasons.append(f"breakout is {snapshot.bars_since_breakout} bar(s) old")
    if snapshot.rsi is not None:
        reasons.append(f"RSI {snapshot.rsi:.0f}")

    score = clamp(sum(components.values()), 0.0, 100.0)
    return SetupScore(score, components, reasons)


# --------------------------------------------------------------------------
# Position sizing
# --------------------------------------------------------------------------

@dataclass
class PositionPlan:
    symbol: str
    timeframe: str
    direction: int
    entry: float
    stop: float
    atr: float
    unit_size: float           # base-asset quantity per unit
    unit_notional: float       # quote value of one unit
    units: int = 1
    total_notional: float = 0.0
    risk_per_unit: float = 0.0
    total_risk: float = 0.0
    notional_capped: bool = False
    pyramid_levels: List[float] = field(default_factory=list)
    exit_level: Optional[float] = None

    def describe(self) -> str:
        return (
            f"{self.units} unit(s) x {self.unit_size:,.6g} = "
            f"${self.total_notional:,.0f} notional, stop {self.stop:,.6g} "
            f"({abs(self.stop - self.entry) / self.entry * 100:.2f}%), "
            f"risk ${self.total_risk:,.0f}"
        )


def position_plan(
    snapshot: TurtleSnapshot,
    params: TurtleParams,
    *,
    equity: float,
    units: int = 1,
    entry: Optional[float] = None,
    exit_level: Optional[float] = None,
) -> Optional[PositionPlan]:
    """Size a Turtle position: ``Unit = (risk% x equity) / N``.

    The defining property of Turtle sizing is that a 1N adverse move costs the
    same dollar amount regardless of which market you are in -- that is what
    makes positions across very different instruments comparable.
    """
    if snapshot.atr is None or snapshot.atr <= 0 or equity <= 0:
        return None
    direction = snapshot.direction
    if direction == 0:
        return None

    entry_price = entry if entry is not None else snapshot.close
    if entry_price <= 0:
        return None

    # One unit moves `atr` dollars per base unit, so sizing to a fixed dollar
    # risk per unit divides the dollar risk by N.
    dollar_risk_per_unit = equity * params.risk_per_unit
    unit_size = dollar_risk_per_unit / snapshot.atr
    unit_notional = unit_size * entry_price

    cap = equity * params.max_notional_pct
    capped = False
    if cap > 0 and unit_notional > cap:
        unit_size = cap / entry_price
        unit_notional = cap
        capped = True

    stop = (
        entry_price - params.stop_atr_multiple * snapshot.atr
        if direction > 0
        else entry_price + params.stop_atr_multiple * snapshot.atr
    )
    if stop <= 0:
        stop = entry_price * 0.5

    # Pyramid ladder: add a unit every 1/2 N in favour.
    ladder: List[float] = []
    for step in range(1, max(units, params.pyramid_max_units)):
        offset = step * params.pyramid_step_atr * snapshot.atr
        ladder.append(entry_price + offset if direction > 0 else entry_price - offset)

    return PositionPlan(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        direction=direction,
        entry=entry_price,
        stop=stop,
        atr=snapshot.atr,
        unit_size=unit_size,
        unit_notional=unit_notional,
        units=units,
        total_notional=unit_notional * units,
        risk_per_unit=unit_size * snapshot.atr,
        total_risk=unit_size * snapshot.atr * units,
        notional_capped=capped,
        pyramid_levels=ladder[: max(0, params.pyramid_max_units - 1)],
        exit_level=exit_level,
    )


# --------------------------------------------------------------------------
# Portfolio risk limits
# --------------------------------------------------------------------------

@dataclass
class Allocation:
    symbol: str
    group: str
    direction: int
    units: int
    notional: float
    risk: float


class RiskBudget:
    """Enforces the Turtle portfolio limits as candidates are accepted.

    Tracks units per market, per correlated group, per direction, and in total.
    Candidates should be offered in descending score order so the limit binds
    on the weakest setup, not the first one seen.
    """

    def __init__(self, params: TurtleParams) -> None:
        self.params = params
        self.allocations: List[Allocation] = []
        self.rejections: List[Tuple[str, str]] = []

    @property
    def total_units(self) -> int:
        return sum(a.units for a in self.allocations)

    def units_in_group(self, group: str) -> int:
        return sum(a.units for a in self.allocations if a.group == group)

    def units_in_direction(self, direction: int) -> int:
        return sum(a.units for a in self.allocations if a.direction == direction)

    def units_for_symbol(self, symbol: str) -> int:
        return sum(a.units for a in self.allocations if a.symbol == symbol)

    def check(self, symbol: str, group: str, direction: int, units: int) -> Optional[str]:
        """Returns a rejection reason, or ``None`` if the trade fits."""
        params = self.params
        if self.units_for_symbol(symbol) + units > params.max_units_per_market:
            return (
                f"market limit: {self.units_for_symbol(symbol)}/{params.max_units_per_market} "
                f"units already in {symbol}"
            )
        if self.units_in_group(group) + units > params.max_units_correlated:
            return (
                f"correlated limit: group '{group}' at "
                f"{self.units_in_group(group)}/{params.max_units_correlated} units"
            )
        if self.units_in_direction(direction) + units > params.max_units_direction:
            return (
                f"direction limit: {self.units_in_direction(direction)}/"
                f"{params.max_units_direction} units already "
                f"{'long' if direction > 0 else 'short'}"
            )
        if self.total_units + units > params.max_units_total:
            return f"total limit: {self.total_units}/{params.max_units_total} units"
        return None

    def accept(
        self, symbol: str, group: str, direction: int, units: int,
        notional: float, risk: float,
    ) -> None:
        self.allocations.append(
            Allocation(symbol, group, direction, units, notional, risk)
        )

    def reject(self, symbol: str, reason: str) -> None:
        self.rejections.append((symbol, reason))

    def summary(self) -> Dict[str, object]:
        return {
            "total_units": self.total_units,
            "long_units": self.units_in_direction(1),
            "short_units": self.units_in_direction(-1),
            "total_notional": sum(a.notional for a in self.allocations),
            "total_risk": sum(a.risk for a in self.allocations),
            "positions": len(self.allocations),
            "rejected": len(self.rejections),
        }


# --------------------------------------------------------------------------
# Correlation grouping
# --------------------------------------------------------------------------

def cluster_by_correlation(
    returns_by_symbol: Dict[str, List[float]],
    *,
    threshold: float = 0.7,
    order: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    """Greedy correlation clustering for the "6 units per group" rule.

    The Turtles grouped markets by hand (currencies, grains, metals...). Crypto
    correlation is too unstable for a static map, so groups are derived from
    realised return correlation instead. Symbols are visited in ``order``
    (typically descending liquidity) and join the first group whose
    representative they correlate with above ``threshold``.
    """
    symbols = [s for s in (order or sorted(returns_by_symbol)) if s in returns_by_symbol]
    groups: Dict[str, str] = {}
    representatives: List[Tuple[str, List[float]]] = []

    for symbol in symbols:
        series = returns_by_symbol[symbol]
        if len(series) < 5:
            groups[symbol] = "unclustered"
            continue
        placed = False
        for group_name, rep_series in representatives:
            if correlation(series, rep_series) >= threshold:
                groups[symbol] = group_name
                placed = True
                break
        if not placed:
            group_name = f"g{len(representatives) + 1}"
            representatives.append((group_name, series))
            groups[symbol] = group_name
    return groups
