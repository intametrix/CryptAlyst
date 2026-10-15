"""Cooldowns and post-event reversal tracking.

Two jobs:

1. **De-duplication.** A developing pump trips the trigger on dozens of
   consecutive polls. ``Cooldowns`` collapses that into one alert per symbol
   and direction per ``cooldown_sec``.

2. **Cycle completion.** A pump-and-dump is only *tracked* if we also catch
   the dump. Every alerted pump is registered as a ``Watch`` holding its peak
   price; if price retraces ``reversal_drop_pct`` from that peak inside the
   watch window, the reversal leg fires. Dumps are mirrored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .common import LOG, now_ms, pct_change, safe_div


class Cooldowns:
    """Per-key alert suppression."""

    def __init__(self, default_sec: float = 900.0) -> None:
        self.default_sec = default_sec
        self._last: Dict[Tuple[str, ...], int] = {}

    def is_clear(self, *key: str, now: Optional[int] = None, window_sec: Optional[float] = None) -> bool:
        stamp = now if now is not None else now_ms()
        window = (self.default_sec if window_sec is None else window_sec) * 1000
        last = self._last.get(tuple(key))
        return last is None or (stamp - last) >= window

    def mark(self, *key: str, now: Optional[int] = None) -> None:
        self._last[tuple(key)] = now if now is not None else now_ms()

    def remaining_sec(self, *key: str, now: Optional[int] = None) -> float:
        stamp = now if now is not None else now_ms()
        last = self._last.get(tuple(key))
        if last is None:
            return 0.0
        return max(0.0, (last + self.default_sec * 1000 - stamp) / 1000.0)

    def clear(self) -> None:
        self._last.clear()


@dataclass
class Watch:
    """An alerted move whose reversal leg we are still waiting for."""

    market: str
    symbol: str
    origin_kind: str          # PUMP or DUMP (what we alerted first)
    origin_price: float
    started_ms: int
    expires_ms: int
    extreme_price: float      # running peak (after a pump) or trough (after a dump)
    origin_score: float = 0.0
    origin_severity: str = "WATCH"
    reversal_fired: bool = False
    reversal_count: int = 0
    last_reversal_ms: int = 0

    @property
    def direction(self) -> int:
        """Direction of the *reversal* leg: -1 for a post-pump dump."""
        return -1 if self.origin_kind == "PUMP" else 1

    @property
    def key(self) -> Tuple[str, str]:
        return (self.market, self.symbol)

    def observe(self, price: float) -> None:
        """Track the running extreme in the direction of the original move."""
        if self.origin_kind == "PUMP":
            self.extreme_price = max(self.extreme_price, price)
        else:
            self.extreme_price = min(self.extreme_price, price)

    def retrace(self, price: float) -> Optional[float]:
        """Signed retrace from the extreme, in the reversal leg's direction.

        Returns a *negative* fraction for a post-pump dump so callers can read
        it as "price is down X%", and positive for a post-dump squeeze.
        """
        if not self.extreme_price:
            return None
        raw = pct_change(price, self.extreme_price)
        if raw is None:
            return None
        return raw  # already signed by price direction

    def elapsed_min(self, now: Optional[int] = None) -> float:
        stamp = now if now is not None else now_ms()
        return max(0.0, (stamp - self.started_ms) / 60000.0)

    def is_expired(self, now: Optional[int] = None) -> bool:
        stamp = now if now is not None else now_ms()
        return stamp >= self.expires_ms


class WatchList:
    """Registry of symbols awaiting a reversal leg."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._watches: Dict[Tuple[str, str], Watch] = {}

    def register(
        self,
        *,
        market: str,
        symbol: str,
        origin_kind: str,
        price: float,
        score: float,
        severity: str,
        now: Optional[int] = None,
    ) -> Watch:
        stamp = now if now is not None else now_ms()
        window_ms = int(self.cfg.watch_window_min * 60_000)
        watch = Watch(
            market=market,
            symbol=symbol,
            origin_kind=origin_kind,
            origin_price=price,
            started_ms=stamp,
            expires_ms=stamp + window_ms,
            extreme_price=price,
            origin_score=score,
            origin_severity=severity,
        )
        self._watches[watch.key] = watch
        return watch

    def observe(self, market: str, symbol: str, price: float) -> Optional[Watch]:
        """Update the running extreme; drop the watch once it expires."""
        watch = self._watches.get((market, symbol))
        if watch is None:
            return None
        if watch.is_expired():
            del self._watches[(market, symbol)]
            return None
        watch.observe(price)
        return watch

    def pending(self, only_unfired: bool = True) -> List[Watch]:
        out = []
        for watch in self._watches.values():
            if watch.is_expired():
                continue
            if only_unfired and watch.reversal_fired:
                continue
            out.append(watch)
        return out

    def get(self, market: str, symbol: str) -> Optional[Watch]:
        return self._watches.get((market, symbol))

    def mark_reversal(self, watch: Watch, now: Optional[int] = None) -> None:
        stamp = now if now is not None else now_ms()
        watch.reversal_fired = True
        watch.reversal_count += 1
        watch.last_reversal_ms = stamp

    def expiry_ok(self, watch: Watch, now: Optional[int] = None) -> bool:
        """True when a repeat reversal alert is outside its own cooldown."""
        stamp = now if now is not None else now_ms()
        if watch.reversal_count == 0:
            return True
        elapsed = (stamp - watch.last_reversal_ms) / 1000.0
        return elapsed >= self.cfg.reversal_cooldown_sec

    def sweep(self, now: Optional[int] = None) -> int:
        """Drop expired watches; returns how many were removed."""
        stamp = now if now is not None else now_ms()
        dead = [key for key, watch in self._watches.items() if watch.is_expired(stamp)]
        for key in dead:
            del self._watches[key]
        return len(dead)

    def __len__(self) -> int:
        return len(self._watches)

    def stats(self) -> Dict[str, int]:
        total = len(self._watches)
        fired = sum(1 for w in self._watches.values() if w.reversal_fired)
        return {"watches": total, "reversals_fired": fired}
