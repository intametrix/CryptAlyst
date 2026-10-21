#!/usr/bin/env python3
"""Unit tests for capital.py: the promotion rules, on generated packs."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import capital

def pack(engine, start=5000.0, equity=5000.0, n=0, net=0.0, last_n=None):
    trades = [{"net_pct": net}] * n
    if last_n is not None:
        # the last WINDOW trades carry this value instead, so the window test is real
        for i in range(min(len(trades), capital.WINDOW)):
            trades[-(i + 1)] = {"net_pct": last_n}
    return {"engine": engine, "start_equity": start, "equity": equity, "trades": trades}

def check(name, got, want):
    if got != want:
        raise AssertionError("%s: got %r want %r" % (name, got, want))
    print("  ok  %s" % name)

d = {"grid"}
# 1 no promotion under 30 trades
r = capital.evaluate(pack("grid", n=10, equity=5100, net=1.0), set())
check("no promotion under 30 trades", r["risk_mult"], 1.0)
# 2 a negative last window blocks promotion
r = capital.evaluate(pack("grid", n=100, equity=6000, net=1.0, last_n=-2.0), set())
check("negative window blocks promotion", r["risk_mult"], 1.0)
# 3 +0.25 per +2%, capped at 3.0
r = capital.evaluate(pack("grid", n=100, equity=5500, net=1.0), set())
check("+10% -> 2.25x", r["risk_mult"], 2.25)
r = capital.evaluate(pack("grid", n=400, equity=9000, net=1.0), set())
check("cap at 3.0x", r["risk_mult"], 3.0)
# 4 slots grow and cap at +4
r = capital.evaluate(pack("grid", n=400, equity=9000, net=1.0), set())
check("slots cap at 5", r["slots"], 5)
# 5 disabled gets nothing
r = capital.evaluate(pack("grid", n=100, equity=6000, net=1.0), d)
check("disabled risk_mult 0", r["risk_mult"], 0.0)
check("disabled allocation 0", r["allocated"], 0.0)
# 6 break-even is exactly baseline
r = capital.evaluate(pack("grid", n=100, equity=5000, net=0.0), set())
check("break-even 1.0x", r["risk_mult"], 1.0)
# 7 a loser is halved
r = capital.evaluate(pack("grid", n=100, equity=4800, net=-0.2), set())
check("loser halved", r["risk_mult"], 0.5)
# 8 recompute is idempotent
a = capital.evaluate(pack("grid", n=100, equity=5500, net=1.0), set())
b = capital.evaluate(pack("grid", n=100, equity=5500, net=1.0), set())
check("idempotent", a, b)
print("\n8/8 capital rules pass")
