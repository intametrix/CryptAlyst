#!/usr/bin/env python3
"""Regression test: the F4 replay's as-of-time change (change_at).

Pins the bug that matters: the OI change must be measured ENDING at the fill's
entry time, using the sample at that time — not the newest sample in the buffer
(which would read the current change for a historical fill). Pure, offline.

    python3 tools/test_f4_replay.py      # exit 0 on pass
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tools import f4_replay as F


def main():
    checks = []
    # history: samples at t=100..140s (oldest-first)
    hist = [(100.0, 100.0), (120.0, 105.0), (140.0, 110.0)]

    # window ending at 140s, base at 120s (140-20): (110/105 - 1)*100 = 4.76
    got = F.change_at(hist, 140_000, 20)
    checks.append(("as-of end", round(got, 2), 4.76))

    # entry time before the history starts -> None (excluded from the cohort)
    checks.append(("before history", F.change_at(hist, 80_000, 20), None))

    # entry time in the middle uses the sample at that time, not the newest
    # at 120s: base at 100s, cur at 120s -> (105/100 - 1)*100 = 5.0
    checks.append(("mid history", round(F.change_at(hist, 120_000, 20), 2), 5.0))

    # empty history -> None
    checks.append(("empty", F.change_at([], 100_000, 20), None))

    # base of zero -> None (avoid divide by zero)
    checks.append(("zero base", F.change_at([(120.0, 0.0), (140.0, 110.0)], 140_000, 20), None))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
