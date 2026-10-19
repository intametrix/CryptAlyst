"""Scanner orchestration: streams in, alerts out.

Runtime shape (asyncio, one process, one socket per market):

    BinanceStream(@ticker xN)  ->  FlowEngine          ->  onset detection
                                        |                      |
                                        |                      v
                                        |            Cooldowns + WatchList
                                        |                      |
    BinanceStream(@aggTrade xK) ->  AggTradeTracker  ->  AlertManager

Loops:

* ``_context_loop``   -- refresh the universe, resize the stream subscription.
* ``_scan_loop``      -- run the onset scan on the evaluation tick.
* ``_pending_loop``   -- resolve candidates waiting on trade-level confirmation.
* ``_reversal_loop``  -- look for the second leg of an alerted move.
* ``_rebaseline_loop``-- refresh flow baselines from fresh 1s klines.
* ``_status_loop``    -- heartbeat so you can tell it is still alive.
"""

from __future__ import annotations

import asyncio
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .alerts import AlertManager
from .common import LOG, Color, clock_utc, fmt_money, fmt_pct, now_ms
from .config import Config, MarketSpec
from .flow import FlowEngine, OnsetCandidate
from .klines import parse_klines
from .onset import (
    PUMP,
    DUMP,
    AggressionSnapshot,
    Event,
    build_onset_event,
    build_reversal_event,
)
from .rest import BinanceError, BinanceREST
from .stream import BinanceStream, agg_trade_stream, ticker_stream
from .tracker import Cooldowns, WatchList
from .universe import Universe


# --------------------------------------------------------------------------
# Trade-level aggression tracking (@aggTrade escalation)
# --------------------------------------------------------------------------

@dataclass
class HotSymbol:
    """A symbol temporarily escalated to trade-level data."""

    symbol: str
    market: str
    subscribed_ms: int
    expires_ms: int
    trades: List[Tuple[int, float, float]] = field(default_factory=list)
    # (timestamp_ms, signed_quote, quote) where signed is +buy / -sell
    total_quote: float = 0.0
    largest_trade_usd: float = 0.0

    def add(self, ts: int, quote: float, is_buyer_maker: bool) -> None:
        """``m`` from Binance: True means the *buyer* was the maker, i.e. a SELL."""
        signed = -quote if is_buyer_maker else quote
        self.trades.append((ts, signed, quote))
        self.total_quote += quote
        if quote > self.largest_trade_usd:
            self.largest_trade_usd = quote
        # Keep a bounded history; the snapshot window is far shorter than this.
        if len(self.trades) > 4000:
            del self.trades[:2000]

    def prune(self, cutoff_ms: int) -> None:
        if not self.trades:
            return
        keep_from = 0
        for index, (ts, _, _) in enumerate(self.trades):
            if ts >= cutoff_ms:
                keep_from = index
                break
        else:
            keep_from = len(self.trades)
        if keep_from:
            del self.trades[:keep_from]

    def snapshot(self, window_sec: float, now: Optional[int] = None) -> AggressionSnapshot:
        stamp = now if now is not None else now_ms()
        cutoff = stamp - int(window_sec * 1000)
        window = [t for t in self.trades if t[0] >= cutoff]
        buy = sum(quote for _, signed, quote in window if signed > 0)
        sell = sum(quote for _, signed, quote in window if signed < 0)
        return AggressionSnapshot(
            symbol=self.symbol,
            buy_quote=buy,
            sell_quote=sell,
            trades=len(window),
            first_ts=window[0][0] if window else stamp,
            last_ts=window[-1][0] if window else stamp,
            largest_trade_usd=self.largest_trade_usd,
        )


class AggTradeTracker:
    """Dynamically escalates a bounded set of symbols to ``@aggTrade``."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.hot: Dict[Tuple[str, str], HotSymbol] = {}
        self.snapshot_window_sec = max(cfg.flow_window_sec * 2.0, 15.0)

    def key(self, market: str, symbol: str) -> Tuple[str, str]:
        return (market, symbol)

    @property
    def at_capacity(self) -> bool:
        return len(self.hot) >= self.cfg.agg_max_symbols

    def is_hot(self, market: str, symbol: str) -> bool:
        return self.key(market, symbol) in self.hot

    def register(self, market: str, symbol: str) -> Optional[HotSymbol]:
        key = self.key(market, symbol)
        existing = self.hot.get(key)
        if existing is not None:
            existing.expires_ms = now_ms() + int(self.cfg.agg_trade_ttl_sec * 1000)
            return existing
        if self.at_capacity:
            return None
        stamp = now_ms()
        hot = HotSymbol(
            symbol=symbol,
            market=market,
            subscribed_ms=stamp,
            expires_ms=stamp + int(self.cfg.agg_trade_ttl_sec * 1000),
        )
        self.hot[key] = hot
        return hot

    def ingest(self, msg: dict) -> None:
        symbol = msg.get("s")
        if not isinstance(symbol, str):
            return
        hot = self.hot.get(self.key("spot", symbol)) or self.hot.get(("futures", symbol))
        if hot is None:
            return
        try:
            price = float(msg["p"])
            qty = float(msg["q"])
        except (KeyError, TypeError, ValueError):
            return
        hot.add(int(msg.get("T") or now_ms()), price * qty, bool(msg.get("m", False)))

    def snapshot(self, market: str, symbol: str) -> Optional[AggressionSnapshot]:
        hot = self.hot.get(self.key(market, symbol))
        if hot is None:
            return None
        hot.prune(now_ms() - int(self.snapshot_window_sec * 2000))
        return hot.snapshot(self.snapshot_window_sec)

    def warmup_elapsed(self, market: str, symbol: str) -> float:
        hot = self.hot.get(self.key(market, symbol))
        if hot is None:
            return 0.0
        return (now_ms() - hot.subscribed_ms) / 1000.0

    def expire(self) -> List[Tuple[str, str]]:
        """Drop stale hot symbols; returns ``(market, symbol)`` to unsubscribe."""
        stamp = now_ms()
        dead = [key for key, hot in self.hot.items() if hot.expires_ms <= stamp]
        for key in dead:
            del self.hot[key]
        return dead


# --------------------------------------------------------------------------
# Pending confirmation
# --------------------------------------------------------------------------

@dataclass
class PendingCandidate:
    """A burst waiting for trade-level evidence before it is alerted."""

    candidate: OnsetCandidate
    created_ms: int
    expires_ms: int
    latency_ms: int = 0


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------

class Scanner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.alerts = AlertManager(cfg)
        self.cooldowns = Cooldowns(cfg.cooldown_sec)
        self.watches = WatchList(cfg)
        self.executor = ThreadPoolExecutor(
            max_workers=max(2, cfg.max_workers), thread_name_prefix="rest"
        )

        self.specs: List[MarketSpec] = cfg.market_specs()
        self.clients: Dict[str, BinanceREST] = {
            spec.name: BinanceREST(
                spec,
                api_key=cfg.api_key,
                api_secret=cfg.api_secret,
                timeout=cfg.http_timeout_sec,
            )
            for spec in self.specs
        }
        self.universes: Dict[str, Universe] = {
            spec.name: Universe(spec, cfg) for spec in self.specs
        }
        self.flows: Dict[str, FlowEngine] = {
            spec.name: FlowEngine(
                market=spec.name,
                window_sec=cfg.flow_window_sec,
                min_burst_multiple=cfg.min_burst_multiple,
                min_burst_z=cfg.min_burst_z,
                min_abs_flow=cfg.min_abs_flow_usd,
                min_move_pct_10s=cfg.min_move_pct_10s,
                min_sigma_multiple=cfg.min_sigma_multiple,
                min_coverage=cfg.min_coverage,
                max_spread_pct=cfg.max_spread_pct,
                min_book_depth_usd=cfg.min_book_depth_usd,
                max_late_pct_60s=cfg.max_move_pct_60s,
                require_acceleration=cfg.require_acceleration,
                max_24h_move_pct=cfg.max_24h_move_pct,
                min_trades_10s=cfg.min_trades_10s,
                baseline_floor_fraction=cfg.baseline_floor_fraction,
            )
            for spec in self.specs
        }
        self.streams: Dict[str, BinanceStream] = {
            spec.name: BinanceStream(
                self._make_handler(spec.name), name=spec.name
            )
            for spec in self.specs
        }
        self.agg = AggTradeTracker(cfg)
        self.pending: Dict[Tuple[str, str], PendingCandidate] = {}

        self.started_ms = now_ms()
        self.scans = 0
        self.candidates_seen = 0
        self._stop = asyncio.Event()
        self._tasks: List[asyncio.Task] = []

    # ------------------------------------------------------------- handlers

    def _make_handler(self, market: str):
        flows = self.flows[market]

        def handle(msg: dict) -> None:
            event_type = msg.get("e")
            if event_type == "24hrTicker":
                flows.ingest_ticker(msg)
            elif event_type == "aggTrade":
                self.agg.ingest(msg)
            elif event_type == "24hrMiniTicker":
                flows.ingest_ticker(msg)

        return handle

    # ------------------------------------------------------------ bootstrap

    async def bootstrap(self) -> bool:
        """Load metadata, build the universe, and seed flow baselines."""
        loop = asyncio.get_running_loop()
        ok = False
        for name, client in self.clients.items():
            universe = self.universes[name]
            try:
                await loop.run_in_executor(self.executor, client.ping)
            except BinanceError as exc:
                LOG.error("%s unreachable: %s", name, exc)
                continue
            meta_count = await loop.run_in_executor(
                self.executor, universe.load_metadata, client
            )
            ctx_count = await loop.run_in_executor(
                self.executor, universe.refresh_context, client
            )
            LOG.info("%s: %s", name, universe.summary() if meta_count else "no metadata")
            ok = ok or (meta_count > 0 and ctx_count > 0)
            if meta_count and ctx_count:
                self._apply_universe(name)
        if not ok:
            LOG.error("no usable market data; check network/geo restrictions")
            return False

        await self.seed_baselines()
        return True

    def _apply_universe(self, market: str) -> None:
        universe = self.universes[market]
        flows = self.flows[market]
        flows.set_universe(universe.tracked)
        streams = [ticker_stream(sym) for sym in universe.tracked]
        self.streams[market].set_desired_streams(streams)
        LOG.info(
            "%s: tracking %d symbols, %d baseline-ready",
            market, len(universe.tracked), flows.ready_count,
        )

    async def seed_baselines(self, symbols: Optional[Sequence[Tuple[str, str]]] = None) -> int:
        """Seed flow baselines from 1s klines so detection works immediately.

        Without this the scanner needs ~80s of live ticks before any symbol has
        a usable baseline, which would mean missing the first minutes of a move
        right after startup.
        """
        loop = asyncio.get_running_loop()
        if symbols is None:
            symbols = []
            for name in self.flows:
                universe = self.universes[name]
                for ctx in universe.top_by_volume(self.cfg.bootstrap_top_n):
                    symbols.append((name, ctx.symbol))

        if not symbols:
            return 0

        total = len(symbols)
        LOG.info(
            "seeding flow baselines from %s klines for %d symbols...",
            self.cfg.bootstrap_interval, total,
        )
        semaphore = asyncio.Semaphore(max(1, self.cfg.bootstrap_workers))
        done = 0
        seeded = 0
        started = time.time()

        async def seed_one(market: str, symbol: str) -> bool:
            nonlocal done, seeded
            async with semaphore:
                client = self.clients[market]
                try:
                    rows = await loop.run_in_executor(
                        self.executor,
                        client.klines,
                        symbol,
                        self.cfg.bootstrap_interval,
                        self.cfg.bootstrap_kline_limit,
                    )
                except BinanceError as exc:
                    LOG.debug("baseline seed failed for %s: %s", symbol, exc)
                    return False
            bars = parse_klines(rows)
            if not bars:
                return False
            # Prime the 24h volume before seeding so the liquidity floor is
            # correct from the very first scan. Without this the floor is zero
            # (no live tick has arrived yet) and a symbol whose 1s klines are
            # mostly empty gets a near-zero baseline reference -- which turns a
            # routine $18K of flow into a reported "1145x burst".
            ctx = self.universes[market].get(symbol)
            if ctx is not None:
                self.flows[market].ensure(symbol).quote_volume_24h = ctx.quote_volume
            count = self.flows[market].seed(symbol, bars)
            seeded += 1 if count else 0
            done += 1
            if done % 50 == 0 or done == total:
                LOG.info(
                    "  baseline seeding: %d/%d symbols (%d ready) in %.0fs",
                    done, total, seeded, time.time() - started,
                )
            return True

        await asyncio.gather(*(seed_one(m, s) for m, s in symbols))
        LOG.info(
            "baseline seeding complete: %d/%d symbols seeded in %.0fs",
            seeded, total, time.time() - started,
        )
        return seeded

    # ------------------------------------------------------------- scanning

    async def _scan_loop(self) -> None:
        """Run the onset scan on every evaluation tick."""
        while not self._stop.is_set():
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                LOG.exception("scan iteration failed")
            await self._sleep(self.cfg.scan_interval_sec)

    async def _scan_once(self) -> None:
        self.scans += 1
        stamp = now_ms()
        for market, flows in self.flows.items():
            # Re-derive the liquidity floor from the freshest 24h volume so a
            # symbol that was quiet at startup cannot produce a huge multiple
            # off a stale, near-zero reference.
            flows.apply_floors()
            candidates = flows.detect(stamp)
            self.candidates_seen += len(candidates)
            for candidate in candidates:
                self._handle_candidate(market, candidate)

    def _handle_candidate(self, market: str, candidate: OnsetCandidate) -> None:
        kind = PUMP if candidate.direction > 0 else DUMP
        key = (market, candidate.symbol)

        if not self.cooldowns.is_clear(market, candidate.symbol, kind):
            self.alerts.stats.suppressed_cooldown += 1
            return

        if self.cfg.confirm_with_agg_trades:
            if not self.agg.is_hot(market, candidate.symbol):
                hot = self.agg.register(market, candidate.symbol)
                if hot is not None:
                    self.streams[market].subscribe(
                        [agg_trade_stream(candidate.symbol)]
                    )
                    LOG.debug("escalated %s to @aggTrade", candidate.symbol)
                    # Cold subscription: come back once trades have accumulated.
                    self.pending[key] = PendingCandidate(
                        candidate=candidate,
                        created_ms=stamp_now(),
                        expires_ms=stamp_now()
                        + int(self.cfg.agg_trade_ttl_sec * 1000),
                    )
                    return
            else:
                # Already hot: judge now with the trades we have.
                if self.agg.warmup_elapsed(market, candidate.symbol) < self.cfg.agg_warmup_sec:
                    self.pending[key] = PendingCandidate(
                        candidate=candidate,
                        created_ms=stamp_now(),
                        expires_ms=stamp_now()
                        + int(self.cfg.agg_trade_ttl_sec * 1000),
                    )
                    return

        self._emit_candidate(market, candidate)

    def _emit_candidate(self, market: str, candidate: OnsetCandidate) -> None:
        aggression = (
            self.agg.snapshot(market, candidate.symbol)
            if self.cfg.confirm_with_agg_trades
            else None
        )
        latency = 0
        key = (market, candidate.symbol)
        pending = self.pending.pop(key, None)
        if pending is not None:
            latency = now_ms() - pending.created_ms
            if pending.candidate.flow_quote > candidate.flow_quote:
                # The burst we queued was larger than the current reading:
                # keep the stronger measurement.
                candidate = pending.candidate

        event = build_onset_event(
            candidate, self.cfg, aggression=aggression, latency_ms=latency
        )
        if event is None:
            return

        kind = event.kind
        self.cooldowns.mark(market, candidate.symbol, kind)
        if self.alerts.emit(event):
            self.watches.register(
                market=market,
                symbol=candidate.symbol,
                origin_kind=kind,
                price=candidate.price,
                score=event.score,
                severity=event.severity,
            )

    async def _pending_loop(self) -> None:
        """Resolve candidates that were waiting on trade-level confirmation."""
        while not self._stop.is_set():
            await self._sleep(1.0)
            if not self.pending:
                continue
            stamp = stamp_now()
            for key in list(self.pending):
                pending = self.pending[key]
                market, symbol = key
                if stamp > pending.expires_ms:
                    del self.pending[key]
                    continue
                if self.agg.warmup_elapsed(market, symbol) < self.cfg.agg_warmup_sec:
                    continue
                # Re-read the live flow state so the alert reflects "now".
                refreshed = self._refresh_candidate(market, symbol, pending.candidate)
                del self.pending[key]
                if refreshed is not None:
                    self._emit_candidate(market, refreshed)
                else:
                    self._emit_candidate(market, pending.candidate)

    def _refresh_candidate(
        self, market: str, symbol: str, fallback: OnsetCandidate
    ) -> Optional[OnsetCandidate]:
        flows = self.flows[market]
        state = flows.states.get(symbol)
        if state is None or state.last_tick is None:
            return None
        fresh = flows._evaluate(state, now_ms())
        if fresh is None:
            return None
        # If the burst has already faded, still report the one we caught but
        # keep the larger flow figure.
        return fresh if fresh.flow_quote >= fallback.flow_quote else fallback

    async def _reversal_loop(self) -> None:
        """Watch alerted symbols for the second leg of the move."""
        while not self._stop.is_set():
            await self._sleep(max(1.0, self.cfg.scan_interval_sec))
            if len(self.watches) == 0:
                continue
            self.watches.sweep()
            for watch in self.watches.pending(only_unfired=False):
                flows = self.flows.get(watch.market)
                if flows is None:
                    continue
                state = flows.states.get(watch.symbol)
                if state is None or state.last_tick is None:
                    continue
                price = state.last_price
                watch.observe(price)
                retrace = watch.retrace(price)
                if retrace is None:
                    continue
                direction = watch.direction
                # Only fire when the retrace is in the reversal's direction and
                # large enough to matter.
                if direction * retrace < self.cfg.reversal_drop_pct / 100.0:
                    continue
                if not self.watches.expiry_ok(watch):
                    continue
                if not self.cooldowns.is_clear(
                    watch.market, watch.symbol, "REVERSAL_" + watch.origin_kind,
                    window_sec=self.cfg.reversal_cooldown_sec,
                ):
                    continue

                flow, coverage = state.flow_quote(self.cfg.flow_window_sec)
                burst = state.baseline.multiple(flow)
                if burst < self.cfg.reversal_min_burst:
                    continue

                key = (watch.market, watch.symbol)
                if key not in self.agg.hot and not self.agg.at_capacity and self.cfg.confirm_with_agg_trades:
                    self.agg.register(watch.market, watch.symbol)
                    self.streams[watch.market].subscribe(
                        [agg_trade_stream(watch.symbol)]
                    )
                aggression = (
                    self.agg.snapshot(watch.market, watch.symbol)
                    if self.cfg.confirm_with_agg_trades
                    else None
                )
                event = build_reversal_event(
                    market=watch.market,
                    symbol=watch.symbol,
                    origin_kind=watch.origin_kind,
                    price=price,
                    retrace=retrace,
                    elapsed_min=watch.elapsed_min(),
                    extreme_price=watch.extreme_price,
                    flow_quote=flow,
                    burst_multiple=burst,
                    coverage=coverage,
                    quote_volume_24h=state.quote_volume_24h,
                    aggression=aggression,
                )
                self.watches.mark_reversal(watch)
                self.cooldowns.mark(
                    watch.market, watch.symbol, "REVERSAL_" + watch.origin_kind
                )
                self.alerts.emit(event)

    # ---------------------------------------------------------------- loops

    async def _context_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            await self._sleep(self.cfg.context_poll_sec)
            for market, client in self.clients.items():
                universe = self.universes[market]
                try:
                    await loop.run_in_executor(
                        self.executor, universe.refresh_context, client
                    )
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("%s context refresh failed: %s", market, exc)
                    continue
                self._apply_universe(market)

    async def _rebaseline_loop(self) -> None:
        if self.cfg.rebaseline_every_sec <= 0:
            return
        while not self._stop.is_set():
            await self._sleep(self.cfg.rebaseline_every_sec)
            for market in self.flows:
                universe = self.universes[market]
                pairs = [
                    (market, ctx.symbol)
                    for ctx in universe.top_by_volume(self.cfg.bootstrap_top_n)
                ]
                try:
                    await self.seed_baselines(pairs)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("%s re-baseline failed: %s", market, exc)

    async def _agg_expiry_loop(self) -> None:
        while not self._stop.is_set():
            await self._sleep(5.0)
            for market, symbol in self.agg.expire():
                self.streams[market].unsubscribe([agg_trade_stream(symbol)])

    async def _status_loop(self) -> None:
        if self.cfg.status_every_sec <= 0:
            return
        while not self._stop.is_set():
            await self._sleep(self.cfg.status_every_sec)
            self.print_status()

    def print_status(self) -> None:
        uptime = (now_ms() - self.started_ms) / 1000.0
        parts = []
        for market, flows in self.flows.items():
            client = self.clients[market]
            stream = self.streams[market]
            weight_pct = client.weight_pressure() * 100
            parts.append(
                f"{market}: {len(flows.states)} syms "
                f"({flows.ready_count} ready), streams={stream.stats.stream_count}, "
                f"msgs={stream.stats.messages}, rest_weight={weight_pct:.0f}%"
            )
        alerts = self.alerts.stats
        line = (
            f"[status] up {uptime / 60:.1f}m | " + " | ".join(parts)
            + f" | scans={self.scans} candidates={self.candidates_seen}"
            + f" alerts={alerts.events} hot={len(self.agg.hot)}"
            + f" watches={len(self.watches)}"
        )
        print(Color.paint(line, Color.GREY), flush=True)
        movers = []
        for market, flows in self.flows.items():
            for symbol, flow in flows.activity(3):
                movers.append((symbol, flow))
        movers.sort(key=lambda item: item[1], reverse=True)
        if movers:
            top = ", ".join(f"{s} {fmt_money(f)}" for s, f in movers[:5])
            print(Color.paint(f"          top {self.cfg.flow_window_sec:.0f}s flow: {top}", Color.GREY), flush=True)

    # ----------------------------------------------------------------- misc

    async def _sleep(self, seconds: float) -> None:
        """Sleep that wakes immediately on stop."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    def request_stop(self) -> None:
        self._stop.set()
        for stream in self.streams.values():
            stream.stop()

    async def run(self) -> int:
        if not await self.bootstrap():
            return 2

        LOG.info("config: %s", self.cfg.describe())
        LOG.info("alerts -> %s", self.alerts.describe())

        for stream in self.streams.values():
            self._tasks.append(asyncio.create_task(stream.run(), name=f"stream-{stream.name}"))
        for coro, name in (
            (self._scan_loop(), "scan"),
            (self._pending_loop(), "pending"),
            (self._reversal_loop(), "reversal"),
            (self._context_loop(), "context"),
            (self._rebaseline_loop(), "rebaseline"),
            (self._agg_expiry_loop(), "agg-expiry"),
            (self._status_loop(), "status"),
        ):
            self._tasks.append(asyncio.create_task(coro, name=name))

        connected = await self.streams[self.specs[0].name].wait_connected(30)
        if not connected:
            LOG.error("stream did not connect within 30s")
        else:
            LOG.info("scanning: window=%.0fs, tick=%.1fs", self.cfg.flow_window_sec, self.cfg.scan_interval_sec)

        if self.cfg.duration_sec > 0:
            await self._sleep(self.cfg.duration_sec)
            LOG.info("duration reached; shutting down")
        else:
            await self._stop.wait()

        self.request_stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.alerts.close()
        self.executor.shutdown(wait=False)
        self.print_status()
        return 0

    async def run_once(self, warmup_sec: float = 15.0) -> int:
        """Connect, collect a few seconds of flow, report, and exit.

        The warmup must exceed the flow window, otherwise the window is only
        partly covered by live ticks and every coverage gate rejects.
        """
        if not await self.bootstrap():
            return 2
        for stream in self.streams.values():
            self._tasks.append(asyncio.create_task(stream.run(), name=f"stream-{stream.name}"))

        primary = self.specs[0].name
        if not await self.streams[primary].wait_connected(30):
            LOG.error("stream did not connect within 30s")
            self.request_stop()
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            return 2

        LOG.info("collecting %.0fs of flow data...", warmup_sec)
        await asyncio.sleep(warmup_sec)

        flows = self.flows[primary]
        print()
        print(Color.paint(
            f"=== single sweep: {len(flows.states)} symbols, "
            f"{flows.ready_count} baseline-ready, "
            f"{flows.messages} ticks received ===",
            Color.BOLD,
        ))
        candidates = flows.detect()
        print(
            f"gates passed: {len(candidates)}  "
            f"(rejected for coverage: {flows.truncated})"
        )
        for candidate in candidates[:10]:
            print("  " + candidate.summary())
            for reason in candidate.reasons:
                print(f"      - {reason}")

        top = flows.activity(8)
        if top:
            print(Color.paint("\ntop current flow:", Color.GREY))
            for symbol, flow in top:
                state = flows.states[symbol]
                change = state.change_over(self.cfg.flow_window_sec) or 0.0
                print(
                    f"  {symbol:<12} {fmt_money(flow):>10} / "
                    f"{self.cfg.flow_window_sec:.0f}s  {fmt_pct(change)}  "
                    f"{state.baseline.multiple(flow):.1f}x baseline"
                )
        for market, engine in self.flows.items():
            client = self.clients[market]
            stream = self.streams[market]
            print(
                Color.paint(
                    f"\n{market}: messages={stream.stats.messages} "
                    f"reconnects={stream.stats.reconnects} "
                    f"rest_requests={client.stats.requests} "
                    f"rest_weight_peak={client.stats.peak_weight_used}/"
                    f"{client.weight_limit}",
                    Color.GREY,
                )
            )
        print()

        self.request_stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.alerts.close()
        self.executor.shutdown(wait=False)
        return 0


def stamp_now() -> int:
    return now_ms()


def install_signal_handlers(scanner: Scanner, loop: asyncio.AbstractEventLoop) -> None:
    """Ctrl-C / SIGTERM trigger a clean shutdown."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, scanner.request_stop)
        except (NotImplementedError, ValueError):
            pass
