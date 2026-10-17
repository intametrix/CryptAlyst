#!/usr/bin/env python3
"""gofeed - all market data and indicators come from the Go backend.

The Go service already holds the websocket streams, caches klines and computes
ATR / RSI / EMA / Donchian / ADX / SMA / volume-median. The Python models were
each pulling raw klines from Binance themselves, which duplicated the heavy
work N times and burned rate limit.

This module is now the single door:

    feed = gofeed.bars("BTCUSDT", "5m", 200)
    feed["close"], feed["high"], feed["low"], feed["quote"]
    feed["ind"]["rsi14"][-1]      # computed in Go

If the backend is unreachable it raises; callers decide whether to skip.
"""

import json
import time
import urllib.request

BACKEND = "http://127.0.0.1:8787"
UA = {"User-Agent": "gofeed/1.0"}

_CACHE = {}
_TTL = 2.0          # seconds; the Go side caches too, this just avoids storms


def _get(url, timeout=6.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def bars(symbol, interval="5m", limit=200, use_cache=True):
    """Bars + precomputed indicators for one symbol, from the Go backend."""
    key = (symbol, interval, limit)
    now = time.time()
    if use_cache:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    d = _get("%s/api/klines?symbol=%s&interval=%s&limit=%d"
             % (BACKEND, symbol, interval, limit))
    raw = d.get("bars") or []
    if not raw:
        raise ValueError("no bars for %s" % symbol)
    feed = {
        "symbol": symbol,
        "interval": interval,
        "t": [b["t"] for b in raw],
        "open": [b["o"] for b in raw],
        "high": [b["h"] for b in raw],
        "low": [b["l"] for b in raw],
        "close": [b["c"] for b in raw],
        "volume": [b["v"] for b in raw],
        "quote": [b.get("q") or 0.0 for b in raw],
        "ind": d.get("indicators") or {},
        "latest": d.get("latest") or {},
    }
    if use_cache:
        _CACHE[key] = (now, feed)
    return feed


def indicator(feed, name, default=None):
    """Last value of a Go-computed indicator, tolerating warmup nulls."""
    seq = (feed.get("ind") or {}).get(name)
    if not seq:
        return default
    for v in reversed(seq):
        if v is not None:
            return v
    return default


def universe(n=40, min_volume=20_000_000.0):
    """The Go backend's own tracked universe (already filtered by liquidity)."""
    d = _get("%s/api/universe" % BACKEND)
    rows = d.get("symbols") or []
    out = []
    for r in rows:
        try:
            qv = float(r.get("quote_volume_24h") or 0)
        except Exception:
            continue
        sym = r.get("symbol")
        if not sym or not sym.endswith("USDT"):
            continue
        if qv < min_volume:
            continue
        out.append((qv, sym))
    out.sort(reverse=True)
    return [s for _, s in out[:n]]


def prices():
    """Batched last prices via Binance (single request, cheapest path)."""
    req = urllib.request.Request(
        "https://api.binance.com/api/v3/ticker/price", headers=UA)
    with urllib.request.urlopen(req, timeout=8) as r:
        return {x["symbol"]: float(x["price"])
                for x in json.loads(r.read().decode("utf-8"))}


def backend_alive():
    try:
        _get(BACKEND + "/api/health", timeout=3)
        return True
    except Exception:
        return False
