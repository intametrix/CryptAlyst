#!/usr/bin/env python3
"""Regression test: Binance 418/429 backoff honours Retry-After.

Before this, a 418 in btc_regime() was an unhandled exception, so crypto_edges
died and the supervisor restarted it into the same ban every 30 seconds. This
test pins the policy with no socket, no network and a fake clock: _fetch is
scripted, time is injected, jitter is stubbed to exactly 1.0.

    python3 tools/test_crypto_edges_backoff.py      # exit 0 on pass
"""
import datetime
import email.message
import email.utils
import os
import sys
import time as _real_time
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import crypto_edges as ce


class Clock:
    """Injected time: only what the module needs, and sleep advances it."""

    def __init__(self, t=1000.0):
        self.t = t
        self.slept = []

    def time(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    strftime = staticmethod(_real_time.strftime)


def http_error(code, retry_after=None):
    msg = email.message.Message()
    if retry_after is not None:
        msg["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.binance.com/x", code, "err", msg, None)


def reset(clock):
    ce._cooldown["until"] = 0.0
    ce.time = clock
    ce.random.random = lambda: 0.5      # jitter factor exactly 1.0


def main():
    clock = Clock()
    reset(clock)
    calls = {"n": 0}

    def scripted(payload):
        def f(url, timeout=10.0):
            calls["n"] += 1
            if isinstance(payload, Exception):
                raise payload
            return payload
        return f

    checks = []

    # 418 with Retry-After: honoured to the second, and the ban is recorded
    reset(clock)
    calls["n"] = 0
    ce._fetch = scripted(http_error(418, "7"))
    try:
        ce.jget("https://api.binance.com/x")
        checks.append(("418 raises RateLimited", False, True))
    except ce.RateLimited as e:
        checks.append(("418 wait is Retry-After to the second", e.wait_s, 7.0))
    checks.append(("418 sets the cooldown", ce.rate_cooldown(), 7.0))
    checks.append(("418 made one request", calls["n"], 1))

    # the gate: while the cooldown is live, no request is made at all
    try:
        ce.jget("https://api.binance.com/x")
        checks.append(("gated call raises RateLimited", False, True))
    except ce.RateLimited as e:
        checks.append(("gated call is told the remainder", e.wait_s, 7.0))
    checks.append(("gated call made no request", calls["n"], 1))

    # 429 with an HTTP-date Retry-After thirty seconds ahead
    reset(clock)
    when = datetime.datetime.fromtimestamp(clock.t + 30, tz=datetime.timezone.utc)
    date_hdr = email.utils.format_datetime(when, usegmt=True)
    ce._fetch = scripted(http_error(429, date_hdr))
    try:
        ce.jget("https://api.binance.com/x")
    except ce.RateLimited as e:
        checks.append(("429 date Retry-After honoured", round(e.wait_s, 0), 30.0))

    # 5xx with no header: exponential from the base, 4s at attempt 0
    reset(clock)
    ce._fetch = scripted(http_error(503))
    try:
        ce.jget("https://api.binance.com/x", attempt=0)
    except ce.RateLimited as e:
        checks.append(("503 backoff attempt 0", e.wait_s, 4.0))

    # a client error that is not backpressure is raised as itself, no cooldown
    reset(clock)
    ce._fetch = scripted(http_error(404))
    try:
        ce.jget("https://api.binance.com/x")
        checks.append(("404 is not absorbed", False, True))
    except urllib.error.HTTPError as e:
        checks.append(("404 is not absorbed", e.code, 404))
    except ce.RateLimited:
        checks.append(("404 is not absorbed", "RateLimited", "HTTPError"))
    checks.append(("404 sets no cooldown", ce.rate_cooldown(), 0.0))

    # the cap is real, even when the server asks for hours
    reset(clock)
    checks.append(("Retry-After 10000 capped", ce.backoff_seconds(0, 10000.0), 300.0))
    checks.append(("attempt 20 still capped", ce.backoff_seconds(20), 300.0))
    checks.append(("exponential 4/8/16", [ce.backoff_seconds(i) for i in (0, 1, 2)], [4.0, 8.0, 16.0]))

    # a good response still returns parsed JSON
    reset(clock)
    ce._fetch = scripted({"ok": 1})
    checks.append(("success passes through", ce.jget("https://api.binance.com/x"), {"ok": 1}))

    # Retry-After parser: seconds, date, garbage, absent
    checks.append(("retry_after seconds", ce.retry_after({"Retry-After": "12"}), 12.0))
    checks.append(("retry_after garbage", ce.retry_after({"Retry-After": "soon"}), None))
    checks.append(("retry_after absent", ce.retry_after({}), None))
    checks.append(("retry_after none headers", ce.retry_after(None), None))
    past = email.utils.parsedate_to_datetime("Wed, 21 Oct 2015 07:28:00 GMT").timestamp()
    checks.append(("retry_after date in the past", ce.retry_after(
        {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, now=past + 60), 0.0))

    # btc_regime survives a dead feed instead of killing the loop
    reset(clock)

    def dead_klines(*a, **k):
        raise ce.RateLimited(30.0, "x")
    ce.klines = dead_klines
    checks.append(("btc_regime returns 0 on a ban", ce.btc_regime(), 0))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    import email.utils  # noqa: E402  (used above; kept local to the test)
    sys.exit(main())
