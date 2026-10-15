"""Async Binance combined-stream websocket client.

One connection carries every subscribed stream, which is what makes
whole-universe monitoring practical: Binance allows up to 1024 streams per
connection, so ~400 symbols of ``@ticker`` plus a handful of on-demand
``@aggTrade`` streams all share a single socket.

Features that matter for a long-running scanner:

* automatic reconnect with exponential backoff and full resubscription,
* *dynamic* subscribe/unsubscribe on a live socket, so we can escalate a
  single symbol to trade-level detail without reconnecting,
* a strict drain loop that never blocks on the consumer.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Set

from .common import LOG

SPOT_STREAM_HOST = "wss://stream.binance.com:9443"

#: Binance rejects connections above 1024 streams; stay clear of the edge.
MAX_STREAMS_PER_CONNECTION = 1000

MessageHandler = Callable[[dict], None]


@dataclass
class StreamStats:
    connected: int = 0
    reconnects: int = 0
    messages: int = 0
    bytes_in: int = 0
    last_message_ms: int = 0
    subscribed: Set[str] = field(default_factory=set)

    @property
    def stream_count(self) -> int:
        return len(self.subscribed)


class BinanceStream:
    """Combined-stream client with dynamic subscription management."""

    def __init__(
        self,
        on_message: MessageHandler,
        *,
        host: str = SPOT_STREAM_HOST,
        name: str = "spot",
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
        idle_warn_sec: float = 30.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.name = name
        self.on_message = on_message
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.idle_warn_sec = idle_warn_sec
        self.stats = StreamStats()

        self._desired: Set[str] = set()
        self._pending_sub: Set[str] = set()
        self._pending_unsub: Set[str] = set()
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._last_message_at: float = 0.0

    # ------------------------------------------------------------- control

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return not self._stop.is_set()

    def set_desired_streams(self, streams: Iterable[str]) -> None:
        """Declare the full stream set; the diff is applied on the live socket."""
        wanted = set(streams)
        if len(wanted) > MAX_STREAMS_PER_CONNECTION:
            LOG.warning(
                "%s: %d streams requested, truncating to %d (Binance limit)",
                self.name, len(wanted), MAX_STREAMS_PER_CONNECTION,
            )
            wanted = set(sorted(wanted)[:MAX_STREAMS_PER_CONNECTION])
        self._desired = wanted
        self._pending_sub = wanted - self.stats.subscribed
        self._pending_unsub = self.stats.subscribed - wanted
        if self._pending_sub or self._pending_unsub:
            LOG.debug(
                "%s: stream diff +%d -%d",
                self.name, len(self._pending_sub), len(self._pending_unsub),
            )

    def subscribe(self, streams: Iterable[str]) -> None:
        new = set(streams)
        self._desired |= new
        self._pending_sub |= new - self.stats.subscribed

    def unsubscribe(self, streams: Iterable[str]) -> None:
        gone = set(streams)
        self._desired -= gone
        self._pending_unsub |= gone & self.stats.subscribed

    async def wait_connected(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------ main loop

    async def run(self) -> None:
        """Connect, stream, and reconnect until :meth:`stop` is called."""
        import websockets
        from .common import now_ms

        backoff = 1.0
        while self.running:
            url = self._build_url()
            try:
                async with websockets.connect(
                    url,
                    open_timeout=20,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                    max_queue=2048,
                ) as ws:
                    self._ws = ws
                    self.stats.connected += 1
                    # A fresh socket starts with whatever was in the URL; the
                    # server confirms that, so seed our view from the URL too.
                    self.stats.subscribed = set(self._url_streams)
                    self._pending_sub = self._desired - self.stats.subscribed
                    self._pending_unsub = set()
                    self._connected.set()
                    backoff = 1.0
                    self._last_message_at = asyncio.get_event_loop().time()
                    LOG.info(
                        "%s stream connected: %d streams",
                        self.name, len(self.stats.subscribed),
                    )

                    watchdog = asyncio.create_task(self._watchdog())
                    try:
                        while self.running:
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            except asyncio.TimeoutError:
                                await self._flush_subscriptions()
                                continue
                            self._apply(raw)
                    finally:
                        watchdog.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                self._connected.clear()
                self.stats.reconnects += 1
                delay = min(60.0, backoff) * (0.7 + random.random() * 0.6)
                LOG.warning(
                    "%s stream error (%s: %s); reconnecting in %.1fs",
                    self.name, type(exc).__name__, exc, delay,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)
            else:
                self._connected.clear()
        self._connected.clear()
        LOG.info("%s stream stopped", self.name)

    # ----------------------------------------------------------- internals

    @property
    def _url_streams(self) -> List[str]:
        return sorted(self._desired)[:MAX_STREAMS_PER_CONNECTION]

    def _build_url(self) -> str:
        """Combined-stream URL. Empty set -> a benign placeholder stream."""
        streams = self._url_streams or ["btcusdt@ticker"]
        return f"{self.host}/stream?streams={'/'.join(streams)}"

    def _apply(self, raw: str) -> None:
        self.stats.messages += 1
        self.stats.bytes_in += len(raw)
        self._last_message_at = asyncio.get_event_loop().time()
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            return
        # Combined frames look like {"stream": "...", "data": {...}}.
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if data is None:
            data = envelope
        if isinstance(data, dict):
            try:
                self.on_message(data)
            except Exception:  # noqa: BLE001 - a bad handler must not kill the feed
                LOG.exception("%s handler error", self.name)

    async def _flush_subscriptions(self) -> None:
        if not self._pending_sub and not self._pending_unsub:
            return
        if self._ws is None:
            return
        async with self._send_lock:
            if self._pending_sub:
                batch = sorted(self._pending_sub)
                await self._send("SUBSCRIBE", batch)
                self.stats.subscribed |= set(batch)
                self._pending_sub = set()
                LOG.debug("%s subscribed %d streams", self.name, len(batch))
            if self._pending_unsub:
                batch = sorted(self._pending_unsub)
                await self._send("UNSUBSCRIBE", batch)
                self.stats.subscribed -= set(batch)
                self._pending_unsub = set()

    async def _send(self, method: str, params: List[str]) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"method": method, "params": params, "id": 1}))
        except Exception as exc:  # noqa: BLE001
            LOG.debug("%s %s failed: %s", self.name, method, exc)

    async def _watchdog(self) -> None:
        """Detect a silent socket (connected but no data) and force a reconnect."""
        while self.running:
            await asyncio.sleep(5.0)
            idle = asyncio.get_event_loop().time() - self._last_message_at
            if idle > self.idle_warn_sec:
                LOG.warning(
                    "%s stream idle for %.0fs; forcing reconnect", self.name, idle
                )
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                return


def ticker_stream(symbol: str) -> str:
    return f"{symbol.lower()}@ticker"


def mini_ticker_stream(symbol: str) -> str:
    return f"{symbol.lower()}@miniTicker"


def agg_trade_stream(symbol: str) -> str:
    return f"{symbol.lower()}@aggTrade"
