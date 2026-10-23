#!/usr/bin/env python3
"""20-scenario crazy-market stress test — run, adjust, re-run.

Each scenario feeds pathological data to the models. A scenario PASSES when no
model crashes AND the key signal is sane (flat -> hold, crash -> sell/cascade,
empty/zero -> no crash). Failures are fixed in the model code, then re-run.

    python3 tools/test_crazy_market.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tactics
tactics.require_enabled = lambda *a, **k: None

import models10
import alpha

P0 = 60000.0


def _b(o, h, l, c, v):
    return {"t": 0, "o": o, "h": h, "l": l, "c": c, "v": v}


def flat(n=60, px=P0):
    return [_b(px, px, px, px, 500.0) for _ in range(n)]


# --- 20 scenarios -----------------------------------------------------------
SCENARIOS = [
    ("1  flatline (no movement)", flat(), {"donchian55": None, "rsi2": None}),
    ("2  zero volume", [_b(P0 + i * 10, P0 + i * 10 + 5, P0 + i * 10 - 5, P0 + i * 10, 0.0) for i in range(60)], {"vol_break": None, "vwap_revert": None}),
    ("3  negative volume", [_b(P0, P0 + 5, P0 - 5, P0, -100.0) for _ in range(60)], {"vol_break": None}),
    ("4  flash crash -30% / 100x vol", flat() + [_b(P0, P0, P0 * 0.70, P0 * 0.70, 50000.0)], {"cascade": True, "donchian55": "sell"}),
    ("5  moon shot +50% / 100x vol", flat() + [_b(P0, P0 * 1.50, P0, P0 * 1.50, 50000.0)], {"donchian55": "buy"}),
    ("6  whipsaw +/-15%", (lambda: [(_b(P0 * (1 + (0.15 if i % 2 == 0 else -0.15)), P0, P0, P0 * (1 + (0.15 if i % 2 == 0 else -0.15)), 500.0)) for i in range(60)])(), {"donchian55": None}),
    ("7  single bar", [_b(P0, P0 * 1.001, P0 * 0.999, P0, 500.0)], {"donchian55": None}),
    ("8  empty (no bars)", [], {"donchian55": None, "vol_break": None}),
    ("9  zero price", [_b(0.0, 0.0, 0.0, 0.0, 500.0) for _ in range(60)], {"donchian55": None, "boll_squeeze": None}),
    ("10 negative price", [_b(-1.0, -1.0, -1.0, -1.0, 500.0) for _ in range(60)], {"donchian55": None}),
    ("11 gap down -50% open", flat() + [_b(P0 * 0.50, P0 * 0.50, P0 * 0.48, P0 * 0.48, 500.0)], {"donchian55": "sell"}),
    ("12 tiny price 1e-9", [_b(1e-9, 1e-9, 1e-9, 1e-9, 500.0) for _ in range(60)], {"donchian55": None}),
    ("13 huge price 1e9", [_b(1e9, 1e9, 1e9, 1e9, 500.0) for _ in range(60)], {"donchian55": None}),
    ("14 staircase up (steps)", [_b(P0 + i * 50, P0 + i * 50, P0 + i * 50, P0 + i * 50, 500.0) for i in range(60)], {"donchian55": "buy"}),
    ("15 staircase down (steps)", [_b(P0 - i * 50, P0 - i * 50, P0 - i * 50, P0 - i * 50, 500.0) for i in range(60)], {"donchian55": "sell"}),
    ("16 volume spike, no price move", flat() + [_b(P0, P0 * 1.001, P0 * 0.999, P0, 5000000.0)], {"vol_break": None}),
    ("17 price spike, no volume", flat() + [_b(P0, P0 * 1.10, P0 * 0.90, P0 * 1.10, 1.0)], {"vol_break": None}),
    ("18 converging (shrinking swings)", (lambda: [_b(P0, P0 * (1 + 0.1 * (0.5 ** (i // 2))), P0 * (1 - 0.1 * (0.5 ** (i // 2))), P0, 500.0) for i in range(60)])(), {"donchian55": None}),
    ("19 diverging (growing swings)", (lambda: [_b(P0, P0 * (1 + 0.01 * i), P0 * (1 - 0.01 * i), P0, 500.0) for i in range(60)])(), {}),
    ("20 NaN close", [_b(P0, P0, P0, float("nan"), 500.0) for _ in range(60)], {"donchian55": None}),
    ("21 NaN volume", [_b(P0, P0, P0, P0, float("nan")) for _ in range(60)], {"vol_break": None, "vwap_revert": None}),
    ("22 infinite price", [_b(float("inf"), float("inf"), float("inf"), float("inf"), 500.0) for _ in range(60)], {"donchian55": None}),
    ("23 infinite volume", [_b(P0, P0 * 1.001, P0 * 0.999, P0, float("inf")) for _ in range(60)], {}),
    ("24 high < low (malformed)", [_b(P0, P0 * 0.99, P0 * 1.01, P0, 500.0) for _ in range(60)], {"vol_break": None}),
    ("25 missing 'v' key", [{"t": 0, "o": P0, "h": P0, "l": P0, "c": P0} for _ in range(60)], {}),
    ("26 missing 'c' key", [{"t": 0, "o": P0, "h": P0, "l": P0, "v": 500.0} for _ in range(60)], {}),
    ("27 None close", [_b(P0, P0, P0, None, 500.0) for _ in range(60)], {}),
    ("28 string close", [_b(P0, P0, P0, "abc", 500.0) for _ in range(60)], {}),
    ("29 doji (open=close, huge wicks)", [_b(P0, P0 * 1.10, P0 * 0.90, P0, 500.0) for _ in range(60)], {"donchian55": None}),
    ("30 exponential up (doubling)", [_b(P0 * (2 ** i), P0 * (2 ** i), P0 * (2 ** i), P0 * (2 ** i), 500.0) for i in range(20)], {}),
    ("31 exponential down (halving)", [_b(P0 * (0.5 ** i), P0 * (0.5 ** i), P0 * (0.5 ** i), P0 * (0.5 ** i), 500.0) for i in range(20)], {}),
    ("32 alternating zero/inf volume", [_b(P0, P0, P0, P0, 0.0 if i % 2 == 0 else float("inf")) for i in range(60)], {}),
]


MODELS = [
    ("donchian55", models10.r_donchian55),
    ("vol_break", models10.r_vol_break),
    ("vwap_revert", models10.r_vwap_revert),
    ("boll_squeeze", models10.r_boll_squeeze),
    ("rsi2", models10.r_rsi2),
    ("cascade", alpha.cascade_signal),
]


def run(model, bars):
    try:
        return model(bars)
    except Exception as e:
        return "CRASH:%s" % type(e).__name__


def main():
    rows = []
    for name, bars, expected in SCENARIOS:
        for mname, m in MODELS:
            got = run(m, bars)
            want = expected.get(mname, None)
            # sanity: a crash is always a fail; otherwise check the specific expectation
            if isinstance(got, str) and got.startswith("CRASH"):
                ok = False
            elif mname in expected:
                ok = got == want
            else:
                ok = True  # no specific expectation; just must not crash
            rows.append((name, mname, got, want, ok))

    w = max(len(r[0]) for r in rows)
    print("%-30s %-14s %-22s %-12s %s" % ("SCENARIO", "MODEL", "ACTUAL", "EXPECTED", "RESULT"))
    print("-" * 92)
    bad = 0
    for name, mname, got, want, ok in rows:
        bad += 0 if ok else 1
        flag = "PASS" if ok else "FAIL"
        print("%-30s %-14s %-22s %-12s %s" % (name, mname, str(got), str(want), flag))
    print("-" * 92)
    print("%d rows, %d failed" % (len(rows), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
