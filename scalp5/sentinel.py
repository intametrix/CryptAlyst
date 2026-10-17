#!/usr/bin/env python3
"""sentinel - watches every model, parks the ones that fail, tests fixes,
and only lets a model trade again once it has earned it.

Lifecycle per model:

    live ──(fails thresholds)──> parked ──> testing(candidate 1)
      ^                                          │
      └────(trial passes: promote)───────────────┤
                                                 └─(fails: next candidate)
                                                      └─(all exhausted: parked)

"parked" sets `enabled: 0` in the model's control file, which the engines honour
by refusing new entries while still managing open positions.

Every decision is written to state/sentinel.json so the UI can show why a model
is parked and what it is currently testing.

    python3 sentinel.py --every 120 --trial 600
"""

import argparse
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
SENT_PATH = os.path.join(STATE, "sentinel.json")

CONTROL_FILES = {"swing": "control.json", "multimodels": "control_multi.json",
                 "statsmodels": "control_stats.json"}

# a candidate is a parameter patch tried while the model is proving itself
CANDIDATES = {
    "swing": [
        {"min_confidence_pct": 78, "risk_pct": 1.0, "scan_every": 2.0},
        {"min_confidence_pct": 82, "risk_pct": 0.75, "lock_trade_pct": 0.35},
        {"min_confidence_pct": 75, "risk_pct": 0.5, "trail_ratio": 0.25},
    ],
    "multimodels": [
        {"min_agree": 4, "risk_pct": 1.0},
        {"min_agree": 3, "risk_pct": 0.75, "tp_pct": 1.2},
        {"min_agree": 2, "risk_pct": 0.5, "scan_every": 45},
    ],
    "statsmodels": [
        {"min_agree": 3, "risk_pct": 1.0},
        {"min_agree": 2, "risk_pct": 0.75, "tp_pct": 1.2},
        {"min_agree": 2, "risk_pct": 0.5, "scan_every": 45},
    ],
}

# fail / pass thresholds
FAIL_AVG = -0.25          # average net per trade
FAIL_WR = 18.0            # win rate %
FAIL_DD = 6.0             # drawdown %
MIN_CLOSES = 8
PASS_AVG = -0.05
PASS_WR = 25.0


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=12.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "sentinel/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def load():
    try:
        with open(SENT_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {"models": {}, "events": []}


def save(st):
    os.makedirs(STATE, exist_ok=True)
    tmp = SENT_PATH + ".t"
    with open(tmp, "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, SENT_PATH)


def write_control(engine, patch):
    path = os.path.join(STATE, CONTROL_FILES.get(engine, "control_%s.json" % engine))
    try:
        with open(path) as fh:
            cur = json.load(fh)
    except Exception:
        cur = {}
    cur.update(patch)
    cur["ts"] = int(time.time() * 1000)
    tmp = path + ".t"
    with open(tmp, "w") as fh:
        json.dump(cur, fh)
    os.replace(tmp, path)


def metrics(engine):
    ts = engine.get("trades") or []
    n = len(ts)
    if not n:
        return {"n": 0, "wr": None, "avg": None}
    wins = sum(1 for t in ts if (t.get("net_pct") or 0) > 0)
    avg = sum(t.get("net_pct") or 0 for t in ts) / n
    return {"n": n, "wr": 100.0 * wins / n, "avg": avg}


def evaluate(st, engines, args):
    peaks = {}
    try:
        with open(os.path.join(STATE, "peaks.json")) as fh:
            peaks = json.load(fh)
    except Exception:
        pass

    now = time.time()
    changed = 0
    for e in engines:
        key = e["key"]
        if key not in CONTROL_FILES:
            continue                       # only models we can control
        stt = st["models"].setdefault(key, {"status": "live", "candidate": 0,
                                            "trial_started": 0, "reason": "",
                                            "since": int(now)})
        m = metrics(e)
        eq = e.get("equity") or 0
        seed = e.get("start_equity") or eq or 1
        pk = max(peaks.get(key) or seed, seed, eq)
        dd = 0.0 if pk <= 0 else max(0.0, 100.0 * (1 - eq / pk))

        def event(msg):
            st["events"] = (st.get("events") or [])[-120:] + [
                {"ts": int(now * 1000), "engine": key, "msg": msg}]

        if stt["status"] == "live":
            fail = (m["n"] >= MIN_CLOSES and
                    ((m["avg"] is not None and m["avg"] < FAIL_AVG) or
                     (m["wr"] is not None and m["wr"] < FAIL_WR) or dd >= FAIL_DD))
            if fail:
                write_control(key, {"enabled": 0})
                stt.update({"status": "testing", "candidate": 0, "trial_started": int(now),
                            "reason": "failed: avg %.3f wr %s dd %.1f%%" % (
                                m["avg"] if m["avg"] is not None else 0,
                                ("%.0f%%" % m["wr"]) if m["wr"] is not None else "—", dd)})
                patch = CANDIDATES[key][0]
                write_control(key, dict(patch, enabled=1))
                event("parked then started trial 1: " + json.dumps(patch))
                changed += 1
                log("%-12s FAIL -> trial 1 %s" % (key, patch))

        elif stt["status"] == "testing":
            elapsed = now - stt.get("trial_started", 0)
            if elapsed >= args.trial:
                passed = (m["n"] >= 4 and m["avg"] is not None and m["avg"] > PASS_AVG
                          and m["wr"] is not None and m["wr"] >= PASS_WR and dd < FAIL_DD)
                if passed:
                    stt.update({"status": "live", "reason": "promoted after trial %d" %
                                (stt["candidate"] + 1), "since": int(now)})
                    event("PROMOTED with candidate %d" % (stt["candidate"] + 1))
                    log("%-12s PASS -> promoted with candidate %d" % (key, stt["candidate"] + 1))
                else:
                    nxt = stt["candidate"] + 1
                    if nxt < len(CANDIDATES[key]):
                        stt.update({"candidate": nxt, "trial_started": int(now)})
                        patch = CANDIDATES[key][nxt]
                        write_control(key, dict(patch, enabled=1))
                        event("trial %d failed, starting %d: %s" % (nxt, nxt + 1, json.dumps(patch)))
                        log("%-12s trial %d failed -> trial %d" % (key, nxt, nxt + 1))
                    else:
                        write_control(key, {"enabled": 0})
                        stt.update({"status": "parked", "reason": "all candidates failed"})
                        event("PARKED — every candidate failed, needs review")
                        log("%-12s PARKED (all candidates failed)" % key)
                changed += 1
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=120.0)
    ap.add_argument("--trial", type=float, default=600.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    log("SENTINEL — park failures, test fixes, promote winners")
    log("fail: avg<%.2f%% or wr<%.0f%% or dd>=%.0f%% (>=%d closes) | pass: avg>%.2f%% and wr>=%.0f%%"
        % (FAIL_AVG, FAIL_WR, FAIL_DD, MIN_CLOSES, PASS_AVG, PASS_WR))
    while True:
        try:
            d = jget(MASTER + "/api/all")
            st = load()
            n = evaluate(st, d.get("engines") or [], args)
            st["ts"] = int(time.time() * 1000)
            save(st)
            if n:
                log("cycle: %d model change(s)" % n)
        except Exception as exc:
            log("cycle failed: %r" % (exc,))
        if args.once:
            break
        time.sleep(max(30.0, args.every))


if __name__ == "__main__":
    main()
