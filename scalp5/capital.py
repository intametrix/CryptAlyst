#!/usr/bin/env python3
"""capital - earn your size.

A book that keeps making money gets to put more capital at work and to hold more
positions. A book that keeps losing gets smaller before the disable rule fires.

    python3 capital.py            # recompute state/capital.json and print the table

Rules, in one place:
  * nothing is promoted on fewer than MIN_TRADES closed trades
  * the last WINDOW trades must also be positive, so an old run cannot carry a book
  * risk_mult  = 1.0 + 0.25 per +2% realised, capped at MAX_RISK
  * slots      = +1 per +5% realised, capped at MAX_SLOTS
  * a loser that is also losing lately is halved
  * a disabled book gets nothing: 0.0 and 0 slots (A6)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
PACKS = os.path.join(STATE)
DISABLED = os.path.join(STATE, "disabled.json")
OUT = os.path.join(STATE, "capital.json")

MIN_TRADES = 30
WINDOW = 50
MAX_RISK = 3.0
MAX_SLOTS = 4


def disabled_books():
    try:
        return {str(b).replace("_", "").lower() for b in json.load(open(DISABLED)).get("books") or []}
    except Exception:
        return set()


def load_pack(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def window_pnl(trades, n=WINDOW):
    tail = trades[-n:]
    return sum(float(t.get("net_pct") or 0.0) for t in tail), len(tail)


def evaluate(pack, disabled):
    eng = str(pack.get("engine") or "")
    start = float(pack.get("start_equity") or 0.0)
    equity = float(pack.get("equity") or start)
    trades = [t for t in (pack.get("trades") or []) if t.get("net_pct") is not None]
    n = len(trades)
    realised = equity - start
    realised_pct = (100.0 * realised / start) if start else 0.0
    last, m = window_pnl(trades)
    wins = sum(1 for t in trades if float(t.get("net_pct") or 0) > 0)

    risk_mult, slots, note = 1.0, 1, "baseline"
    if eng.replace("_", "").lower() in disabled:
        return {"engine": eng, "trades": n, "realised_pct": round(realised_pct, 3),
                "win_rate": round(100.0 * wins / n, 1) if n else 0.0, "window": m,
                "risk_mult": 0.0, "slots": 1, "allocated": 0.0,
                "note": "disabled (A6): no capital, no slots"}
    if n < MIN_TRADES:
        note = "too few trades (%d < %d)" % (n, MIN_TRADES)
    elif realised_pct > 0 and last >= 0:
        risk_mult = min(MAX_RISK, 1.0 + 0.25 * int(realised_pct // 2))
        slots = 1 + min(MAX_SLOTS, int(realised_pct // 5))
        note = "promoted: +%.1f%% realised, last %d %+.2f" % (realised_pct, m, last)
    elif realised_pct < 0 and last < 0:
        risk_mult, slots = 0.5, 1
        note = "demoted: %.1f%% realised, last %d %+.2f" % (realised_pct, m, last)

    return {"engine": eng, "trades": n, "realised_pct": round(realised_pct, 3),
            "win_rate": round(100.0 * wins / n, 1) if n else 0.0, "window": m,
            "risk_mult": round(risk_mult, 3), "slots": slots,
            "allocated": round(start * risk_mult, 2), "note": note}


def compute():
    dis = disabled_books()
    rows = []
    for name in sorted(os.listdir(PACKS)):
        if not (name.startswith("pack_") and name.endswith(".json")):
            continue
        pack = load_pack(os.path.join(PACKS, name))
        if not pack or not pack.get("engine"):
            continue
        rows.append(evaluate(pack, dis))
    # a disabled book gets an explicit zero row even if its pack is gone: absence must
    # never be read as "eligible"
    have = {r["engine"].replace("_", "").lower() for r in rows}
    for name in sorted(dis - have):
        rows.append({"engine": name, "trades": 0, "realised_pct": 0.0, "win_rate": 0.0,
                     "window": 0, "risk_mult": 0.0, "slots": 1, "allocated": 0.0,
                     "note": "disabled (A6): no capital, no slots"})
    return rows


def write(rows):
    os.makedirs(STATE, exist_ok=True)
    body = {r["engine"]: r for r in rows}
    tmp = OUT + ".t"
    with open(tmp, "w") as fh:
        json.dump(body, fh, sort_keys=True)
    os.replace(tmp, OUT)
    return body


def table(rows):
    print("%-22s %6s %8s %7s %6s %6s %10s  %s" % (
        "engine", "trades", "realised%", "win%", "risk", "slots", "allocated", "note"))
    for r in sorted(rows, key=lambda x: -x["realised_pct"]):
        print("%-22s %6d %8.2f %7.1f %6.2f %6d %10.0f  %s" % (
            r["engine"], r["trades"], r["realised_pct"], r["win_rate"],
            r["risk_mult"], r["slots"], r["allocated"], r["note"]))


if __name__ == "__main__":
    rs = compute()
    write(rs)
    table(rs)
    print("\nwritten: %s" % OUT)
