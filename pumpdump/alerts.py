"""Alert delivery: console, JSONL, CSV, webhook, and desktop notifications.

Console and file writes happen inline (they are fast and local). Webhook and
desktop delivery are pushed onto a background worker so a slow or hanging HTTP
endpoint can never stall the scan loop.
"""

from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .common import LOG, Color, clock_utc, fmt_money, fmt_pct, fmt_price, now_ms
from .config import Config
from .onset import Event, passes_severity

# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

_KIND_STYLE = {
    "PUMP": ("PUMP", "▲", Color.BRIGHT_GREEN),
    "DUMP": ("DUMP", "▼", Color.BRIGHT_RED),
    "REVERSAL_DUMP": ("REV-DUMP", "↘", Color.RED),
    "REVERSAL_PUMP": ("REV-PUMP", "↗", Color.GREEN),
}

_SEVERITY_STYLE = {
    "CRITICAL": (Color.BOLD + Color.BRIGHT_YELLOW, Color.BG_RED),
    "ALERT": (Color.BRIGHT_YELLOW, ""),
    "WATCH": (Color.CYAN, ""),
}


class ConsoleSink:
    """Human-readable, colour-coded alerts."""

    def __init__(self, stream=None, color: bool = True) -> None:
        self.stream = stream or sys.stdout
        self._lock = threading.Lock()
        if not color:
            Color.enabled = False

    def emit(self, event: Event) -> None:
        label, arrow, kind_color = _KIND_STYLE.get(
            event.kind, (event.kind, "*", Color.CYAN)
        )
        sev_fg, sev_bg = _SEVERITY_STYLE.get(event.severity, ("", ""))
        metrics = event.metrics

        window = metrics.get("window_sec", 10)
        move = metrics.get("change_10s") or 0.0
        flow = metrics.get("flow_quote") or 0.0
        burst = metrics.get("burst_multiple") or 0.0
        quote_vol = metrics.get("quote_volume_24h") or 0.0

        head = (
            f"{Color.paint(clock_utc(event.ts_ms), Color.GREY)}  "
            f"{Color.paint(arrow + ' ' + label, kind_color, Color.BOLD)}  "
            f"{Color.paint(f'[{event.severity} {event.score:.0f}]', sev_fg, sev_bg)}  "
            f"{Color.paint(event.symbol, Color.BOLD)}  "
            f"{Color.paint(fmt_price(event.price), Color.BOLD)}  "
            f"{Color.paint(fmt_pct(move), kind_color)}/{window:.0f}s  "
            f"{Color.paint('flow ' + fmt_money(flow), Color.CYAN)} "
            f"({burst:.1f}x, 24h {fmt_money(quote_vol)})"
        )

        lines = [head]
        for reason in event.reasons:
            lines.append(f"          {Color.paint('· ' + reason, Color.GREY)}")
        comp = event.components
        if comp:
            parts = "  ".join(f"{k}={v:g}" for k, v in sorted(comp.items()))
            lines.append(
                f"          {Color.paint('score: ' + parts, Color.GREY, Color.DIM)}"
            )

        with self._lock:
            self.stream.write("\n".join(lines) + "\n")
            self.stream.flush()


class NullSink:
    def emit(self, event: Event) -> None:  # noqa: D102
        return


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

#: Stable CSV column order so a spreadsheet can be opened without re-mapping.
CSV_COLUMNS: List[str] = [
    "event_id", "ts_ms", "ts_utc", "market", "symbol", "kind", "severity", "score",
    "price", "window_sec", "flow_quote", "burst_multiple", "burst_z", "change_10s",
    "change_30s", "change_60s", "sigma_10s", "sigma_multiple", "acceleration",
    "coverage", "trades_10s", "trades_multiple", "quote_volume_24h", "book_depth_usd",
    "spread_pct", "range_position", "agg_buy_ratio", "agg_buy_quote", "agg_sell_quote",
    "agg_trades", "agg_largest_trade_usd", "agg_confirmed", "latency_ms", "penalty",
    "score_flow", "score_speed", "score_aggression", "score_quality",
    "retrace", "elapsed_min", "extreme_price", "origin_kind", "reasons",
]


class JsonlSink:
    """Append-only newline-delimited JSON -- the machine-readable audit trail."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        line = json.dumps(event.to_row(), separators=(",", ":"), default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class CsvSink:
    """Append-only CSV with a header, for spreadsheet analysis."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._has_header = self.path.exists() and self.path.stat().st_size > 0

    def emit(self, event: Event) -> None:
        row = event.to_row()
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=CSV_COLUMNS, extrasaction="ignore"
                )
                if not self._has_header:
                    writer.writeheader()
                    self._has_header = True
                writer.writerow({key: row.get(key, "") for key in CSV_COLUMNS})


# --------------------------------------------------------------------------
# Remote / desktop (queued)
# --------------------------------------------------------------------------

def _build_webhook_payload(event: Event, url: str) -> Dict[str, Any]:
    """Shape the payload for the endpoint flavour implied by ``url``."""
    metrics = event.metrics
    move = metrics.get("change_10s") or 0.0
    title = f"{event.kind} {event.symbol} [{event.severity} {event.score:.0f}]"
    summary = (
        f"{fmt_pct(move)}/{metrics.get('window_sec', 10):.0f}s, "
        f"flow {fmt_money(metrics.get('flow_quote') or 0)} "
        f"({metrics.get('burst_multiple') or 0:.1f}x baseline), "
        f"price {fmt_price(event.price)}"
    )
    body = summary + "\n" + "\n".join(f"- {r}" for r in event.reasons)

    if "discord" in url:
        colour = 0x2ECC71 if event.direction > 0 else 0xE74C3C
        return {
            "embeds": [
                {
                    "title": title,
                    "description": body[:3900],
                    "color": colour,
                    "footer": {"text": "binance-pumpdump"},
                }
            ]
        }
    if "slack" in url:
        return {"text": f"*{title}*\n{body}"}
    if "telegram" in url or "api.telegram.org" in url:
        chat_id = os.environ.get("PUMPDUMP_TELEGRAM_CHAT_ID", "")
        return {"chat_id": chat_id, "text": f"{title}\n{body}"}
    return {**event.to_row(), "title": title, "summary": summary}


class WebhookSink:
    """POST alerts to Discord/Slack/Telegram/generic endpoints, off-thread."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout
        self._queue: "queue.Queue[Optional[Event]]" = queue.Queue(maxsize=256)
        self._thread = threading.Thread(
            target=self._worker, name="webhook", daemon=True
        )
        self._thread.start()

    def emit(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            LOG.warning("webhook queue full; dropping alert for %s", event.symbol)

    def stop(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def _worker(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                return
            payload = _build_webhook_payload(event, self.url)
            data = json.dumps(payload, default=str).encode("utf-8")
            request = urllib.request.Request(
                self.url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "pumpdump/1.0"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout):
                    pass
            except urllib.error.HTTPError as exc:
                LOG.warning("webhook HTTP %s for %s", exc.code, event.symbol)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("webhook failed for %s: %s", event.symbol, exc)


class DesktopSink:
    """macOS notification banners, off-thread."""

    def __init__(self) -> None:
        self.enabled = sys.platform == "darwin"
        self._queue: "queue.Queue[Optional[Event]]" = queue.Queue(maxsize=64)
        if self.enabled:
            threading.Thread(target=self._worker, name="notify", daemon=True).start()

    def emit(self, event: Event) -> None:
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            pass

    def stop(self) -> None:
        if self.enabled:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass

    def _worker(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                return
            move = event.metrics.get("change_10s") or 0.0
            title = f"{event.kind} {event.symbol} ({event.severity} {event.score:.0f})"
            body = (
                f"{fmt_pct(move)} in {event.metrics.get('window_sec', 10):.0f}s, "
                f"flow {fmt_money(event.metrics.get('flow_quote') or 0)} "
                f"({event.metrics.get('burst_multiple') or 0:.1f}x)"
            )
            script = (
                f'display notification "{_osa_escape(body)}" '
                f'with title "{_osa_escape(title)}" sound name "Ping"'
            )
            try:
                subprocess.run(
                    ["osascript", "-e", script],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
            except Exception:  # noqa: BLE001
                pass


def _osa_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


# --------------------------------------------------------------------------
# Manager
# --------------------------------------------------------------------------

@dataclass
class AlertStats:
    events: int = 0
    suppressed_severity: int = 0
    suppressed_cooldown: int = 0
    by_kind: Dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_kind is None:
            self.by_kind = {}

    def record(self, event: Event) -> None:
        self.events += 1
        self.by_kind[event.kind] = self.by_kind.get(event.kind, 0) + 1


class AlertManager:
    """Fans an event out to every enabled sink."""

    def __init__(self, cfg: Config, *, console: bool = True) -> None:
        self.cfg = cfg
        self.stats = AlertStats()
        self.sinks: List[Any] = []
        self.console = ConsoleSink(color=not cfg.no_color) if console else NullSink()
        self.sinks.append(self.console)

        if cfg.alert_jsonl:
            self.sinks.append(JsonlSink(cfg.alert_jsonl))
        if cfg.alert_csv:
            self.sinks.append(CsvSink(cfg.alert_csv))
        if cfg.webhook_url:
            self.sinks.append(WebhookSink(cfg.webhook_url))
        if cfg.desktop_notify:
            self.sinks.append(DesktopSink())

    def emit(self, event: Event) -> bool:
        """Deliver an event. Returns False if it was filtered out."""
        if not passes_severity(event.severity, self.cfg.min_severity):
            self.stats.suppressed_severity += 1
            return False

        self.stats.record(event)
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception:  # noqa: BLE001 - one bad sink must not stop the rest
                LOG.exception("alert sink %s failed", type(sink).__name__)
        return True

    def close(self) -> None:
        for sink in self.sinks:
            stop = getattr(sink, "stop", None)
            if callable(stop):
                stop()

    def describe(self) -> str:
        names = [type(s).__name__ for s in self.sinks]
        return ", ".join(names) if names else "none"
