#!/usr/bin/env python3
"""Regression test: skim entry floor (should_enter) + momentum percentile log.

The fee-machine bug: skim entered the strongest 3-bar momentum symbol with no
minimum, so dead entries (<0.5 bps) were churned into -20 bps fee losses. The
observe-only floor (10 bps) suppresses those and samples their distribution for
later fitting. This test pins the guard and the percentile helper, no network.

    python3 tools/test_skim_guard.py      # exit 0 on pass
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import skim as S


def main():
    checks = []

    # should_enter: the floor is half the +30 bps take-profit, clearing the fee
    checks.append(("below floor", S.should_enter(5.0, 10.0), False))
    checks.append(("at floor", S.should_enter(10.0, 10.0), True))
    checks.append(("above floor", S.should_enter(15.0, 10.0), True))
    checks.append(("default floor", S.should_enter(9.9), False))

    # pctile: p50 and p90 of a known sorted list
    scores = sorted([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    checks.append(("p50", S.pctile(scores, 0.5), 5.0))
    checks.append(("p90", S.pctile(scores, 0.9), 9.0))
    checks.append(("pctile empty", S.pctile([], 0.9), None))
    checks.append(("pctile single", S.pctile([7.0], 0.9), 7.0))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
