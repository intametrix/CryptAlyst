#!/usr/bin/env python3
"""Regression test: oi_gate price×OI quadrant scoring (observe-only).

Pins the quadrant classification, the strategy-aware adjustment, the short
inversion, and the climax-tail cap. Pure functions, no network, no wall clock,
no live state.

    python3 tools/test_oi_gate.py      # exit 0 on pass
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import oi_gate as O


def main():
    checks = []

    # quadrant classification
    checks.append(("confirmation", O.quadrant(1.0, 3.0), "confirmation"))   # px↑ OI↑
    checks.append(("divergence", O.quadrant(1.0, -3.0), "divergence"))      # px↑ OI↓
    checks.append(("accumulation", O.quadrant(-1.0, 3.0), "accumulation"))  # px↓ OI↑
    checks.append(("distribution", O.quadrant(-1.0, -3.0), "distribution")) # px↓ OI↓
    checks.append(("climax", O.quadrant(0.3, 9.0), "climax"))               # extreme OI
    checks.append(("flat", O.quadrant(0.01, 1.0), "flat"))                  # tiny price move

    # strategy-aware adjustment (momentum: confirmation boosts a long)
    checks.append(("momentum long confirmation", O.adjust(1.0, "confirmation", "buy", "momentum"), 1.15))
    checks.append(("momentum long distribution", O.adjust(1.0, "distribution", "buy", "momentum"), 0.80))

    # short inverts: a bearish quadrant is favourable for a short
    checks.append(("momentum short distribution", O.adjust(1.0, "distribution", "sell", "momentum"), 1.20))
    checks.append(("momentum short confirmation", O.adjust(1.0, "confirmation", "sell", "momentum"), 0.85))

    # reversion prefers the fade (divergence/distribution)
    checks.append(("reversion long divergence", O.adjust(1.0, "divergence", "buy", "reversion"), 1.10))

    # climax caps the adjustment regardless of strategy
    checks.append(("climax cap momentum", O.adjust(1.0, "climax", "buy", "momentum"), 0.70))
    checks.append(("climax cap reversion", O.adjust(1.0, "climax", "buy", "reversion"), 0.60))

    # default is neutral (observe-only, no weighting when unknown strategy)
    checks.append(("default neutral", O.adjust(1.0, "divergence", "buy", "unknown"), 1.0))

    # envelope feature carries the quadrant, never blocks
    f = O.feature(1.0, -3.0)
    checks.append(("feature quadrant", f["oi_quadrant"], "divergence"))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
