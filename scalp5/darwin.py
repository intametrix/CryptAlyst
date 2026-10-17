#!/usr/bin/env python3
"""darwin - selection pressure on the strategy population.

Every cycle it scores every crypto strategy book, kills the ones that cannot
survive, diagnoses *why* they died, and spawns a mutated successor that
addresses the diagnosis. Then it repeats.

Fitness (higher is better):
    return_pct  -  0.6 * drawdown_pct  +  0.02 * win_rate  -  0.1 * fee_drag

Diagnosis -> mutation (the successor inherits the base signal but changes the
thing that killed its parent):

    FEE_BLEED    avg net near -0.2%, few winners      -> bigger target, longer hold
    BAD_ENTRY    low MFE, win rate < 25%              -> tighter entry, lower risk
    LEAKED       peak green, closed red               -> lower target, tighter trail
    OVERTRADING  many closes per hour, flat P&L       -> longer hold, lower risk
    STOPPED_OUT  large share of stop exits            -> wider stop, smaller size

Lineage lives in state/lineage.json; the strategy engine reads it and only runs
the living. Books that die keep their state file for the post-mortem.

    python3 darwin.py --every 600 --min-trades 8
"""

import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LINEAGE = os.path.join(STATE, "lineage.json")

MUTATIONS = {
    "FEE_BLEED":   {"tp": 1.35, "hold": 1.5, "risk": 0.9},
    "BAD_ENTRY":   {"risk": 0.7, "hold": 1.2},
    "LEAKED":      {"tp": 0.75, "hold": 0.8},
    "OVERTRADING": {"hold": 1.6, "risk": 0.8},
    "STOPPED_OUT": {"sl": 1.25, "risk": 0.8},
}


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def load_lineage():
    try:
        with open(LINEAGE) as fh:
            return json.load(fh)
    except Exception:
        return {"generation": 1, "strategies": []}


def save_lineage(d):
    os.makedirs(STATE, exist_ok=True)
    tmp = LINEAGE + ".t"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, LINEAGE)


def load_books():
    out = {}
    for f in glob.glob(os.path.join(STATE, "strategy_*.json")):
        try:
            with open(f) as fh:
                d = json.load(fh)
            if (time.time() - os.path.getmtime(f)) > 900:
                continue          # stale: the engine stopped writing it
            out[d["engine"]] = d
        except Exception:
            continue
    return out


def diagnose(book):
    trades = book.get("trades") or []
    n = len(trades)
    if n == 0:
        return "NO_TRADES", "never found a qualifying signal"
    wins = sum(1 for t in trades if (t.get("net_pct") or 0) > 0)
    wr = 100.0 * wins / n
    avg = sum(t.get("net_pct") or 0 for t in trades) / n
    peaks = [t.get("peak_pct") or 0 for t in trades]
    mfe = sum(peaks) / n
    leaked = sum(1 for t in trades
                 if (t.get("peak_pct") or 0) >= 0.3 and (t.get("net_pct") or 0) <= 0)
    stops = sum(1 for t in trades if t.get("reason") == "stop")
    span = (trades[-1].get("exit_ms") or 0) - (trades[0].get("exit_ms") or 0)
    per_hour = n / max(0.02, span / 3.6e6) if span else 0

    if abs(avg + 0.2) < 0.08 and wr < 35:
        return "FEE_BLEED", "avg net %.3f%% with %.0f%% wins: paying the spread" % (avg, wr)
    if wr < 25 and mfe < 0.25:
        return "BAD_ENTRY", "win %.0f%% and MFE only %.2f%%: entries have no edge" % (wr, mfe)
    if leaked >= max(2, n * 0.3):
        return "LEAKED", "%d of %d peaked >=0.3%% then closed red" % (leaked, n)
    if per_hour > 30 and avg < 0:
        return "OVERTRADING", "%.0f closes/h at avg %.3f%%" % (per_hour, avg)
    if stops >= max(3, n * 0.5):
        return "STOPPED_OUT", "%d of %d exits were stops" % (stops, n)
    return "OUTSIDE", "no decisive failure mode (avg %.3f%%, win %.0f%%)" % (avg, wr)


def fitness(book):
    trades = book.get("trades") or []
    n = len(trades)
    if not n:
        return -999.0
    wins = sum(1 for t in trades if (t.get("net_pct") or 0) > 0)
    wr = 100.0 * wins / n
    ret = book.get("return_pct") or 0
    eq = book.get("equity") or 0
    start = book.get("start_equity") or 2000
    dd = max(0.0, 100.0 * (1 - eq / start))
    return ret - 0.6 * dd + 0.02 * wr


def mutate(base_params, diag):
    p = dict(base_params or {})
    for k, factor in MUTATIONS.get(diag, {}).items():
        if k in p:
            p[k] = round(p[k] * factor, 3)
    p["tp"] = round(min(3.0, max(0.4, p.get("tp", 1.0))), 2)
    p["sl"] = round(min(3.0, max(0.4, p.get("sl", 1.0))), 2)
    p["hold"] = int(min(3600, max(120, p.get("hold", 900))))
    p["risk"] = round(min(3.0, max(0.1, p.get("risk", 1.0))), 2)
    return p


def cycle(args):
    lin = load_lineage()
    if not lin["strategies"]:
        log("no lineage yet — run crypto_strategies.py once to seed it")
        return
    books = load_books()
    living = [s for s in lin["strategies"] if s.get("alive", True)]
    rows = []
    for s in living:
        b = books.get(s["name"])
        if not b:
            continue
        rows.append((fitness(b), s, b))

    if not rows:
        log("no live books with data yet")
        return

    rows.sort(key=lambda r: r[0])
    log("fitness ranking (worst first)")
    for f, s, b in rows:
        n = len(b.get("trades") or [])
        log("  %-18s %+7.2f  ret %+6.2f%%  trades %-3d gen %s" % (
            s["name"], f, b.get("return_pct") or 0, n, s.get("generation", 1)))

    # survival rule: kill anything below the bar that has enough evidence
    survivors, deaths = [], []
    for f, s, b in rows:
        n = len(b.get("trades") or [])
        if n >= args.min_trades and f < args.cull:
            deaths.append((f, s, b))
        else:
            survivors.append(s)

    if not deaths:
        log("no deaths this cycle (bar %.2f, min trades %d)" % (args.cull, args.min_trades))
        return

    gen = lin.get("generation", 1) + 1
    lin["generation"] = gen
    for f, s, b in deaths:
        diag, why = diagnose(b)
        s["alive"] = False
        s["died_ts"] = int(time.time() * 1000)
        s["diagnosis"] = diag
        s["cause"] = why
        s["final_return"] = b.get("return_pct")

        child_params = mutate(s.get("params") or {}, diag)
        child = {
            "name": "%s.g%d" % (s["name"].split(".g")[0], gen),
            "base": s.get("base"),
            "params": child_params,
            "generation": gen,
            "parent": s["name"],
            "alive": True,
            "born_ts": int(time.time() * 1000),
            "diagnosis": diag,
            "why": why,
        }

        # prefer a real code-level mutation: generate a new Python module
        try:
            sys.path.insert(0, HERE)
            from model_gen import generate as gen_model
            family = (s.get("base") or s["name"]).split(".g")[0].split("_")[0]
            rec = gen_model(family, diag, gen, why)
            child["module"] = rec["module"]
            child["params"] = dict(child_params, **{k: v for k, v in rec["params"].items()
                                                    if k in ("tp", "sl", "hold", "risk")})
            log("  generated %-18s (%s)" % (rec["name"], rec["module"]))
        except Exception as exc:
            log("  code generation failed (%r) — parameter mutation only" % (exc,))
        lin["strategies"].append(child)
        log("KILLED %-18s %s" % (s["name"], why))
        log("  born %-18s params %s" % (child["name"], json.dumps(child_params)))

    lin["strategies"] = [s for s in lin["strategies"] if s.get("alive", True)] + \
                        [s for s in lin["strategies"] if not s.get("alive", True)][-20:]
    lin["culled"] = (lin.get("culled") or 0) + len(deaths)
    save_lineage(lin)
    log("generation %d: %d dead, %d alive, lifetime culls %d" % (
        gen, len(deaths), len([s for s in lin["strategies"] if s.get("alive", True)]),
        lin["culled"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=600.0)
    ap.add_argument("--cull", type=float, default=-1.0,
                    help="fitness below this is killed")
    ap.add_argument("--min-trades", type=int, default=8)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    log("DARWIN — kill the unfit, diagnose them, breed a better one")
    log("survival bar: fitness >= %.2f after %d trades" % (args.cull, args.min_trades))
    while True:
        try:
            cycle(args)
        except Exception as exc:
            log("cycle failed: %r" % (exc,))
        if args.once:
            break
        time.sleep(max(60.0, args.every))


if __name__ == "__main__":
    main()
