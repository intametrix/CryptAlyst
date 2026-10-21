#!/usr/bin/env python3
"""ratelimit - one shared rate-limit-aware fetch for the whole fleet.

Binance answers 418/429 when it decides the caller's IP is abusing it, and the
answer to 418 is to stop asking, not to crash. A bare urlopen turns the 418 into
an uncaught exception, which the supervisor reads as a crash and restarts forever.

Every engine's fetch goes through jget here: on a retryable code (418, 429, 5xx)
the caller sleeps the Retry-After (or exponential, capped) and retries; on a
non-retryable code (401, 404, a malformed body) it raises as itself. A process-wide
cooldown means a ban is respected across all engines at once, not hammered.

NOTE: MAX_WAIT_S is 120s here, deliberately tighter than crypto_edges.py's 300s
cap (a faster resume after a ban). If the two should ever match, change both in
one commit so they never drift silently.
"""
import json
import time
import urllib.error
import urllib.request

RETRY_CODES = (418, 429, 500, 502, 503, 504)
MAX_WAIT_S = 120.0
_cooldown = {"until": 0.0}


def jget(url, timeout=8.0, headers=None):
    """Fetch JSON, retrying rate-limit codes with backoff. Raises on real errors."""
    now = time.time()
    wait = _cooldown["until"] - now
    if wait > 0:
        time.sleep(min(wait, MAX_WAIT_S))
    hdrs = dict(headers) if headers else {"User-Agent": "ratelimit/1"}
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES:
                raise
            last = e
            ra = e.headers.get("Retry-After") if e.headers else None
            try:
                wait = float(ra) if ra is not None else (2.0 * (2 ** attempt))
            except (TypeError, ValueError):
                wait = 2.0 * (2 ** attempt)
            wait = min(wait, MAX_WAIT_S)
            _cooldown["until"] = time.time() + wait
            time.sleep(wait)
        except Exception as e:
            last = e
            if attempt >= 3:
                raise
            time.sleep(0.5)
    raise last
