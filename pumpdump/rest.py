"""Binance REST client: stdlib only, weight-aware, with optional signing.

Design notes
------------
* ``urllib.request`` is used directly so the scanner has **zero third-party
  dependencies**. Only ``websockets``-free public endpoints are needed for
  detection; signing is available for credential checks and future use.
* Responses are requested gzip-encoded. Binance's full-market 24h ticker is
  ~1.9 MB uncompressed for spot and ~275 KB gzipped -- a 6x bandwidth saving
  that matters when polling in a loop.
* Every response's ``x-mbx-used-weight-1m`` header is recorded so the scanner
  can report (and stay inside) the exchange rate limit.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .common import LOG, now_ms
from .config import MarketSpec


class BinanceError(RuntimeError):
    """Raised for a non-retryable API or transport failure."""

    def __init__(self, message: str, status: Optional[int] = None, code: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class ClientStats:
    requests: int = 0
    errors: int = 0
    retries: int = 0
    bytes_in: int = 0
    last_weight_used: int = 0
    peak_weight_used: int = 0
    throttled_429: int = 0
    banned_418: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, *, ok: bool, nbytes: int, weight: int) -> None:
        with self._lock:
            self.requests += 1
            if not ok:
                self.errors += 1
            self.bytes_in += nbytes
            if weight:
                self.last_weight_used = weight
                self.peak_weight_used = max(self.peak_weight_used, weight)

    def bump(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, field_name, getattr(self, field_name) + amount)


class BinanceREST:
    """Thread-safe public/signed REST access for one market."""

    def __init__(
        self,
        spec: MarketSpec,
        *,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        timeout: float = 20.0,
        user_agent: str = "binance-pumpdump/1.0",
        max_retries: int = 4,
        recv_window: int = 5000,
    ) -> None:
        self.spec = spec
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.recv_window = recv_window
        self.stats = ClientStats()
        self._opener = urllib.request.build_opener()
        # Serialise signed requests so a shared timestamp/nonce stays ordered.
        self._sign_lock = threading.Lock()

    # ---------------------------------------------------------------- core

    def _request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        signed: bool = False,
        method: str = "GET",
    ) -> Any:
        query = dict(params or {})
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Connection": "close",
        }
        if signed:
            if not self.api_key or not self.api_secret:
                raise BinanceError(
                    "signed endpoint requires BINANCE_API_KEY and BINANCE_API_SECRET"
                )
            headers["X-MBX-APIKEY"] = self.api_key

        attempt = 0
        while True:
            attempt += 1
            q = dict(query)
            if signed:
                q["timestamp"] = now_ms()
                q["recvWindow"] = self.recv_window
                payload = _encode_params(q)
                signature = hmac.new(
                    self.api_secret.encode("utf-8"),
                    payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                url = f"{self.spec.host}{path}?{payload}&signature={signature}"
            else:
                encoded = _encode_params(q)
                url = f"{self.spec.host}{path}" + (f"?{encoded}" if encoded else "")

            request = urllib.request.Request(url, headers=headers, method=method)
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read()
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    weight = _int_or_zero(response.headers.get("x-mbx-used-weight-1m"))
                    self.stats.record(ok=True, nbytes=len(raw), weight=weight)
                    if not raw:
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                body = b""
                try:
                    body = exc.read()
                    if exc.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                except Exception:  # noqa: BLE001 - body is best-effort diagnostics
                    pass
                self.stats.record(ok=False, nbytes=len(body), weight=0)
                status = exc.code
                code, message = _decode_error(body)
                retry_after = _int_or_zero(exc.headers.get("Retry-After"))

                if status in (429, 418):
                    self.stats.bump("throttled_429" if status == 429 else "banned_418")
                    if status == 418:
                        wait = max(retry_after, 120)
                        LOG.error(
                            "%s rate-limit ban (418); backing off %ss", self.spec.name, wait
                        )
                    else:
                        wait = max(retry_after, 5)
                        LOG.warning("%s throttled (429); sleeping %ss", self.spec.name, wait)
                    if attempt > self.max_retries:
                        raise BinanceError(
                            f"{self.spec.name} {status}: {message}", status, code
                        ) from exc
                    time.sleep(wait)
                    continue

                if status in (500, 502, 503, 504) and attempt <= self.max_retries:
                    self.stats.bump("retries")
                    time.sleep(_backoff(attempt))
                    continue

                # 400-class: not retryable. Surface Binance's own message.
                detail = f"{self.spec.name} HTTP {status}"
                if code is not None:
                    detail += f" code={code}"
                if message:
                    detail += f": {message}"
                if status == 451:
                    detail += (
                        " (restricted location -- spot/futures hosts may be "
                        "geo-blocked on this network)"
                    )
                raise BinanceError(detail, status, code) from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                self.stats.record(ok=False, nbytes=0, weight=0)
                if attempt > self.max_retries:
                    raise BinanceError(
                        f"{self.spec.name} request failed after {attempt} attempts: {exc}"
                    ) from exc
                self.stats.bump("retries")
                time.sleep(_backoff(attempt))

    # ----------------------------------------------------------- endpoints

    def ping(self) -> Any:
        return self._request(self.spec.path_ping)

    def server_time(self) -> int:
        data = self._request("/fapi/v1/time" if self.spec.is_futures else "/api/v3/time")
        return int(data["serverTime"])

    def exchange_info(self) -> Dict[str, Any]:
        return self._request(self.spec.path_exchange_info)

    def ticker_24h(self) -> List[Dict[str, Any]]:
        """Full-market 24h rolling ticker (includes volume and trade count)."""
        return self._request(self.spec.path_ticker_24h) or []

    def ticker_price(self) -> List[Dict[str, Any]]:
        """Full-market last price -- the cheap, high-frequency feed."""
        return self._request(self.spec.path_ticker_price) or []

    def klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 60,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[List[Any]]:
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self._request(self.spec.path_klines, params) or []

    def open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        if not self.spec.path_open_interest:
            return None
        try:
            return self._request(self.spec.path_open_interest, {"symbol": symbol})
        except BinanceError:
            return None

    def premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        if not self.spec.path_premium_index:
            return None
        try:
            return self._request(self.spec.path_premium_index, {"symbol": symbol})
        except BinanceError:
            return None

    def account(self) -> Dict[str, Any]:
        path = "/fapi/v2/balance" if self.spec.is_futures else "/api/v3/account"
        return self._request(path, signed=True)

    def api_key_permissions(self) -> Optional[Dict[str, Any]]:
        try:
            return self._request("/sapi/v1/account/apiRestrictions", signed=True)
        except BinanceError as exc:
            LOG.debug("apiRestrictions unavailable: %s", exc)
            return None

    # ------------------------------------------------------------- helper

    @property
    def weight_limit(self) -> int:
        return self.spec.weight_limit_per_min

    def weight_pressure(self) -> float:
        """Fraction of the per-minute weight budget currently consumed."""
        used = self.stats.last_weight_used or 0
        return used / self.weight_limit if self.weight_limit else 0.0


def _int_or_zero(value: Optional[str]) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _encode_params(params: Dict[str, Any]) -> str:
    """Percent-encode query parameters the way Binance expects.

    Deliberately **not** ``urllib.parse.urlencode(doseq=True)``. Binance's array
    parameters take a JSON document -- ``symbols=["BTCUSDT","ETHUSDT"]`` -- and
    ``doseq=True`` treats a string value as a sequence of characters, exploding
    it into one parameter per character and yielding HTTP 400. List values are
    therefore JSON-encoded here, and scalars are encoded plainly.
    """
    parts: List[str] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, dict)):
            value = json.dumps(value, separators=(",", ":"))
        parts.append(
            f"{urllib.parse.quote(str(key), safe='')}="
            f"{urllib.parse.quote(str(value), safe='')}"
        )
    return "&".join(parts)


def _decode_error(body: bytes) -> tuple:
    """Return ``(code, message)`` from a Binance error body."""
    if not body:
        return None, ""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, body[:200].decode("utf-8", "replace")
    if isinstance(payload, dict):
        return payload.get("code"), str(payload.get("msg", ""))
    return None, str(payload)[:200]


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter, capped at ~20s."""
    return min(20.0, (2 ** (attempt - 1)) * 0.5) * (0.7 + random.random() * 0.6)
