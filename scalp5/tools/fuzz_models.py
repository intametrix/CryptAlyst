#!/usr/bin/env python3
"""Random fuzz: thousands of randomly-malformed bar sets, models must never crash.

Each bar's fields are drawn from a pool of pathological values (valid float,
zero, negative, tiny, huge, NaN, inf, None, string), and 10% of fields are
omitted entirely. Every model runs on every generated set; any exception is a
failure. Seeded for reproducibility.

    python3 tools/fuzz_models.py [iterations]
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tactics
tactics.require_enabled = lambda *a, **k: None

import models10
import alpha
import crypto_strategies

# a pool of pathological values a field can take
CRAZY = [0.0, -1.0, 1.0, 60000.0, 1e-9, 1e9, float("nan"), float("inf"),
         None, "abc", -100.0, 1e15, -1e15, 123456.78]


def random_bars(rnd, n):
    bars = []
    for _ in range(n):
        b = {}
        for k in ("o", "h", "l", "c", "v"):
            if rnd.random() < 0.9:          # 10% chance to omit the key
                b[k] = rnd.choice(CRAZY)
        if rnd.random() < 0.05:             # 5% chance the "bar" is not a dict
            b = rnd.choice(CRAZY)
        bars.append(b)
    return bars


MODELS = [
    models10.r_donchian55, models10.r_vol_break, models10.r_vwap_revert,
    models10.r_boll_squeeze, models10.r_rsi2, alpha.cascade_signal,
]

# alert-based strategies take (alert, votes, held) — fuzz them the same way
ALERT_MODELS = [
    crypto_strategies.flow_follow, crypto_strategies.range_fade,
    crypto_strategies.model_stack, crypto_strategies.sweep_reversal,
    crypto_strategies.momentum_stack,
]


def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    rnd = random.Random(12345)
    crashes = {}
    for _ in range(iters):
        bars = random_bars(rnd, rnd.randint(0, 80))
        for m in MODELS:
            try:
                m(bars)
            except Exception as e:
                key = "%s:%s" % (m.__name__, type(e).__name__)
                crashes[key] = crashes.get(key, 0) + 1
        a = rnd.choice(CRAZY); votes = rnd.choice(CRAZY); held = rnd.choice(CRAZY)
        for m in ALERT_MODELS:
            try:
                m(a, votes, held)
            except Exception as e:
                key = "%s:%s" % (m.__name__, type(e).__name__)
                crashes[key] = crashes.get(key, 0) + 1
    print("iterations: %d (bar sets + alert triples)" % iters)
    if crashes:
        for k, n in sorted(crashes.items()):
            print("  CRASH  %-40s x%d" % (k, n))
        return 1
    print("no crashes across %d malformed inputs" % iters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
