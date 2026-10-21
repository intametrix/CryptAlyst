#!/usr/bin/env python3
"""Regression test: solver tier-1 core (typed space, fee model, reproducible
recommendation). Pure functions, no network, no wall clock, no live state.

    python3 tools/test_solver.py      # exit 0 on pass
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import solver as S


def main():
    checks = []

    # typed + bounded parameter space
    checks.append(("in-bounds", S.bounds_ok({"lock_pct": 0.5, "clock_s": 600}), True))
    checks.append(("out-of-bounds", S.bounds_ok({"lock_pct": 5.0}), False))
    checks.append(("unknown param", S.bounds_ok({"nope": 1}), False))
    checks.append(("empty params", S.bounds_ok({}), False))

    # fee model: round trip is fee + slippage, provenance carried
    fm = S.fee_model(fee_bps=20.0, slippage_bps=1.5, provenance="measured")
    checks.append(("round trip", fm["round_trip_bps"] == 21.5, True))
    checks.append(("provenance", fm["provenance"] == "measured", True))

    # objective: risk-adjusted (mean minus drawdown penalty); empty -> None
    checks.append(("empty objective", S.objective([]) is None, True))
    o1 = S.objective([1.0, 1.0, 1.0, 1.0])   # no drawdown: score == mean
    checks.append(("flat objective", o1 == 1.0, True))
    o2 = S.objective([2.0, -1.0, 2.0, -1.0]) # drawdown drags the score below the mean
    mean = (2.0 - 1.0 + 2.0 - 1.0) / 4.0
    checks.append(("drawdown penalises", o2 < mean, True))

    # recommendation is reproducible only with full provenance
    r_full = S.recommend("donchian55", {"lock_pct": 0.5}, 0.8, {"trades": 10},
                         code_hash="abc123", budget=100, priors={"lock_pct": 0.4},
                         window="2026-10-01..2026-10-20")
    checks.append(("reproducible", r_full["reproducible"], True))
    checks.append(("tier label", r_full["tier"], "backtest_only"))

    r_opinion = S.recommend("donchian55", {"lock_pct": 0.5}, 0.8, {"trades": 10},
                            code_hash="", budget=None, priors=None, window=None)
    checks.append(("opinion not reproducible", r_opinion["reproducible"], False))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
