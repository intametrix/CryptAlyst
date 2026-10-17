"""Binance public-data client: spot klines/tickers/exchangeInfo + fapi context.

Public endpoints only -- no API key, no signing, no account access.  This is
the ``rest.py`` role the plan refers to (plan s5, s6.5).

Features that matter for a full-universe pull (plan s5: "~2k requests, one
evening on a laptop"):
  * on-disk JSON cache per (symbol, interval), merged on re-fetch
  * weight accounting with a polite budget, plus 429/418 backoff
  * stdlib only (urllib)
"""

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
DATA_API = "https://data-api.binance.vision"   # market-data-only mirror

# klines weight by requested limit (Binance spot docs)
KLINES_WEIGHT = [(100, 1), (500, 2), (1000, 5), (1500, 10)]

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 3_600_000,
    "2h": 2 * 3_600_000,
    "4h": 4 * 3_600_000,
    "6h": 6 * 3_600_000,
    "8h": 8 * 3_600_000,
    "12h": 12 * 3_600_000,
    "1d": 86_400_000,
    "3d": 3 * 86_400_000,
    "1w": 7 * 86_400_000,
}


class WeightBudget(object):
    """Sliding-window request-weight governor (spot allows 6000/min)."""

    def __init__(self, per_minute=1000):
        self.per_minute = per_minute
        self.events = []          # (timestamp, weight)

    def _trim(self, now):
        cutoff = now - 60.0
        self.events = [e for e in self.events if e[0] >= cutoff]

    def spend(self, weight):
        now = time.time()
        self._trim(now)
        used = sum(w for _, w in self.events)
        if used + weight > self.per_minute:
            sleep_for = 60.0 - (now - self.events[0][0]) + 0.05 if self.events else 1.0
            if sleep_for > 0:
                time.sleep(min(sleep_for, 61.0))
            now = time.time()
            self._trim(now)
        self.events.append((now, weight))

    def used(self):
        now = time.time()
        self._trim(now)
        return sum(w for _, w in self.events)


class BinancePublic(object):
    def __init__(self, cache_dir="cache", base=SPOT, fapi=FAPI, budget=None,
                 max_retries=5, verbose=False):
        self.base = base
        self.fapi = fapi
        self.cache_dir = cache_dir
        self.budget = budget or WeightBudget()
        self.max_retries = max_retries
        self.verbose = verbose
        os.makedirs(self.cache_dir, exist_ok=True)
        self.stats = {"requests": 0, "errors": 0, "weight": 0}

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _get(self, url, params=None, weight=1):
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        last_err = None
        for attempt in range(self.max_retries):
            self.budget.spend(weight)
            self.stats["requests"] += 1
            self.stats["weight"] += weight
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "binance-turtle/0.1",
                                  "Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_err = exc
                self.stats["errors"] += 1
                if exc.code in (418, 429):
                    retry_after = int(exc.headers.get("Retry-After", "5") or 5)
                    time.sleep(min(retry_after, 60))
                elif 400 <= exc.code < 500:
                    body = ""
                    try:
                        body = exc.read().decode("utf-8", "replace")
                    except Exception:
                        pass
                    raise RuntimeError("HTTP %s for %s: %s" % (exc.code, url, body))
                else:
                    time.sleep(0.5 * (2 ** attempt))
            except Exception as exc:                      # network flake
                last_err = exc
                self.stats["errors"] += 1
                time.sleep(0.5 * (2 ** attempt))
        raise RuntimeError("request failed after %d tries: %s (%s)"
                           % (self.max_retries, url, last_err))

    # ------------------------------------------------------------------
    # cache
    # ------------------------------------------------------------------
    def _cache_path(self, name):
        return os.path.join(self.cache_dir, name)

    def _load_json(self, path):
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _save_json(self, path, payload):
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # market data
    # ------------------------------------------------------------------
    def ping(self):
        self._get(self.base + "/api/v3/ping", weight=1)
        return True

    def server_time(self):
        return self._get(self.base + "/api/v3/time", weight=1)["serverTime"]

    def klines(self, symbol, interval="1d", start_ms=None, end_ms=None,
               limit=1000):
        """One page of klines as dicts."""
        if limit not in [w[0] for w in KLINES_WEIGHT]:
            for cap, _ in KLINES_WEIGHT:
                if limit <= cap:
                    limit = cap
                    break
        weight = 1
        for cap, w in KLINES_WEIGHT:
            if limit <= cap:
                weight = w
                break
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        raw = self._get(self.base + "/api/v3/klines", params, weight=weight)
        out = []
        for k in raw:
            out.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
                "taker_buy_base": float(k[9]),
                "taker_buy_quote": float(k[10]),
            })
        return out

    def klines_range(self, symbol, interval, start_ms, end_ms=None,
                     use_cache=True, limit=1000):
        """Paginated full history for one symbol, cached and merged on disk."""
        cache_name = "%s_%s.json" % (symbol, interval)
        path = self._cache_path(cache_name)
        rows = []
        if use_cache:
            cached = self._load_json(path)
            if cached:
                rows = cached
        have = {r["open_time"] for r in rows}
        step = INTERVAL_MS[interval]
        # BACKWARD FILL FIRST. The cache only ever held recent bars, so a
        # merge-forward loop silently capped history at whatever was cached --
        # which is how BTC ended up with 399 bars and the regime gate never ran.
        if rows:
            earliest = min(r["open_time"] for r in rows)
            bcur = start_ms
            while bcur < earliest:
                page = self.klines(symbol, interval, start_ms=bcur,
                                   end_ms=earliest - 1, limit=limit)
                if not page:
                    break
                fresh = [r for r in page if r["open_time"] not in have]
                rows.extend(fresh)
                have.update(r["open_time"] for r in fresh)
                if len(page) < limit:
                    break
                bcur = page[-1]["open_time"] + step
        cursor = start_ms
        if rows:
            last = max(r["open_time"] for r in rows)
            cursor = max(start_ms, last + step)
        while True:
            if end_ms is not None and cursor > end_ms:
                break
            page = self.klines(symbol, interval, start_ms=cursor,
                               end_ms=end_ms, limit=limit)
            if not page:
                break
            fresh = [r for r in page if r["open_time"] not in have]
            rows.extend(fresh)
            have.update(r["open_time"] for r in fresh)
            if len(page) < limit:
                break
            cursor = page[-1]["open_time"] + step
        rows.sort(key=lambda r: r["open_time"])
        if use_cache and rows:
            self._save_json(path, rows)
        return rows

    def exchange_info(self, use_cache=True, max_age_s=12 * 3600):
        path = self._cache_path("exchange_info.json")
        if use_cache and os.path.exists(path):
            if time.time() - os.path.getmtime(path) < max_age_s:
                cached = self._load_json(path)
                if cached:
                    return cached
        data = self._get(self.base + "/api/v3/exchangeInfo", weight=20)
        slim = {"symbols": {}}
        for s in data.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            lot = tick = None
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    lot = float(f.get("stepSize", 0) or 0)
                elif f.get("filterType") == "PRICE_FILTER":
                    tick = float(f.get("tickSize", 0) or 0)
            slim["symbols"][s["symbol"]] = {
                "base": s.get("baseAsset"),
                "quote": s.get("quoteAsset"),
                "lot_step": lot,
                "tick_size": tick,
            }
        if use_cache:
            self._save_json(path, slim)
        return slim

    def tickers_24hr(self):
        return self._get(self.base + "/api/v3/ticker/24hr", weight=80)

    def funding_rate(self, symbol, limit=1):
        """Public fapi funding -- the crowded-long veto (plan s2/s6.5)."""
        return self._get(self.fapi + "/fapi/v1/fundingRate",
                         {"symbol": symbol, "limit": limit}, weight=1)

    def open_interest(self, symbol):
        return self._get(self.fapi + "/fapi/v1/openInterest",
                         {"symbol": symbol}, weight=1)

    def open_interest_hist(self, symbol, period="1d", limit=30):
        return self._get(self.fapi + "/futures/data/openInterestHist",
                         {"symbol": symbol, "period": period, "limit": limit},
                         weight=1)

    # ------------------------------------------------------------------
    # universe screen (plan s1.1)
    # ------------------------------------------------------------------
    def universe(self, cfg, include_stables=False):
        """Top-N USDT spot pairs passing the $5M 24h quote-volume floor.

        The volatility band is applied later, once klines are in hand -- it
        needs ATR, which the ticker endpoint cannot give.
        """
        tickers = self.tickers_24hr()
        excluded_bases = {"USDC", "TUSD", "BUSD", "FDUSD", "DAI", "USDP",
                          "EUR", "GBP", "TRY", "BRL", "ARS", "AEUR", "USD1"}
        rows = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith(cfg.quote):
                continue
            if any(sym.endswith(suf) for suf in cfg.exclude_suffixes):
                continue
            base = sym[:-len(cfg.quote)]
            if not include_stables and base in excluded_bases:
                continue
            qv = float(t.get("quoteVolume", 0) or 0)
            if qv < cfg.min_quote_volume_24h:
                continue
            rows.append({
                "symbol": sym,
                "base": base,
                "quote_volume_24h": qv,
                "last_price": float(t.get("lastPrice", 0) or 0),
                "price_change_pct": float(t.get("priceChangePercent", 0) or 0),
                "high_24h": float(t.get("highPrice", 0) or 0),
                "low_24h": float(t.get("lowPrice", 0) or 0),
                "trades_24h": int(t.get("count", 0) or 0),
            })
        rows.sort(key=lambda r: r["quote_volume_24h"], reverse=True)
        return rows[:cfg.max_symbols]


# ----------------------------------------------------------------------
# small analysis helpers used by the screen and the detector
# ----------------------------------------------------------------------
def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def floor_to_step(qty, step):
    if not step or step <= 0:
        return qty
    return int(qty / step) * step


def median_hourly_quote_volume(bars, hours_per_bar, window=24 * 7):
    """Median quote volume per hour, from bars of a known bar length."""
    vols = [b["quote_volume"] for b in bars[-window:]] if bars else []
    med = median(vols)
    if med is None or not hours_per_bar:
        return None
    return med / float(hours_per_bar)
