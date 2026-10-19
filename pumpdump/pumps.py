"""Market-wide pump & dump screener.

The live onset engine (:mod:`pumpdump.flow`) catches a move *as it starts*.
This module answers the complementary question -- **"what is pumping or dumping
right now?"** -- in a single pass, so you get an answer immediately instead of
after the scanner has been running for hours.

It works from 1-minute klines, which give exact volume and taker-side data (the
websocket ticker stream does not carry taker volume), so each row is a
self-contained, auditable verdict:

===================  ==================================================
multi-horizon return 5m / 15m / 60m / 24h, so a fresh pump is
                     distinguishable from the tail of an old one
volume ratio         the last 5 completed minutes vs this symbol's own
                     median 5-minute volume
taker buy ratio      aggressive buying vs selling, from kline field 9
close position       where price closed inside the 5-minute range -- a
                     pump that closed on its low is a wick, not a move
24h location         distance from the 24h high/low, to spot a move
                     that is already extended
===================  ==================================================

A liquidity floor and a book-independent volume floor keep illiquid pairs from
manufacturing absurd ratios.
"""

from __future__ import annotations

import csv
import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .common import (
    LOG,
    Color,
    clock_utc,
    fmt_money,
    fmt_pct,
    fmt_price,
    iso_utc,
    median,
    now_ms,
    pct_change,
    safe_div,
    stdev,
)
from .config import Config
from .klines import Bar, parse_klines
from .rest import BinanceError, BinanceREST
from .universe import Universe

PUMP = "PUMP"
DUMP = "DUMP"
REVERSAL_DUMP = "REVERSAL_DUMP"
REVERSAL_PUMP = "REVERSAL_PUMP"

WINDOW_BARS = 5          # the "recent" measurement window, in 1m bars
DEFAULT_KLINE_LIMIT = 180  # 3 hours of 1m bars


@dataclass
class PumpDumpRow:
    """One symbol's current pump/dump verdict."""

    symbol: str
    kind: str
    score: float
    price: float
    ret_5m: Optional[float] = None
    ret_15m: Optional[float] = None
    ret_60m: Optional[float] = None
    ret_24h: Optional[float] = None
    vol_ratio: float = 0.0
    vol_z: float = 0.0
    recent_quote_volume: float = 0.0
    baseline_quote_volume: float = 0.0
    quote_volume_24h: float = 0.0
    trades_ratio: float = 0.0
    taker_buy_ratio: float = 0.5
    close_position: float = 0.5
    range_pct: float = 0.0
    atr_pct: float = 0.0
    from_24h_high: Optional[float] = None
    from_24h_low: Optional[float] = None
    bars_since_extreme: Optional[int] = None
    reasons: List[str] = field(default_factory=list)
    components: Dict[str, float] = field(default_factory=dict)

    @property
    def direction(self) -> int:
        if self.kind in (PUMP, REVERSAL_PUMP):
            return 1
        return -1

    def to_row(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "score": round(self.score, 1),
            "price": self.price,
            "ret_5m": self.ret_5m,
            "ret_15m": self.ret_15m,
            "ret_60m": self.ret_60m,
            "ret_24h": self.ret_24h,
            "vol_ratio": round(self.vol_ratio, 3),
            "vol_z": round(self.vol_z, 3),
            "recent_quote_volume": round(self.recent_quote_volume, 2),
            "baseline_quote_volume": round(self.baseline_quote_volume, 2),
            "quote_volume_24h": round(self.quote_volume_24h, 2),
            "trades_ratio": round(self.trades_ratio, 3),
            "taker_buy_ratio": round(self.taker_buy_ratio, 4),
            "close_position": round(self.close_position, 4),
            "range_pct": round(self.range_pct, 6),
            "atr_pct": round(self.atr_pct, 6),
            "from_24h_high": self.from_24h_high,
            "from_24h_low": self.from_24h_low,
            "bars_since_extreme": self.bars_since_extreme,
            "reasons": " | ".join(self.reasons),
        }


@dataclass
class ScreenThresholds:
    min_move_5m: float = 1.5          # percent
    min_move_15m: float = 2.5
    min_vol_ratio: float = 3.0
    min_vol_z: float = 2.0
    min_recent_usd: float = 20_000.0  # money actually traded in the window
    min_quote_volume_24h: float = 750_000.0
    min_trades_5m: int = 25
    # Reversal legs: a big prior move plus a fast move the other way.
    reversal_prior_pct: float = 6.0
    reversal_fast_pct: float = 3.0
    reversal_vol_ratio: float = 1.5
    wick_fade_ratio: float = 0.5

    def as_dict(self) -> Dict[str, float]:
        return {
            "min_move_5m": self.min_move_5m,
            "min_move_15m": self.min_move_15m,
            "min_vol_ratio": self.min_vol_ratio,
            "min_vol_z": self.min_vol_z,
            "min_recent_usd": self.min_recent_usd,
            "min_quote_volume_24h": self.min_quote_volume_24h,
            "min_trades_5m": self.min_trades_5m,
            "reversal_prior_pct": self.reversal_prior_pct,
            "reversal_fast_pct": self.reversal_fast_pct,
            "reversal_vol_ratio": self.reversal_vol_ratio,
        }


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def analyse_symbol(
    symbol: str,
    bars: Sequence[Bar],
    thresholds: ScreenThresholds,
    *,
    quote_volume_24h: float = 0.0,
    ret_24h: Optional[float] = None,
) -> Optional[PumpDumpRow]:
    """Compute the pump/dump verdict for one symbol, or ``None`` if uninterested."""
    # Need enough history for returns, the baseline, and a 24h high/low scan.
    if len(bars) < 65:
        return None

    completed = list(bars[:-1])          # drop the in-progress minute
    if len(completed) < 60:
        return None
    last = bars[-1]
    price = last.close
    if price <= 0:
        return None

    row = PumpDumpRow(
        symbol=symbol,
        kind="QUIET",
        score=0.0,
        price=price,
        quote_volume_24h=quote_volume_24h or sum(b.quote_volume for b in bars[-1440:]),
        ret_24h=ret_24h,
    )

    row.ret_5m = pct_change(price, completed[-5].close)
    row.ret_15m = pct_change(price, completed[-15].close)
    row.ret_60m = pct_change(price, completed[-60].close)

    # --- volume vs the symbol's own median 5-minute volume ---------------
    recent_bars = completed[-WINDOW_BARS:]
    row.recent_quote_volume = sum(b.quote_volume for b in recent_bars)

    older = completed[:-WINDOW_BARS]
    chunks = [
        sum(b.quote_volume for b in older[i : i + WINDOW_BARS])
        for i in range(0, max(0, len(older) - WINDOW_BARS + 1), WINDOW_BARS)
    ]
    chunks = [c for c in chunks if c >= 0]
    baseline = median(chunks) if chunks else 0.0
    spread = stdev(chunks)
    # Floor from the 24h average 5-minute volume, so a symbol that was quiet
    # all day long cannot produce a 500x ratio off a near-zero divisor.
    floor = (row.quote_volume_24h / 288.0) * 0.25 if row.quote_volume_24h else 0.0
    reference = max(baseline, floor)
    row.baseline_quote_volume = reference
    row.vol_ratio = safe_div(row.recent_quote_volume, reference)
    if spread > 0:
        row.vol_z = (row.recent_quote_volume - (sum(chunks) / len(chunks))) / spread if chunks else 0.0
    else:
        row.vol_z = row.vol_ratio if reference > 0 else 0.0

    # --- taker aggression and bar structure ------------------------------
    total_quote = sum(b.quote_volume for b in recent_bars)
    taker_buy = sum(b.taker_buy_quote for b in recent_bars)
    row.taker_buy_ratio = safe_div(taker_buy, total_quote, 0.5)

    window = recent_bars + [last]
    high = max(b.high for b in window)
    low = min(b.low for b in window)
    row.range_pct = safe_div(high - low, low)
    row.close_position = (
        (price - low) / (high - low) if high > low else 0.5
    )

    recent_trades = sum(b.trades for b in recent_bars)
    older_trades = [
        sum(b.trades for b in older[i : i + WINDOW_BARS])
        for i in range(0, max(0, len(older) - WINDOW_BARS + 1), WINDOW_BARS)
    ]
    row.trades_ratio = safe_div(recent_trades, median(older_trades) if older_trades else 0.0)

    # --- ATR% as a volatility sanity check -------------------------------
    ranges = [
        max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i - 1].close),
            abs(bars[i].low - bars[i - 1].close),
        )
        for i in range(1, len(bars))
    ]
    if ranges:
        window_atr = sum(ranges[-20:]) / min(20, len(ranges))
        row.atr_pct = safe_div(window_atr, price)

    # --- 24h location ----------------------------------------------------
    lookback = bars[-1440:] if len(bars) >= 1440 else bars
    high_24h = max(b.high for b in lookback)
    low_24h = min(b.low for b in lookback)
    if high_24h > 0:
        row.from_24h_high = pct_change(price, high_24h)
    if low_24h > 0:
        row.from_24h_low = pct_change(price, low_24h)

    # How long ago was the extreme? Distinguishes "starting" from "finished".
    extreme_index = max(
        range(len(lookback)), key=lambda i: lookback[i].high
    )
    row.bars_since_extreme = len(lookback) - 1 - extreme_index

    _classify(row, thresholds)
    return row if row.kind != "QUIET" else None


def _classify(row: PumpDumpRow, thresholds: ScreenThresholds) -> None:
    """Assign a kind, score and reasons. Mutates ``row``."""
    reasons: List[str] = []
    components: Dict[str, float] = {}

    ret_5m = row.ret_5m or 0.0
    ret_15m = row.ret_15m or 0.0
    ret_60m = row.ret_60m or 0.0

    # Liquidity and activity floors first: without these, thin pairs dominate.
    if row.quote_volume_24h < thresholds.min_quote_volume_24h:
        return
    if row.recent_quote_volume < thresholds.min_recent_usd:
        return

    # Determine the candidate kind in a LOCAL variable and only commit it to
    # the row once every gate has passed. Assigning row.kind up front means an
    # early `return` from a gate leaves the row labelled PUMP with score 0,
    # which then leaks out of analyse_symbol as a "hit".
    kind = "QUIET"

    # --- reversal legs ---------------------------------------------------
    # A move that has already run and is now being given back (or squeezed).
    if ret_60m > thresholds.reversal_prior_pct / 100.0 and ret_5m < -thresholds.reversal_fast_pct / 100.0:
        kind = REVERSAL_DUMP
        reasons.append(
            f"gave back {fmt_pct(ret_5m)} in 5m after running {fmt_pct(ret_60m)} over 1h"
        )
    elif ret_60m < -thresholds.reversal_prior_pct / 100.0 and ret_5m > thresholds.reversal_fast_pct / 100.0:
        kind = REVERSAL_PUMP
        reasons.append(
            f"bounced {fmt_pct(ret_5m)} in 5m after falling {fmt_pct(ret_60m)} over 1h"
        )

    if kind == "QUIET":
        # --- fresh pump / dump ------------------------------------------
        if ret_5m >= thresholds.min_move_5m / 100.0 or ret_15m >= thresholds.min_move_15m / 100.0:
            kind = PUMP
        elif ret_5m <= -thresholds.min_move_5m / 100.0 or ret_15m <= -thresholds.min_move_15m / 100.0:
            kind = DUMP
        else:
            return

        # A directional move must be backed by abnormal volume.
        if row.vol_ratio < thresholds.min_vol_ratio:
            return
        if row.vol_z < thresholds.min_vol_z and row.vol_ratio < 2 * thresholds.min_vol_ratio:
            return

    else:
        # Reversals need some volume, but less than a fresh breakout.
        if row.vol_ratio < thresholds.reversal_vol_ratio:
            return

    # All gates passed: commit the verdict.
    row.kind = kind

    # --- scoring ---------------------------------------------------------
    direction = row.direction

    move_magnitude = max(abs(ret_5m), abs(ret_15m) / 1.5)
    move_gate = max(thresholds.min_move_5m, 0.01) / 100.0
    components["move"] = round(
        30.0 * min(1.0, safe_div(move_magnitude, move_gate * 3.0)), 2
    )

    components["volume"] = round(
        30.0 * min(1.0, safe_div(row.vol_ratio, thresholds.min_vol_ratio * 4.0)), 2
    )

    aligned_taker = row.taker_buy_ratio if direction > 0 else 1.0 - row.taker_buy_ratio
    components["aggression"] = round(
        20.0 * max(0.0, min(1.0, (aligned_taker - 0.5) / 0.25)), 2
    )

    aligned_close = row.close_position if direction > 0 else 1.0 - row.close_position
    components["structure"] = round(
        12.0 * max(0.0, min(1.0, (aligned_close - 0.4) / 0.5)), 2
    )

    components["liquidity"] = round(
        8.0 * max(0.0, min(1.0, (math.log10(max(row.quote_volume_24h, 1.0)) - 6.0) / 1.5)),
        2,
    )

    reasons.append(
        f"5m {fmt_pct(ret_5m)} / 15m {fmt_pct(ret_15m)} / 1h {fmt_pct(ret_60m)}"
    )
    reasons.append(
        f"volume {row.vol_ratio:.1f}x its 5m median "
        f"({fmt_money(row.recent_quote_volume)} in 5m, {row.vol_z:.1f}sigma)"
    )
    side = "buy" if direction > 0 else "sell"
    reasons.append(f"taker {side} {aligned_taker * 100:.0f}% of volume")
    reasons.append(
        f"closed at {row.close_position * 100:.0f}% of the 5m range, "
        f"ATR {row.atr_pct * 100:.2f}%"
    )
    if row.bars_since_extreme is not None:
        reasons.append(
            f"24h extreme was {row.bars_since_extreme} min ago "
            f"(high {fmt_pct(row.from_24h_high)}, low {fmt_pct(row.from_24h_low)})"
        )
    # A pump that closed on its low is being sold into.
    adverse_wick = (1.0 - row.close_position) if direction > 0 else row.close_position
    if adverse_wick >= thresholds.wick_fade_ratio:
        components["structure"] = round(components["structure"] * 0.4, 2)
        reasons.append(
            f"fading: closed {adverse_wick * 100:.0f}% away from the "
            f"{'high' if direction > 0 else 'low'}"
        )

    row.score = max(0.0, min(100.0, sum(components.values())))
    row.components = components
    row.reasons = reasons


# --------------------------------------------------------------------------
# The screener
# --------------------------------------------------------------------------

class PumpDumpScreener:
    """Fetches and screens the whole liquid spot market."""

    def __init__(
        self,
        cfg: Config,
        thresholds: ScreenThresholds,
        *,
        top_n: int = 200,
        kline_limit: int = DEFAULT_KLINE_LIMIT,
        workers: int = 8,
        interval: str = "1m",
    ) -> None:
        self.cfg = cfg
        self.thresholds = thresholds
        self.top_n = top_n
        self.kline_limit = kline_limit
        self.workers = max(1, workers)
        self.interval = interval
        self.spec = cfg.market_specs()[0]
        self.client = BinanceREST(
            self.spec,
            api_key=cfg.api_key,
            api_secret=cfg.api_secret,
            timeout=cfg.http_timeout_sec,
        )
        self.universe = Universe(self.spec, cfg)

    def prepare(self) -> List[str]:
        self.universe.load_metadata(self.client)
        self.universe.refresh_context(self.client)
        ranked = self.universe.top_by_volume(self.top_n)
        LOG.info(
            "pump/dump screen: %d symbols (>= %s 24h volume)",
            len(ranked), fmt_money(self.cfg.min_quote_volume_24h),
        )
        return [ctx.symbol for ctx in ranked]

    def screen(self, symbols: Optional[Sequence[str]] = None) -> List[PumpDumpRow]:
        if symbols is None:
            symbols = self.prepare()
        if not symbols:
            return []

        rows: List[PumpDumpRow] = []
        total = len(symbols)
        done = 0
        LOG.info("fetching %s klines for %d symbols...", self.interval, total)
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(self.client.klines, symbol, self.interval, self.kline_limit): symbol
                for symbol in symbols
            }
            for future, symbol in futures.items():
                done += 1
                try:
                    raw = future.result()
                except BinanceError as exc:
                    LOG.debug("klines failed for %s: %s", symbol, exc)
                    continue
                bars = parse_klines(raw)
                ctx = self.universe.get(symbol)
                try:
                    row = analyse_symbol(
                        symbol,
                        bars,
                        self.thresholds,
                        quote_volume_24h=ctx.quote_volume if ctx else 0.0,
                        ret_24h=(ctx.price_change_pct / 100.0) if ctx else None,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the screen
                    LOG.debug("analysis failed for %s: %s", symbol, exc)
                    continue
                if row is not None:
                    rows.append(row)
                if done % 100 == 0 or done == total:
                    LOG.info("  screened %d/%d (%d hits)", done, total, len(rows))

        rows.sort(key=lambda r: r.score, reverse=True)
        return rows


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

_HEADER = (
    f"{'#':>3} {'symbol':<12} {'kind':<13} {'score':>5} {'price':>13} "
    f"{'5m':>8} {'15m':>8} {'1h':>8} {'vol':>7} {'taker':>6} "
    f"{'close':>6} {'5m USD':>10} {'24h USD':>10}"
)


def print_rows(rows: Sequence[PumpDumpRow], *, limit: int = 40, title: str = "") -> None:
    paint = Color.paint
    print()
    if title:
        print(paint(title, Color.BOLD))
    if not rows:
        print(
            paint(
                "No pump/dump candidates cleared the thresholds right now.",
                Color.YELLOW,
            )
        )
        print()
        return

    print(paint(_HEADER, Color.BOLD))
    print(paint("-" * len(_HEADER), Color.GREY))
    for index, row in enumerate(rows[:limit], start=1):
        if row.kind == PUMP:
            kind_color = Color.BRIGHT_GREEN
        elif row.kind == DUMP:
            kind_color = Color.BRIGHT_RED
        elif row.kind == REVERSAL_DUMP:
            kind_color = Color.RED
        else:
            kind_color = Color.GREEN
        print(
            f"{index:>3} {row.symbol:<12} "
            f"{paint(f'{row.kind:<13}', kind_color, Color.BOLD)} "
            f"{row.score:>5.0f} "
            f"{fmt_price(row.price):>13} "
            f"{fmt_pct(row.ret_5m):>8} "
            f"{fmt_pct(row.ret_15m):>8} "
            f"{fmt_pct(row.ret_60m):>8} "
            f"{row.vol_ratio:>6.1f}x "
            f"{row.taker_buy_ratio * 100:>5.0f}% "
            f"{row.close_position * 100:>5.0f}% "
            f"{fmt_money(row.recent_quote_volume):>10} "
            f"{fmt_money(row.quote_volume_24h):>10}"
        )

    print()
    top = rows[: min(5, len(rows))]
    if top:
        print(paint("detail:", Color.BOLD))
        for row in top:
            print(f"  {paint(row.symbol, Color.BOLD)} ({row.kind}, score {row.score:.0f})")
            for reason in row.reasons:
                print(paint(f"      - {reason}", Color.GREY))
    print()


def write_rows(
    rows: Sequence[PumpDumpRow],
    thresholds: ScreenThresholds,
    json_path: Optional[str],
    csv_path: Optional[str],
    *,
    scanned: int = 0,
) -> None:
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "generated_ms": now_ms(),
                    "generated_utc": iso_utc(),
                    "market": "spot",
                    "symbols_scanned": scanned,
                    "thresholds": thresholds.as_dict(),
                    "candidates": [r.to_row() for r in rows],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    if csv_path:
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = list(rows[0].to_row().keys()) if rows else [
            "symbol", "kind", "score", "price"
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row.to_row())
