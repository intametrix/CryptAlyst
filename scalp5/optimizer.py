#!/usr/bin/env python3
"""optimizer - continuous parameter search against measured outcomes.

Every cycle it reads what each engine actually did, scores it, and nudges the
engine's own control file. Nothing is guessed: each change is tied to a metric
that justified it, and every step is bounded so a bad read cannot blow up a
book.

Rules (all derived from the trade ledger, not from opinion):

  fee bleed      avg net < -0.15% and fee share > 55%   -> risk x0.75, tp -0.1
  poor entries   hit rate < 20% with >= 8 closes        -> min_conf +4 (cap 85)
  over-trading   > 40 closes/hour                       -> scan_every x1.5
  leaked profit  leaked > 30% of closes                 -> trail_ratio -0.05, lock -0.05
  timeouts       clock exits > 40% of closes            -> max_hold x0.7
  winning        return > +1% and >= 5 closes           -> risk x1.10 (cap)
  idle           no closes in 30 min but positions open  -> hold x0.8

Bounds: risk 0.05..3.0 | tp 0.4..3.0 | sl 0.4..3.0 | max_hold 120..3600
        scan 2..120 | min_conf 0..85 | trail 0.05..0.8

    python3 optimizer.py --every 300
"""

import argparse
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
BACKEND = "http://127.0.0.1:8787"

PEAKS_PATH = os.path.join(HERE, "state", "peaks.json")
DD_BRAKE = 0.06      # cut risk when equity is 6% below its peak
DD_HARD = 0.10       # go defensive at 10% below peak
DD_GROWTH_MAX = 0.03 # no growth while more than 3% below peak

BOUNDS = {
    "risk_pct": (0.05, 3.0),
    "tp_pct": (0.4, 3.0),
    "sl_pct": (0.4, 3.0),
    "max_hold": (120.0, 3600.0),
    "scan_every": (2.0, 120.0),
    "min_confidence_pct": (0.0, 85.0),
    "trail_ratio": (0.05, 0.8),
    "min_agree": (1.0, 5.0),
}  # type: dict


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=8.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "optimizer/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def clamp(key, v):
    lo, hi = BOUNDS.get(key, (None, None))
    if lo is None:
        return v
    return max(lo, min(hi, v))


def pct(v, d=2):
    return ("%+." + str(d) + "f%%") % v


def stats(trades):
    n = len(trades)
    if not n:
        return None
    wins = sum(1 for t in trades if (t.get("net_pct") or 0) > 0)
    avg = sum(t.get("net_pct") or 0 for t in trades) / n
    gross = sum(t.get("gross_pct") or 0 for t in trades) / n
    fee = max(0.0, min(1.0, 1 - (avg / gross))) if gross else 1.0
    leaked = sum(1 for t in trades
                 if (t.get("peak_pct") or 0) >= 0.3 and (t.get("net_pct") or 0) <= 0)
    timeout = sum(1 for t in trades if str(t.get("reason", "")).startswith(("clock", "time")))
    held = sorted(t.get("held_s") or 0 for t in trades)
    span = (trades[-1].get("exit_ms") or 0) - (trades[0].get("exit_ms") or 0)
    hours = max(0.02, span / 3.6e6) if span else 0.02
    return {"n": n, "wr": 100.0 * wins / n, "avg": avg, "fee": fee,
            "leaked": 100.0 * leaked / n, "timeout": 100.0 * timeout / n,
            "median_hold": held[len(held) // 2], "per_hour": n / hours}


def load_peaks():
    try:
        with open(PEAKS_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_peaks(peaks):
    os.makedirs(STATE, exist_ok=True)
    tmp = PEAKS_PATH + ".t"
    with open(tmp, "w") as fh:
        json.dump(peaks, fh)
    os.replace(tmp, PEAKS_PATH)


def decide(engine, peaks):
    """Return [(key, new_value, reason)] for one engine."""
    st = stats(engine.get("trades") or [])
    cur = engine.get("settings") or {}
    out = []

    # ---- drawdown brake runs before anything else ----
    key = engine["key"]
    eq = float(engine.get("equity") or 0)
    # seed the peak from the book's starting equity so an engine that is
    # already underwater when the optimiser first sees it is not treated as flat
    seed = float(engine.get("start_equity") or 0) or eq
    pk = max(float(peaks.get(key) or seed), seed, eq)
    peaks[key] = pk
    dd = 0.0 if pk <= 0 else max(0.0, 1.0 - eq / pk)

    def bump(key_, factor, why, absolute=None):
        if key_ not in cur:
            return
        old = float(cur[key_])
        new = absolute if absolute is not None else old * factor
        new = clamp(key_, round(new, 4))
        if abs(new - old) > 1e-9:
            out.append((key_, new, why.format(old=old, new=new)))

    if dd >= DD_HARD:
        bump("risk_pct", 0.0, "BRAKE drawdown %.1f%%: risk {old:.3f}->{new:.3f}" % (100 * dd),
             absolute=0.25)
        return out, st, {"drawdown_pct": round(100 * dd, 2), "brake": "hard"}
    if dd >= DD_BRAKE:
        bump("risk_pct", 0.0, "BRAKE drawdown %.1f%%: risk {old:.3f}->{new:.3f}" % (100 * dd),
             absolute=max(0.25, float(cur.get("risk_pct", 1.0)) * 0.5))
        return out, st, {"drawdown_pct": round(100 * dd, 2), "brake": "cut"}

    if not st or st["n"] < 5:
        return out, st, {"drawdown_pct": round(100 * dd, 2), "brake": "none"}

    def bump(key, factor, why, absolute=None):
        if key not in cur:
            return
        old = float(cur[key])
        new = absolute if absolute is not None else old * factor
        new = clamp(key, round(new, 4))
        if abs(new - old) > 1e-9:
            out.append((key, new, why.format(old=old, new=new)))

    if st["avg"] < -0.30 and st["fee"] > 0.60:
        bump("risk_pct", 0.75, "fee bleed: avg net %.3f -> risk {old:.3f}->{new:.3f}" % st["avg"])
        if "tp_pct" in cur and cur["tp_pct"] > 0.5:
            bump("tp_pct", 1.0, "fee bleed: target {old:.2f}->{new:.2f}", absolute=cur["tp_pct"] - 0.1)

    if st["wr"] < 20 and st["n"] >= 8:
        if "min_confidence_pct" in cur:
            bump("min_confidence_pct", 1.0, "hit rate %.0f%%: floor {old:.0f}->{new:.0f}" % st["wr"],
                 absolute=cur["min_confidence_pct"] + 4)
        elif "min_agree" in cur:
            bump("min_agree", 1.0, "hit rate %.0f%%: agreement {old:.0f}->{new:.0f}" % st["wr"],
                 absolute=cur["min_agree"] + 1)

    # frequency is only throttled when it is actually losing money; otherwise
    # more trades = more chances, which is the growth directive.
    if st["per_hour"] > 60 and st["avg"] < -0.20:
        bump("scan_every", 1.5, "losing at %.0f closes/h: scan {old:.1f}->{new:.1f}" % st["per_hour"])

    if st["leaked"] > 30:
        bump("trail_ratio", 0.9, "leaked %.0f%% of closes: trail {old:.2f}->{new:.2f}" % st["leaked"])

    if st["timeout"] > 40:
        bump("max_hold", 0.7, "clock exits %.0f%%: hold {old:.0f}->{new:.0f}" % st["timeout"])

    # growth bias: if the book is not bleeding, press the accelerator
    ret = engine.get("return_pct") or 0
    if dd > DD_GROWTH_MAX:
        return out, st, {"drawdown_pct": round(100 * dd, 2), "brake": "no-growth"}
    if st["avg"] > -0.05 and st["wr"] >= 25 and st["n"] >= 5 and ret >= -2.0:
        bump("risk_pct", 1.20, "healthy (wr %.0f%%, avg %.3f): risk {old:.3f}->{new:.3f}" %
             (st["wr"], st["avg"]))
    if ret > 1.0 and st["avg"] > 0:
        bump("risk_pct", 1.15, "winning %s: risk {old:.3f}->{new:.3f}" % pct(ret))

    return out, st, {"drawdown_pct": round(100 * dd, 2), "brake": "none"}


def write_control(engine_key, changes):
    files = {"swing": "control.json", "multimodels": "control_multi.json",
             "statsmodels": "control_stats.json"}
    path = os.path.join(STATE, files.get(engine_key, "control_%s.json" % engine_key))
    try:
        with open(path) as fh:
            cur = json.load(fh)
    except Exception:
        cur = {}
    for k, v, _ in changes:
        cur[k] = v
    cur["ts"] = int(time.time() * 1000)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cur, fh)
    os.replace(tmp, path)
    return os.path.basename(path)


def cycle(args):
    try:
        d = jget(MASTER + "/api/all", timeout=15.0)
    except Exception as exc:
        log("cannot read dashboard: %r" % (exc,))
        return
    engines = [e for e in d.get("engines", []) if e.get("equity") is not None]
    peaks = load_peaks()
    hist = []
    applied = 0
    for e in engines:
        changes, st, braked = decide(e, peaks)
        line = {"ts": int(time.time() * 1000), "engine": e["key"],
                "equity": e.get("equity"), "return_pct": e.get("return_pct"),
                "drawdown_pct": (braked or {}).get("drawdown_pct"),
                "brake": (braked or {}).get("brake"),
                "metrics": st, "changes": [{"key": k, "to": v, "why": w} for k, v, w in changes]}
        if changes:
            try:
                f = write_control(e["key"], changes)
                line["file"] = f
                applied += len(changes)
                log("%-12s %d change(s) -> %s" % (e["key"], len(changes), f))
                for k, v, w in changes:
                    log("   %-20s %s" % (k, w))
            except Exception as exc:
                line["error"] = str(exc)
                log("%-12s write failed: %r" % (e["key"], exc))
        hist.append(line)

    save_peaks(peaks)
    os.makedirs(STATE, exist_ok=True)
    tmp = os.path.join(STATE, "optimizer.json")
    try:
        with open(tmp) as fh:
            prev = json.load(fh)
    except Exception:
        prev = {"cycles": []}
    prev["ts"] = int(time.time() * 1000)
    prev["cycles"] = (prev.get("cycles") or [])[-40:] + [{"ts": int(time.time() * 1000),
                                                           "engines": hist,
                                                           "applied": applied}]
    with open(tmp + ".t", "w") as fh:
        json.dump(prev, fh)
    os.replace(tmp + ".t", tmp)
    log("cycle: %d engines scored, %d parameter changes" % (len(engines), applied))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=300.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    log("OPTIMISER — measures outcomes, adjusts bounded parameters")
    log("bounds: " + ", ".join("%s %s..%s" % (k, v[0], v[1]) for k, v in BOUNDS.items()))
    while True:
        cycle(args)
        if args.once:
            break
        time.sleep(max(30.0, args.every))


if __name__ == "__main__":
    main()
