#!/usr/bin/env python3
"""Regression test: OI window change (oi_change_pct) in feeds.py.

The bug: feeds.py's docstring claimed 5m/15m/1h OI change while oi_chg_pct held a
~30s last-poll delta. The ring buffer makes the windows real; this test pins the
window math with injected samples and time, no network, no wall clock.

    python3 tools/test_feeds_oi.py      # exit 0 on pass
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import feeds as F


def main():
    now = 2000.0
    checks = []

    # empty history -> None
    checks.append(("empty history", F.oi_change_pct([], now, 300), None))

    # one sample -> None (no base older than it)
    checks.append(("one sample", F.oi_change_pct([(1999.0, 100.0)], now, 300), None))

    # samples spanning the whole window: change from the oldest
    hist = [(1700.0, 100.0), (1900.0, 110.0), (2000.0, 121.0)]
    checks.append(("full window", round(F.oi_change_pct(hist, now, 300), 3), 21.0))

    # a window narrower than the samples: base is the oldest sample inside it
    checks.append(("narrow window", round(F.oi_change_pct(hist, now, 100), 3), 10.0))

    # no sample old enough -> None (all samples older than the window)
    checks.append(("no old sample", F.oi_change_pct([(1600.0, 100.0)], now, 300), None))

    # base of zero -> None (avoid divide-by-zero)
    checks.append(("zero base", F.oi_change_pct([(1700.0, 0.0), (2000.0, 100.0)], now, 300), None))

    # falling OI -> negative change
    checks.append(("falling OI", round(F.oi_change_pct([(1700.0, 200.0), (2000.0, 180.0)], now, 300), 3), -10.0))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
