#!/usr/bin/env python3
"""Regression test: the shared rate-limit fetch (ratelimit.jget).

Binance 418/429 is a ban, not an error. Before this, every engine had its own
bare urlopen, so a 418 crashed the engine and the supervisor restarted it forever.
This test pins the shared backoff: Retry-After honoured, exponential capped,
non-retryable codes raised as themselves, and a process-wide cooldown.

    python3 tools/test_ratelimit.py      # exit 0 on pass
"""
import email.message
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ratelimit as R


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeResp:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_err(code, retry_after=None):
    msg = email.message.Message()
    if retry_after is not None:
        msg["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x", code, "err", msg, None)


def main():
    clock = Clock(1000.0)
    R.time = clock
    orig = urllib.request.urlopen
    calls = {"n": 0}
    checks = []

    def transport(fn):
        def w(url, timeout=None):
            calls["n"] += 1
            r = fn()
            if isinstance(r, Exception):
                raise r
            return FakeResp(r)
        urllib.request.urlopen = w

    def reset():
        R._cooldown["until"] = 0.0
        calls["n"] = 0
        clock.t = 1000.0

    try:
        # 1. success returns parsed JSON
        reset()
        transport(lambda: json.dumps({"ok": 1}).encode())
        checks.append(("success", R.jget("http://x"), {"ok": 1}))

        # 2. non-retryable 404 raises as itself, no retry
        reset()
        transport(lambda: http_err(404))
        try:
            R.jget("http://x")
            checks.append(("404 raises", False, True))
        except urllib.error.HTTPError as e:
            checks.append(("404 raises", e.code, 404))
        checks.append(("404 no retry", calls["n"], 1))

        # 3. 418 with Retry-After "3" -> sleep 3, retry, then success
        reset()
        seq = [http_err(418, "3"), json.dumps({"ok": 2}).encode()]
        transport(lambda: seq.pop(0))
        checks.append(("418 then success", R.jget("http://x"), {"ok": 2}))
        checks.append(("418 retried once", calls["n"], 2))

        # 4. 418 sets a process-wide cooldown
        reset()
        transport(lambda: http_err(418, "10"))
        try:
            R.jget("http://x")
        except Exception:
            pass
        checks.append(("cooldown set after 418", R._cooldown["until"] > 1000.0, True))

        # 5. the cooldown is honoured before the next request
        reset()
        R._cooldown["until"] = clock.t + 5.0
        transport(lambda: json.dumps({"ok": 3}).encode())
        R.jget("http://x")
        checks.append(("cooldown slept before request", clock.t, 1005.0))

        # 6. Retry-After is capped
        reset()
        transport(lambda: http_err(429, "10000"))
        try:
            R.jget("http://x")
        except Exception:
            pass
        checks.append(("Retry-After capped", R._cooldown["until"] - clock.t <= 120.1, True))
    finally:
        urllib.request.urlopen = orig

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
