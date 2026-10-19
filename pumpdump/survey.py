"""Market survey: apply the Turtle system across every liquid spot pair.

Pipeline:

1. Build the universe from ``exchangeInfo`` + the 24h ticker (reusing
   :class:`~pumpdump.universe.Universe`), filtered by liquidity.
2. Fetch OHLCV klines per symbol per timeframe, concurrently.
3. Compute a :class:`~pumpdump.turtle.TurtleSnapshot` per (symbol, timeframe).
4. Score every breakout setup, using cross-timeframe alignment as one input.
5. Cluster symbols by realised return correlation, then walk the ranking in
   descending score order and allocate units through
   :class:`~pumpdump.turtle.RiskBudget`, which enforces the Turtle portfolio
   limits.
6. Print a ranked survey and persist it.

The ordering in step 5 is what makes the limits behave: the weakest setup is
the one that gets rejected, not whichever happened to be evaluated last.
"""

from __future__ import annotations

import csv
import json
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
    now_ms,
)
from .config import Config
from .indicators import returns
from .klines import Bar, parse_klines
from .rest import BinanceError, BinanceREST
from .turtle import (
    PositionPlan,
    RiskBudget,
    SetupScore,
    TurtleParams,
    TurtleSnapshot,
    cluster_by_correlation,
    compute_snapshot,
    position_plan,
    score_setup,
)
from .universe import Universe

#: Klines fetched per symbol per timeframe. 250 daily bars is a full year.
DEFAULT_KLINE_LIMIT = 250
DEFAULT_TIMEFRAMES = ("1d", "4h", "1h")


@dataclass
class RankedSetup:
    snapshot: TurtleSnapshot
    score: SetupScore
    group: str = "unclustered"
    plan: Optional[PositionPlan] = None
    accepted: bool = False
    reject_reason: Optional[str] = None

    @property
    def symbol(self) -> str:
        return self.snapshot.symbol

    @property
    def direction(self) -> int:
        return self.snapshot.direction


@dataclass
class SurveyReport:
    generated_ms: int
    timeframes: List[str]
    equity: float
    symbols_scanned: int = 0
    symbols_with_data: int = 0
    ranked: List[RankedSetup] = field(default_factory=list)
    exit_signals: List[TurtleSnapshot] = field(default_factory=list)
    clusters: Dict[str, str] = field(default_factory=dict)
    budget: Optional[RiskBudget] = None

    @property
    def accepted(self) -> List[RankedSetup]:
        return [r for r in self.ranked if r.accepted]

    @property
    def long_setups(self) -> List[RankedSetup]:
        return [r for r in self.ranked if r.direction > 0]

    @property
    def short_setups(self) -> List[RankedSetup]:
        return [r for r in self.ranked if r.direction < 0]


class MarketSurvey:
    """Runs one end-to-end Turtle survey over the spot market."""

    def __init__(
        self,
        cfg: Config,
        params: TurtleParams,
        *,
        timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
        equity: float = 10_000.0,
        top_n: int = 150,
        kline_limit: int = DEFAULT_KLINE_LIMIT,
        workers: int = 8,
        long_only: bool = True,
    ) -> None:
        self.cfg = cfg
        self.params = params
        self.timeframes = list(timeframes)
        self.equity = equity
        self.top_n = top_n
        self.kline_limit = kline_limit
        self.workers = max(1, workers)
        self.long_only = long_only
        self.spec = cfg.market_specs()[0]
        self.client = BinanceREST(
            self.spec,
            api_key=cfg.api_key,
            api_secret=cfg.api_secret,
            timeout=cfg.http_timeout_sec,
        )
        self.universe = Universe(self.spec, cfg)

    # ------------------------------------------------------------- fetching

    def prepare_universe(self) -> List[str]:
        self.universe.load_metadata(self.client)
        self.universe.refresh_context(self.client)
        ranked = self.universe.top_by_volume(self.top_n)
        LOG.info(
            "survey universe: %d symbols (of %d tracked, >= %s 24h volume)",
            len(ranked), len(self.universe.tracked),
            fmt_money(self.cfg.min_quote_volume_24h),
        )
        return [ctx.symbol for ctx in ranked]

    def _fetch(self, symbol: str, interval: str) -> List[Bar]:
        try:
            rows = self.client.klines(symbol, interval, self.kline_limit)
        except BinanceError as exc:
            LOG.debug("klines failed for %s %s: %s", symbol, interval, exc)
            return []
        return parse_klines(rows)

    def collect_bars(
        self, symbols: Sequence[str]
    ) -> Dict[Tuple[str, str], List[Bar]]:
        """Fetch all (symbol, timeframe) klines concurrently."""
        jobs = [(s, tf) for s in symbols for tf in self.timeframes]
        out: Dict[Tuple[str, str], List[Bar]] = {}
        total = len(jobs)
        done = 0
        LOG.info("fetching %d kline series across %d symbols...", total, len(symbols))
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(self._fetch, symbol, interval): (symbol, interval)
                for symbol, interval in jobs
            }
            for future in futures:
                key = futures[future]
                try:
                    out[key] = future.result()
                except Exception as exc:  # noqa: BLE001
                    LOG.debug("fetch failed for %s: %s", key, exc)
                    out[key] = []
                done += 1
                if done % 100 == 0 or done == total:
                    LOG.info("  fetched %d/%d series", done, total)
        return out

    # ------------------------------------------------------------- analysis

    def build_snapshots(
        self, bars: Dict[Tuple[str, str], List[Bar]]
    ) -> Dict[str, Dict[str, TurtleSnapshot]]:
        """``snapshots[symbol][timeframe]`` for every symbol with enough data."""
        snapshots: Dict[str, Dict[str, TurtleSnapshot]] = {}
        for (symbol, timeframe), series in bars.items():
            if len(series) < self.params.minimum_bars:
                continue
            ctx = self.universe.get(symbol)
            snapshot = compute_snapshot(
                symbol,
                timeframe,
                series,
                self.params,
                quote_volume_24h=ctx.quote_volume if ctx else 0.0,
            )
            if snapshot is None:
                continue
            snapshots.setdefault(symbol, {})[timeframe] = snapshot
        return snapshots

    def cluster(
        self, bars: Dict[Tuple[str, str], List[Bar]], order: Sequence[str]
    ) -> Dict[str, str]:
        """Correlation clusters from the primary (longest) timeframe returns."""
        primary = self.timeframes[0]
        returns_by_symbol: Dict[str, List[float]] = {}
        for symbol in order:
            series = bars.get((symbol, primary)) or []
            if len(series) < 12:
                continue
            vals = [r for r in returns(series) if r is not None]
            if len(vals) >= 10:
                returns_by_symbol[symbol] = vals
        if len(returns_by_symbol) < 2:
            return {}
        clusters = cluster_by_correlation(returns_by_symbol, threshold=0.7, order=order)
        sizes: Dict[str, int] = {}
        for group in clusters.values():
            sizes[group] = sizes.get(group, 0) + 1
        LOG.info(
            "correlation clustering: %d symbols into %d groups (largest %d)",
            len(clusters), len(sizes), max(sizes.values()) if sizes else 0,
        )
        return clusters

    def rank(
        self,
        snapshots: Dict[str, Dict[str, TurtleSnapshot]],
        clusters: Dict[str, str],
        order: Sequence[str],
    ) -> List[RankedSetup]:
        """Score every symbol's best timeframe and sort by score."""
        ranked: List[RankedSetup] = []
        primary = self.timeframes[0]

        for symbol in order:
            per_timeframe = snapshots.get(symbol)
            if not per_timeframe:
                continue
            primary_snapshot = per_timeframe.get(primary)
            if primary_snapshot is None:
                continue
            if primary_snapshot.direction == 0:
                continue
            if self.long_only and primary_snapshot.direction < 0:
                # Spot cannot short: a downside breakout is an exit/avoid
                # signal, and is surfaced separately rather than as a trade.
                continue
            alignment = [
                snap for tf, snap in per_timeframe.items() if tf != primary
            ]
            score = score_setup(primary_snapshot, self.params, align_timeframes=alignment)
            if score.rejected:
                continue
            ranked.append(
                RankedSetup(
                    snapshot=primary_snapshot,
                    score=score,
                    group=clusters.get(symbol, "unclustered"),
                )
            )

        ranked.sort(key=lambda r: r.score.score, reverse=True)
        return ranked

    def allocate(self, ranked: List[RankedSetup]) -> RiskBudget:
        """Walk the ranking in order and fill the Turtle risk budget."""
        budget = RiskBudget(self.params)
        for entry in ranked:
            unit = self.params.max_units_per_market  # target full size when it fits
            reason = budget.check(
                entry.symbol, entry.group, entry.direction, unit
            )
            units = 1
            if reason is not None:
                # Try a single unit before giving up entirely.
                reason = budget.check(entry.symbol, entry.group, entry.direction, 1)
            if reason is not None:
                budget.reject(entry.symbol, reason)
                entry.reject_reason = reason
                continue

            plan = position_plan(
                entry.snapshot,
                self.params,
                equity=self.equity,
                units=units,
                exit_level=entry.snapshot.exit_low_10,
            )
            if plan is None:
                entry.reject_reason = "could not size the position"
                continue
            entry.plan = plan
            entry.accepted = True
            budget.accept(
                entry.symbol, entry.group, entry.direction, units,
                plan.total_notional, plan.total_risk,
            )
        return budget

    # ------------------------------------------------------------------ run

    def run(self) -> SurveyReport:
        report = SurveyReport(
            generated_ms=now_ms(),
            timeframes=list(self.timeframes),
            equity=self.equity,
        )
        symbols = self.prepare_universe()
        report.symbols_scanned = len(symbols)
        if not symbols:
            return report

        bars = self.collect_bars(symbols)
        snapshots = self.build_snapshots(bars)
        report.symbols_with_data = len(snapshots)
        LOG.info(
            "computed indicators for %d/%d symbols",
            report.symbols_with_data, report.symbols_scanned,
        )

        report.clusters = self.cluster(bars, symbols)
        ranked = self.rank(snapshots, report.clusters, symbols)
        report.ranked = ranked
        report.budget = self.allocate(ranked)

        # Collect live exit signals: these matter for positions already held.
        exits: List[TurtleSnapshot] = []
        for symbol in symbols:
            per_timeframe = snapshots.get(symbol)
            if not per_timeframe:
                continue
            primary_snapshot = per_timeframe.get(self.timeframes[0])
            if primary_snapshot is None:
                continue
            if primary_snapshot.exit_long or primary_snapshot.exit_short:
                exits.append(primary_snapshot)
        report.exit_signals = exits
        return report


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

_HEADER = (
    f"{'#':>3} {'symbol':<12} {'dir':<4} {'score':>5} {'close':>13} "
    f"{'N(ATR)':>11} {'ATR%':>6} {'extN':>5} {'ADX':>5} {'grp':<5} "
    f"{'units':>5} {'notional':>10} {'stop':>13} {'risk':>8}"
)


def print_report(report: SurveyReport, *, limit: int = 25, color: bool = True) -> None:
    """Render the survey to stdout."""
    paint = Color.paint
    print()
    print(
        paint(
            f"TURTLE MARKET SURVEY  {clock_utc(report.generated_ms)} UTC  |  "
            f"spot  |  timeframes {','.join(report.timeframes)}  |  "
            f"equity ${report.equity:,.0f}",
            Color.BOLD,
        )
    )
    print(
        paint(
            f"scanned {report.symbols_scanned} symbols, "
            f"{report.symbols_with_data} with enough history, "
            f"{len(report.ranked)} breakout setups, "
            f"{len(report.accepted)} allocated",
            Color.GREY,
        )
    )
    print()

    if not report.ranked:
        print(
            paint(
                "No breakout setups passed the filters "
                "(this is normal in a quiet or range-bound market).",
                Color.YELLOW,
            )
        )
        print()
    else:
        print(paint(_HEADER, Color.BOLD))
        print(paint("-" * len(_HEADER), Color.GREY))
        for index, entry in enumerate(report.ranked[:limit], start=1):
            snap = entry.snapshot
            plan = entry.plan
            direction_label = "LONG" if entry.direction > 0 else "SHORT"
            direction_color = Color.GREEN if entry.direction > 0 else Color.RED
            ext = (
                snap.extension_up_atr
                if entry.direction > 0
                else snap.extension_down_atr
            )
            row = (
                f"{index:>3} {entry.symbol:<12} "
                f"{paint(f'{direction_label:<4}', direction_color)} "
                f"{entry.score.score:>5.0f} "
                f"{fmt_price(snap.close):>13} "
                f"{fmt_price(snap.atr) if snap.atr else 'n/a':>11} "
                f"{(snap.atr_pct or 0) * 100:>5.2f}% "
                f"{ext if ext is not None else float('nan'):>5.2f} "
                f"{snap.adx if snap.adx is not None else float('nan'):>5.1f} "
                f"{entry.group:<5} "
                f"{plan.units if plan else 0:>5} "
                f"{fmt_money(plan.total_notional) if plan else '-':>10} "
                f"{fmt_price(plan.stop) if plan else '-':>13} "
                f"{fmt_money(plan.total_risk) if plan else '-':>8}"
            )
            if not entry.accepted:
                row += paint(f"   [rejected: {entry.reject_reason}]", Color.GREY)
            print(row)

    accepted = report.accepted
    if accepted:
        print()
        print(paint("PORTFOLIO (Turtle risk limits applied)", Color.BOLD))
        for entry in accepted:
            plan = entry.plan
            assert plan is not None
            print(
                f"  {paint(entry.symbol, Color.BOLD):<22} {plan.describe()}"
            )
            if plan.pyramid_levels:
                ladder = ", ".join(fmt_price(p) for p in plan.pyramid_levels)
                print(
                    paint(
                        f"      pyramid adds at {ladder} "
                        f"(+{plan.pyramid_levels and ''}"
                        f"half-N steps), exit on close below "
                        f"{fmt_price(plan.exit_level) if plan.exit_level else 'n/a'}",
                        Color.GREY,
                    )
                )
            if plan.notional_capped:
                print(
                    paint(
                        "      note: position capped by max_notional_pct "
                        "(N-based size exceeded it)",
                        Color.YELLOW,
                    )
                )

    if report.budget is not None:
        summary = report.budget.summary()
        print()
        print(
            paint(
                f"budget: {summary['total_units']} units "
                f"({summary['long_units']} long / {summary['short_units']} short)  "
                f"notional {fmt_money(summary['total_notional'])}  "
                f"risk {fmt_money(summary['total_risk'])} "
                f"({summary['total_risk'] / report.equity * 100:.2f}% of equity)  "
                f"rejected {summary['rejected']}",
                Color.CYAN,
            )
        )
        if report.budget.rejections:
            print(paint("  limit rejections:", Color.GREY))
            for symbol, reason in report.budget.rejections[:8]:
                print(paint(f"    {symbol:<12} {reason}", Color.GREY))

    if report.exit_signals:
        print()
        print(
            paint(
                f"EXIT SIGNALS ({len(report.exit_signals)}) "
                f"-- close below the 10-bar low / above the 10-bar high",
                Color.BOLD,
            )
        )
        for snap in report.exit_signals[:15]:
            side = "long" if snap.exit_long else "short"
            print(
                f"  {snap.symbol:<12} {fmt_price(snap.close):>13}  "
                f"{paint(f'exit {side}', Color.YELLOW)}  "
                f"10-bar low {fmt_price(snap.exit_low_10)}  "
                f"ADX {snap.adx:.0f}" if snap.adx is not None else ""
            )
    print()


def report_to_dict(report: SurveyReport) -> Dict[str, object]:
    return {
        "generated_ms": report.generated_ms,
        "generated_utc": iso_utc(report.generated_ms),
        "market": "spot",
        "timeframes": report.timeframes,
        "equity": report.equity,
        "symbols_scanned": report.symbols_scanned,
        "symbols_with_data": report.symbols_with_data,
        "budget": report.budget.summary() if report.budget else {},
        "clusters": report.clusters,
        "setups": [
            {
                **entry.snapshot.to_row(),
                "score": round(entry.score.score, 1),
                "components": entry.score.components,
                "reasons": entry.score.reasons,
                "group": entry.group,
                "accepted": entry.accepted,
                "reject_reason": entry.reject_reason,
                "plan": (
                    {
                        "entry": entry.plan.entry,
                        "stop": entry.plan.stop,
                        "unit_size": entry.plan.unit_size,
                        "unit_notional": entry.plan.unit_notional,
                        "units": entry.plan.units,
                        "total_notional": entry.plan.total_notional,
                        "total_risk": entry.plan.total_risk,
                        "pyramid_levels": entry.plan.pyramid_levels,
                        "exit_level": entry.plan.exit_level,
                        "notional_capped": entry.plan.notional_capped,
                    }
                    if entry.plan
                    else None
                ),
            }
            for entry in report.ranked
        ],
        "exit_signals": [snap.to_row() for snap in report.exit_signals],
    }


def write_report(report: SurveyReport, json_path: Optional[str], csv_path: Optional[str]) -> None:
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report_to_dict(report), indent=2, default=str),
            encoding="utf-8",
        )
    if csv_path:
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = report_to_dict(report)["setups"]
        columns = [
            "symbol", "timeframe", "score", "close", "atr", "atr_pct", "atr_rank",
            "upper_20", "upper_55", "lower_20", "lower_55", "extension_up_atr",
            "bars_since_breakout", "adx", "plus_di", "minus_di", "rsi",
            "quote_volume_24h", "group", "accepted", "reject_reason",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in columns})
