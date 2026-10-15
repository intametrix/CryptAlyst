"""Market universe: symbol metadata, 24h context, and tracking selection."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .common import LOG, fmt_money, safe_div
from .config import Config, MarketSpec
from .rest import BinanceError, BinanceREST


@dataclass
class SymbolMeta:
    """Static-ish exchange metadata for one tradable symbol."""

    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    contract_type: str = ""
    onboard_ms: int = 0

    @property
    def is_perpetual(self) -> bool:
        return self.contract_type.upper() in ("PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER", "")


@dataclass
class SymbolContext:
    """Rolling 24h statistics, refreshed from the full-market ticker."""

    symbol: str
    market: str
    last_price: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    price_change_pct: float = 0.0
    quote_volume: float = 0.0
    base_volume: float = 0.0
    trades: int = 0
    updated_ms: int = 0

    @property
    def range_position(self) -> float:
        """Where price sits in the 24h range: 1 = at the 24h high."""
        span = self.high_price - self.low_price
        if span <= 0:
            return 0.5
        return max(0.0, min(1.0, (self.last_price - self.low_price) / span))

    @property
    def volume_per_minute(self) -> float:
        """Average quote volume per minute over the trailing 24h."""
        return self.quote_volume / 1440.0


class Universe:
    """Owns symbol metadata + 24h context for a single market."""

    def __init__(self, spec: MarketSpec, cfg: Config) -> None:
        self.spec = spec
        self.cfg = cfg
        self.meta: Dict[str, SymbolMeta] = {}
        self.context: Dict[str, SymbolContext] = {}
        self.tracked: List[str] = []
        self.last_refresh_ms: int = 0
        self.refresh_count: int = 0

    # ------------------------------------------------------------- metadata

    def load_metadata(self, client: BinanceREST) -> int:
        """Fetch ``exchangeInfo`` and index eligible symbols."""
        try:
            info = client.exchange_info()
        except BinanceError as exc:
            LOG.error("%s exchangeInfo failed: %s", self.spec.name, exc)
            return 0

        symbols = info.get("symbols", []) if isinstance(info, dict) else []
        allowed_quotes: Set[str] = set(self.cfg.quote_assets)
        keep: Dict[str, SymbolMeta] = {}

        for entry in symbols:
            symbol = entry.get("symbol")
            if not isinstance(symbol, str):
                continue
            quote = str(entry.get("quoteAsset", "")).upper()
            if quote not in allowed_quotes:
                continue
            status = str(entry.get("status", "")).upper()
            if status != "TRADING":
                continue
            contract = str(entry.get("contractType", "")).upper()
            if self.spec.is_futures and contract and contract not in (
                "PERPETUAL",
                "CURRENT_QUARTER",
                "NEXT_QUARTER",
            ):
                continue
            if self._excluded(symbol):
                continue
            keep[symbol] = SymbolMeta(
                symbol=symbol,
                base_asset=str(entry.get("baseAsset", "")),
                quote_asset=quote,
                status=status,
                contract_type=contract,
                onboard_ms=int(entry.get("onboardDate", 0) or 0),
            )

        self.meta = keep
        LOG.info(
            "%s universe: %d %s-quoted symbols from exchangeInfo",
            self.spec.name,
            len(keep),
            "/".join(sorted(allowed_quotes)),
        )
        return len(keep)

    def _excluded(self, symbol: str) -> bool:
        if symbol in set(self.cfg.exclude_symbols):
            return True
        for pattern in self.cfg.exclude_patterns:
            if pattern and pattern.upper() in symbol:
                return True
        return False

    # -------------------------------------------------------------- context

    def refresh_context(self, client: BinanceREST) -> int:
        """Pull the full-market 24h ticker and rebuild the tracked list."""
        try:
            rows = client.ticker_24h()
        except BinanceError as exc:
            LOG.error("%s 24h ticker failed: %s", self.spec.name, exc)
            return 0

        stamp = int(time.time() * 1000)
        updated: Dict[str, SymbolContext] = {}
        for row in rows:
            symbol = row.get("symbol")
            if not isinstance(symbol, str):
                continue
            if self.meta and symbol not in self.meta:
                continue
            if self._excluded(symbol):
                continue
            try:
                ctx = SymbolContext(
                    symbol=symbol,
                    market=self.spec.name,
                    last_price=float(row.get("lastPrice") or 0.0),
                    open_price=float(row.get("openPrice") or 0.0),
                    high_price=float(row.get("highPrice") or 0.0),
                    low_price=float(row.get("lowPrice") or 0.0),
                    price_change_pct=float(row.get("priceChangePercent") or 0.0),
                    quote_volume=float(row.get("quoteVolume") or 0.0),
                    base_volume=float(row.get("volume") or 0.0),
                    trades=int(float(row.get("count") or 0)),
                    updated_ms=stamp,
                )
            except (TypeError, ValueError):
                continue
            if ctx.last_price <= 0:
                continue
            updated[symbol] = ctx

        self.context = updated
        self.last_refresh_ms = stamp
        self.refresh_count += 1
        self._select_tracked()
        return len(updated)

    def _select_tracked(self) -> None:
        """Rank by 24h quote volume and keep the top N above the liquidity bar."""
        candidates = [
            ctx for ctx in self.context.values()
            if ctx.quote_volume >= self.cfg.min_quote_volume_24h
        ]
        candidates.sort(key=lambda c: c.quote_volume, reverse=True)
        limit = max(0, self.cfg.max_symbols_per_market)
        self.tracked = [ctx.symbol for ctx in candidates[:limit]] if limit else [
            ctx.symbol for ctx in candidates
        ]

    # -------------------------------------------------------------- lookups

    def get(self, symbol: str) -> Optional[SymbolContext]:
        return self.context.get(symbol)

    def top_by_volume(self, n: int) -> List[SymbolContext]:
        ranked = sorted(self.context.values(), key=lambda c: c.quote_volume, reverse=True)
        return ranked[:n]

    def summary(self) -> str:
        return (
            f"{self.spec.name}: {len(self.context)} symbols with 24h data, "
            f"{len(self.tracked)} tracked "
            f"(>= {fmt_money(self.cfg.min_quote_volume_24h)} 24h quote volume)"
        )


def bootstrap_plan(
    universes: Sequence[Universe], top_n: int
) -> List[tuple]:
    """Symbols to preload 1m klines for, as ``(market, symbol)`` pairs."""
    plan: List[tuple] = []
    for universe in universes:
        for ctx in universe.top_by_volume(top_n):
            plan.append((universe.spec.name, ctx.symbol))
    return plan
