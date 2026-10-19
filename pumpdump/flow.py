"""Sub-minute flow engine: the heart of onset detection.

The detector's question is *"has volume suddenly started flowing right now?"*,
not *"has this already moved?"*. Binance's ``@ticker`` stream gives us a
per-second snapshot whose 24h counters are cumulative, so differencing them
yields exact per-second traded volume:

    volume traded during second t = q[t] - q[t-1]

Summing that over a sliding window gives the flow measurement the whole
design rests on. Everything here is built to make that number trustworthy:

* **Coverage tracking.** If messages are dropped we know how much of the
  window is actually covered and refuse to score a half-observed burst.
* **Contamination-resistant baselines.** Baseline samples are capped at a
  multiple of the running median, so a sustained pump cannot inflate the very
  baseline it is being compared against.
* **A liquidity floor on the baseline.** ``max(median, 24h_volume/8640)``
  stops a dead-but-listed pair from producing absurd multiples off a
  near-zero divisor.
* **Book-quality fields** (spread, top-of-book depth) that let the detector
  reject thin-book artefacts.
"""

from __future__ import annotations

import bisect
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from .common import (
    LOG,
    clamp,
    median,
    pct_change,
    safe_div,
    stdev,
)

#: Seconds in a day, used to convert a 24h volume into a per-10s rate.
SECONDS_PER_DAY = 86_400
TEN_SECONDS = 10


@dataclass
class Tick:
    """One ``@ticker`` snapshot, reduced to what the detector needs."""

    ts: int                 # milliseconds
    price: float
    quote_vol: float        # cumulative 24h quote volume
    base_vol: float         # cumulative 24h base volume
    trades: int             # cumulative 24h trade count
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0

    # differenced against the previous tick by SymbolFlow.update()
    d_quote: float = 0.0
    d_base: float = 0.0
    d_trades: int = 0
    dt_ms: int = 0
    seeded: bool = False    # True when loaded from klines, not a live delta


class FlowBaseline:
    """Robust rolling distribution of per-window flow, cheap to query.

    Samples arrive every ``window`` seconds. Keeping them in a sorted list
    makes the median/p90 exact while inserts stay O(n) on a list of ~180.
    Mean and stdev are maintained incrementally.
    """

    def __init__(self, capacity: int = 180, cap_multiple: float = 6.0) -> None:
        self.capacity = capacity
        self.cap_multiple = cap_multiple
        self._order: Deque[float] = deque()
        self._sorted: List[float] = []
        self._sum = 0.0
        self._sumsq = 0.0
        self.floor = 0.0          # absolute floor on the reference value

    # ------------------------------------------------------------- mutation

    def add(self, sample: float) -> float:
        """Add a sample (capped against contamination) and return what was stored."""
        if sample < 0 or not math.isfinite(sample):
            return 0.0
        cap = max(self.reference * self.cap_multiple, self.floor)
        stored = min(sample, cap) if cap > 0 else sample
        self._order.append(stored)
        bisect.insort(self._sorted, stored)
        self._sum += stored
        self._sumsq += stored * stored
        while len(self._order) > self.capacity:
            old = self._order.popleft()
            idx = bisect.bisect_left(self._sorted, old)
            if idx < len(self._sorted):
                self._sorted.pop(idx)
            self._sum -= old
            self._sumsq -= old * old
        return stored

    def seed(self, samples: Sequence[float]) -> None:
        for sample in samples:
            self.add(sample)

    # ------------------------------------------------------------ accessors

    @property
    def count(self) -> int:
        return len(self._order)

    @property
    def ready(self) -> bool:
        return self.count >= 8

    @property
    def mean(self) -> float:
        return self._sum / self.count if self.count else 0.0

    @property
    def stdev(self) -> float:
        if self.count < 2:
            return 0.0
        variance = max(0.0, self._sumsq / self.count - self.mean ** 2)
        return math.sqrt(variance)

    @property
    def med(self) -> float:
        return median(self._sorted) if self._sorted else 0.0

    def quantile(self, q: float) -> float:
        if not self._sorted:
            return 0.0
        idx = min(len(self._sorted) - 1, max(0, int(q * (len(self._sorted) - 1))))
        return self._sorted[idx]

    @property
    def p90(self) -> float:
        return self.quantile(0.9)

    @property
    def p25(self) -> float:
        return self.quantile(0.25)

    @property
    def reference(self) -> float:
        """The divisor a burst is measured against: robust, and floored.

        Deliberately the **lower quartile**, not the median. Bursts live in the
        upper tail, so anchoring on the median creates a feedback loop: the cap
        in :meth:`add` is a multiple of the reference, stored samples push the
        median up toward that cap, and the next cap is a multiple of the new
        median -- the baseline ratchets upward by ``cap_multiple`` every round
        and the symbol goes blind. The lower quartile is unmoved by anything in
        the top half of the distribution, which breaks that loop.
        """
        return max(self.p25, self.floor)

    def multiple(self, value: float) -> float:
        return safe_div(value, self.reference)

    def zscore(self, value: float) -> float:
        spread = self.stdev
        if spread <= 0:
            # Degenerate spread: fall back to a multiple-based pseudo-z so a
            # symbol with a perfectly flat history is not auto-rejected.
            return self.multiple(value) if self.reference > 0 else 0.0
        return (value - self.mean) / spread


@dataclass
class SymbolFlow:
    """Per-symbol rolling state for sub-minute flow analysis."""

    symbol: str
    market: str = "spot"
    max_seconds: int = 420
    baseline_window: int = TEN_SECONDS

    ticks: Deque[Tick] = field(default_factory=deque)
    baseline: FlowBaseline = field(default_factory=FlowBaseline)
    # trailing 10s flows, used for acceleration comparisons
    recent_flows: Deque[Tuple[int, float]] = field(default_factory=deque)

    last_tick: Optional[Tick] = None
    last_price: float = 0.0
    quote_volume_24h: float = 0.0
    trades_24h: int = 0
    price_change_pct_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0

    sigma_1s: float = 0.0
    sigma_samples: int = 0

    #: Fraction of the 24h average 10s flow used as the baseline floor.
    #: Deliberately below 1.0: the floor is a sanity guard against a
    #: near-zero reference, not the reference itself. At 1.0 the 24h average
    #: dominates the symbol's own observed quiet baseline and makes liquid
    #: pairs effectively unalertable.
    floor_fraction: float = 0.25

    _bucket_start: int = 0
    _bucket_flow: float = 0.0
    _bucket_covered_ms: int = 0
    _baseline_dirty_since: int = 0

    # ------------------------------------------------------------- ingest

    def update(self, tick: Tick) -> None:
        """Apply a live tick, differencing the cumulative counters."""
        previous = self.last_tick
        if previous is not None:
            dt = tick.ts - previous.ts
            if dt <= 0:
                # Out-of-order or duplicate second: keep the newer price only.
                self.last_price = tick.price
                self.last_tick = tick
                return
            tick.dt_ms = dt
            tick.d_quote = max(0.0, tick.quote_vol - previous.quote_vol)
            tick.d_base = max(0.0, tick.base_vol - previous.base_vol)
            tick.d_trades = max(0, tick.trades - previous.trades)
            self._accumulate_bucket(tick)
        else:
            tick.dt_ms = 0
            tick.d_quote = 0.0
            tick.d_base = 0.0
            tick.d_trades = 0

        self.ticks.append(tick)
        while len(self.ticks) > 1 and tick.ts - self.ticks[0].ts > self.max_seconds * 1000:
            self.ticks.popleft()

        price_move = None
        if previous is not None and previous.price > 0:
            price_move = pct_change(tick.price, previous.price)
        if price_move is not None:
            self.sigma_samples += 1
            n = self.sigma_samples
            # Running mean of |1s return| scaled to a stdev-like figure.
            approx = abs(price_move) * 1.2533
            self.sigma_1s = approx if n == 1 else (self.sigma_1s * (n - 1) + approx) / n

        self.last_tick = tick
        self.last_price = tick.price
        self.quote_volume_24h = tick.quote_vol
        self.trades_24h = tick.trades

    def _accumulate_bucket(self, tick: Tick) -> None:
        """Fold ticks into non-overlapping N-second baseline buckets."""
        bucket = tick.ts // (self.baseline_window * 1000)
        if self._bucket_start == 0:
            self._bucket_start = bucket
        if bucket != self._bucket_start:
            if self._bucket_covered_ms >= self.baseline_window * 900:
                flow = self._bucket_flow
                self.baseline.add(flow)
                self.recent_flows.append((self._bucket_start, flow))
                while len(self.recent_flows) > 60:
                    self.recent_flows.popleft()
            self._bucket_start = bucket
            self._bucket_flow = 0.0
            self._bucket_covered_ms = 0
        self._bucket_flow += tick.d_quote
        self._bucket_covered_ms += tick.dt_ms

    def seed_from_klines(self, bars: Sequence) -> None:
        """Warm the baseline and volatility from 1s klines (cold-start fix).

        ``bars`` are :class:`~pumpdump.klines.Bar` objects at 1s resolution.
        """
        if not bars:
            return
        flows: List[float] = []
        window_flow = 0.0
        covered = 0
        bucket = None
        returns: List[float] = []

        previous_close = 0.0
        for bar in bars:
            if previous_close > 0:
                ret = pct_change(bar.close, previous_close)
                if ret is not None:
                    returns.append(ret)
            previous_close = bar.close

            slot = bar.open_time // (self.baseline_window * 1000)
            if bucket is None:
                bucket = slot
            if slot != bucket:
                if covered >= self.baseline_window * 900:
                    flows.append(window_flow)
                window_flow = 0.0
                covered = 0
                bucket = slot
            window_flow += bar.quote_volume
            covered += 1000

        if covered >= self.baseline_window * 900:
            flows.append(window_flow)

        if flows:
            self.baseline.seed(flows)
        if returns:
            self.sigma_1s = stdev(returns)
            self.sigma_samples = len(returns)

    # ------------------------------------------------------------ measures

    def _window_ticks(self, seconds: float, now: int) -> Tuple[List[Tick], int]:
        """Ticks inside the window **ending at** ``now``, plus the covered span.

        The window is bounded on both sides. The upper bound matters because
        callers do query windows that end in the past -- ``flow_acceleration``
        compares the current window against the one before it -- and without it
        the "previous" window would silently swallow every tick up to the
        present, making the two windows overlap and the ratio meaningless.
        """
        cutoff = now - int(seconds * 1000)
        selected: List[Tick] = []
        for tick in reversed(self.ticks):
            if tick.ts > now:
                continue
            if tick.ts < cutoff:
                break
            selected.append(tick)
        selected.reverse()
        covered = sum(t.dt_ms for t in selected)
        return selected, covered

    def flow_quote(self, seconds: float, now: Optional[int] = None) -> Tuple[float, float]:
        """``(quote volume, coverage 0-1)`` traded over the trailing window."""
        stamp = now if now is not None else int(time.time() * 1000)
        ticks, covered = self._window_ticks(seconds, stamp)
        total = sum(t.d_quote for t in ticks)
        target = int(seconds * 1000)
        return total, clamp(safe_div(covered, target), 0.0, 1.0)

    def flow_trades(self, seconds: float, now: Optional[int] = None) -> Tuple[int, float]:
        stamp = now if now is not None else int(time.time() * 1000)
        ticks, covered = self._window_ticks(seconds, stamp)
        total = sum(t.d_trades for t in ticks)
        target = int(seconds * 1000)
        return total, clamp(safe_div(covered, target), 0.0, 1.0)

    def change_over(self, seconds: float, now: Optional[int] = None) -> Optional[float]:
        stamp = now if now is not None else int(time.time() * 1000)
        cutoff = stamp - int(seconds * 1000)
        anchor: Optional[float] = None
        for tick in reversed(self.ticks):
            if tick.ts <= cutoff:
                anchor = tick.price
                break
        if anchor is None or anchor <= 0 or self.last_price <= 0:
            return None
        return pct_change(self.last_price, anchor)

    def range_position(self, seconds: float, now: Optional[int] = None) -> float:
        """Where the current price sits in the window's high-low range."""
        stamp = now if now is not None else int(time.time() * 1000)
        ticks, _ = self._window_ticks(seconds, stamp)
        if not ticks:
            return 0.5
        prices = [t.price for t in ticks] + [self.last_price]
        low, high = min(prices), max(prices)
        if high <= low:
            return 0.5
        return clamp((self.last_price - low) / (high - low), 0.0, 1.0)

    @property
    def spread_pct(self) -> float:
        tick = self.last_tick
        if tick is None or tick.bid <= 0 or tick.ask <= 0 or tick.ask < tick.bid:
            return 0.0
        mid = (tick.bid + tick.ask) / 2.0
        return safe_div(tick.ask - tick.bid, mid)

    @property
    def book_depth_usd(self) -> float:
        """Top-of-book notional available on both sides (the thinner side)."""
        tick = self.last_tick
        if tick is None:
            return 0.0
        bid_side = tick.bid * tick.bid_qty
        ask_side = tick.ask * tick.ask_qty
        if bid_side <= 0 and ask_side <= 0:
            return 0.0
        return min(bid_side, ask_side) if bid_side > 0 and ask_side > 0 else max(bid_side, ask_side)

    def flow_acceleration(self, now: Optional[int] = None) -> Optional[float]:
        """Current 10s flow divided by the previous 10s flow.

        A ratio above 1 means volume is still building -- the signature of an
        onset rather than the tail of a move.
        """
        stamp = now if now is not None else int(time.time() * 1000)
        current, coverage_now = self.flow_quote(TEN_SECONDS, stamp)
        previous, coverage_prev = self.flow_quote(TEN_SECONDS, stamp - TEN_SECONDS * 1000)
        if coverage_now < 0.5 or coverage_prev < 0.5:
            return None
        return safe_div(current, max(previous, 1e-9))

    @property
    def baseline_per_10s_from_24h(self) -> float:
        return self.quote_volume_24h / (SECONDS_PER_DAY / TEN_SECONDS)

    def apply_liquidity_floor(self) -> None:
        """Floor the baseline at a fraction of the 24h average 10s flow.

        Guards the divisor: without it a symbol whose observed baseline is ~0
        (sparse trades, or a 1s-kline history that is mostly empty bars)
        reports a routine $18K of flow as a "1000x burst".
        """
        self.baseline.floor = self.baseline_per_10s_from_24h * self.floor_fraction

    def sigma_window(self, seconds: float) -> float:
        """Volatility over an N-second horizon via the sqrt-time rule."""
        return self.sigma_1s * math.sqrt(max(seconds, 0.0))


@dataclass
class OnsetCandidate:
    """A symbol whose flow signature says "something is starting right now"."""

    symbol: str
    market: str
    direction: int              # +1 pump, -1 dump
    price: float
    flow_quote: float           # quote volume in the trailing window
    coverage: float
    burst_multiple: float
    burst_z: float
    change_10s: Optional[float]
    change_30s: Optional[float]
    change_60s: Optional[float]
    sigma_10s: float
    sigma_multiple: float
    acceleration: Optional[float]
    range_position: float
    spread_pct: float
    book_depth_usd: float
    quote_volume_24h: float
    trades_10s: int
    trades_multiple: float
    reasons: List[str] = field(default_factory=list)

    @property
    def move(self) -> float:
        return self.change_10s or 0.0

    def summary(self) -> str:
        bits = [
            f"{self.symbol}",
            f"{'+' if self.direction > 0 else '-'}{abs(self.move) * 100:.2f}%/10s",
            f"flow {self.flow_quote:,.0f}",
            f"{self.burst_multiple:.1f}x",
        ]
        return " ".join(bits)


class FlowEngine:
    """Owns every tracked symbol's flow state and runs the onset scan."""

    def __init__(
        self,
        *,
        market: str = "spot",
        window_sec: float = TEN_SECONDS,
        min_burst_multiple: float = 8.0,
        min_burst_z: float = 4.0,
        min_abs_flow: float = 5_000.0,
        min_move_pct_10s: float = 0.4,
        min_sigma_multiple: float = 3.0,
        min_coverage: float = 0.7,
        max_spread_pct: float = 0.004,
        min_book_depth_usd: float = 500.0,
        max_late_pct_60s: float = 6.0,
        require_acceleration: bool = True,
        max_24h_move_pct: float = 60.0,
        min_trades_10s: int = 8,
        baseline_floor_fraction: float = 0.25,
    ) -> None:
        self.market = market
        self.window_sec = window_sec
        self.min_burst_multiple = min_burst_multiple
        self.min_burst_z = min_burst_z
        self.min_abs_flow = min_abs_flow
        self.min_move_pct_10s = min_move_pct_10s
        self.min_sigma_multiple = min_sigma_multiple
        self.min_coverage = min_coverage
        self.max_spread_pct = max_spread_pct
        self.min_book_depth_usd = min_book_depth_usd
        self.max_late_pct_60s = max_late_pct_60s
        self.require_acceleration = require_acceleration
        self.max_24h_move_pct = max_24h_move_pct
        self.min_trades_10s = min_trades_10s
        self.baseline_floor_fraction = baseline_floor_fraction

        self.states: Dict[str, SymbolFlow] = {}
        self.tracked: set = set()
        self.last_message_ms: int = 0
        self.messages: int = 0
        self.truncated: int = 0   # samples refused for poor coverage

    # ------------------------------------------------------------ lifecycle

    def ensure(self, symbol: str) -> SymbolFlow:
        state = self.states.get(symbol)
        if state is None:
            state = SymbolFlow(
                symbol=symbol,
                market=self.market,
                floor_fraction=self.baseline_floor_fraction,
            )
            self.states[symbol] = state
        return state

    def set_universe(self, symbols: Iterable[str]) -> None:
        wanted = set(symbols)
        self.tracked = wanted
        for symbol in list(self.states):
            if symbol not in wanted:
                del self.states[symbol]

    def apply_floors(self) -> None:
        for state in self.states.values():
            state.apply_liquidity_floor()

    def seed(self, symbol: str, bars: Sequence) -> int:
        """Seed a symbol's baseline from 1s klines. Returns samples added."""
        state = self.ensure(symbol)
        state.seed_from_klines(bars)
        state.apply_liquidity_floor()
        return state.baseline.count

    # --------------------------------------------------------------- ingest

    def ingest_ticker(self, msg: dict) -> Optional[str]:
        """Feed one ``@ticker`` payload. Returns the symbol if applied."""
        symbol = msg.get("s")
        if not isinstance(symbol, str):
            return None
        if self.tracked and symbol not in self.tracked:
            return None
        try:
            tick = Tick(
                ts=int(msg.get("E") or 0),
                price=float(msg["c"]),
                quote_vol=float(msg.get("q") or 0.0),
                base_vol=float(msg.get("v") or 0.0),
                trades=int(float(msg.get("n") or 0)),
                bid=float(msg.get("b") or 0.0),
                ask=float(msg.get("a") or 0.0),
                bid_qty=float(msg.get("B") or 0.0),
                ask_qty=float(msg.get("A") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if tick.ts <= 0 or tick.price <= 0:
            return None

        state = self.ensure(symbol)
        state.update(tick)
        state.price_change_pct_24h = _safe_float(msg.get("P"))
        state.high_24h = _safe_float(msg.get("h"))
        state.low_24h = _safe_float(msg.get("l"))
        self.last_message_ms = max(self.last_message_ms, tick.ts)
        self.messages += 1
        return symbol

    # -------------------------------------------------------------- detect

    def detect(self, now: Optional[int] = None) -> List[OnsetCandidate]:
        """Scan every tracked symbol for a flow burst that is just starting."""
        stamp = now if now is not None else int(time.time() * 1000)
        # Ignore symbols whose feed has gone quiet: a stale last price would
        # otherwise be compared against fresh flow from another symbol.
        stale_cutoff = stamp - 15_000

        candidates: List[OnsetCandidate] = []
        for symbol, state in self.states.items():
            if state.last_tick is None or state.last_tick.ts < stale_cutoff:
                continue
            if not state.baseline.ready:
                continue

            candidate = self._evaluate(state, stamp)
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.flow_quote, reverse=True)
        return candidates

    def _evaluate(self, state: SymbolFlow, stamp: int) -> Optional[OnsetCandidate]:
        window = self.window_sec
        flow, coverage = state.flow_quote(window, stamp)
        if coverage < self.min_coverage:
            self.truncated += 1
            return None
        if flow < self.min_abs_flow:
            return None

        burst_multiple = state.baseline.multiple(flow)
        burst_z = state.baseline.zscore(flow)
        if burst_multiple < self.min_burst_multiple:
            return None
        if burst_z < self.min_burst_z and burst_multiple < 2 * self.min_burst_multiple:
            return None

        change_10s = state.change_over(window, stamp)
        if change_10s is None:
            return None
        direction = 1 if change_10s > 0 else -1
        if abs(change_10s) < self.min_move_pct_10s / 100.0:
            return None

        sigma_10s = state.sigma_window(window)
        sigma_multiple = safe_div(abs(change_10s), sigma_10s) if sigma_10s > 0 else 0.0
        if sigma_10s > 0 and sigma_multiple < self.min_sigma_multiple:
            return None

        change_30s = state.change_over(30.0, stamp)
        change_60s = state.change_over(60.0, stamp)
        # "Already moved" guard: if the last minute dwarfs the last 10
        # seconds, this is the tail of a move, not the start of one.
        if change_60s is not None and abs(change_60s) > self.max_late_pct_60s / 100.0:
            if abs(change_60s) > 3.0 * abs(change_10s):
                return None
        if state.price_change_pct_24h and abs(state.price_change_pct_24h) > self.max_24h_move_pct:
            if direction * state.price_change_pct_24h > 0:
                return None

        acceleration = state.flow_acceleration(stamp)
        if self.require_acceleration and acceleration is not None and acceleration < 1.0:
            return None

        trades_10s, _ = state.flow_trades(window, stamp)
        if trades_10s < self.min_trades_10s:
            return None
        trades_multiple = safe_div(trades_10s, max(state.baseline.med, 1e-9))

        spread = state.spread_pct
        if spread > self.max_spread_pct:
            return None
        depth = state.book_depth_usd
        if depth and depth < self.min_book_depth_usd:
            return None

        range_position = state.range_position(window, stamp)
        # A pump should close near the top of its own window, a dump near the
        # bottom. A burst that spikes and immediately reverses is a wick.
        aligned_range = range_position if direction > 0 else 1.0 - range_position
        reasons = [
            f"{window:.0f}s flow ${flow:,.0f} = {burst_multiple:.1f}x baseline "
            f"({burst_z:.1f}sigma)",
            f"{window:.0f}s move {change_10s * 100:+.2f}% "
            f"({sigma_multiple:.1f}x {sigma_10s * 100:.2f}% vol)",
        ]
        if acceleration is not None:
            reasons.append(f"flow acceleration {acceleration:.1f}x vs prior {window:.0f}s")
        reasons.append(f"trades {trades_10s} in {window:.0f}s")
        if spread:
            reasons.append(f"spread {spread * 100:.3f}%")
        if aligned_range < 0.4:
            reasons.append(
                f"warning: closed at {range_position * 100:.0f}% of window range"
            )

        return OnsetCandidate(
            symbol=state.symbol,
            market=state.market,
            direction=direction,
            price=state.last_price,
            flow_quote=flow,
            coverage=coverage,
            burst_multiple=burst_multiple,
            burst_z=burst_z,
            change_10s=change_10s,
            change_30s=change_30s,
            change_60s=change_60s,
            sigma_10s=sigma_10s,
            sigma_multiple=sigma_multiple,
            acceleration=acceleration,
            range_position=range_position,
            spread_pct=spread,
            book_depth_usd=depth,
            quote_volume_24h=state.quote_volume_24h,
            trades_10s=trades_10s,
            trades_multiple=trades_multiple,
            reasons=reasons,
        )

    # ------------------------------------------------------------- reporting

    def activity(self, limit: int = 5, now: Optional[int] = None) -> List[Tuple[str, float]]:
        """Symbols with the largest current-window flow, for the heartbeat."""
        stamp = now if now is not None else int(time.time() * 1000)
        ranked: List[Tuple[str, float]] = []
        for symbol, state in self.states.items():
            if state.last_tick is None:
                continue
            flow, coverage = state.flow_quote(self.window_sec, stamp)
            if coverage >= self.min_coverage and flow > 0:
                ranked.append((symbol, flow))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:limit]

    @property
    def ready_count(self) -> int:
        return sum(1 for s in self.states.values() if s.baseline.ready)

    @property
    def tracked_count(self) -> int:
        return len(self.tracked) or len(self.states)


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
