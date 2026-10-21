#!/usr/bin/env python3
"""Regression test: the fleet gross-exposure gate in risk.exposure_ok.

Pins the bug that the new position's notional was divided by one book's equity
instead of the fleet equity, which over-rejected large sizes. Pure, offline.

    python3 tools/test_risk.py      # exit 0 on pass
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import risk  # noqa: E402


def main():
    checks = []

    # fleet of 5 books x $5000 = $25000 total; max_gross_multiple = 3.0
    eq = 25000.0
    checks.append(("empty fleet accepts", risk.exposure_ok(0.0, 1200.0, eq, 3.0), True))
    # fleet already at 2.5x: a further 0.5x-equivalent notional stays under 3.0
    checks.append(("near ceiling accepts", risk.exposure_ok(2.5, 0.4 * eq, eq, 3.0), True))
    # fleet already at 2.5x: adding another 1.0x-equivalent would exceed 3.0
    checks.append(("over ceiling rejects", risk.exposure_ok(2.5, 1.0 * eq, eq, 3.0), False))
    # zero fleet equity never divides by zero
    checks.append(("zero equity safe", risk.exposure_ok(0.0, 100.0, 0.0, 3.0), False))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
