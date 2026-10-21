#!/usr/bin/env python3
"""supervisor - keep the fleet alive, cut the losers, record why.

Runs unattended. Every cycle it:
  1. restarts any engine that has died
  2. reads every book and grades it
  3. disables a book that is losing on evidence (not on one bad trade)
  4. records the standings so there is a trail to review later

    python3 supervisor.py --every 60 --hours 3
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LOG = os.path.join(STATE, "supervisor.json")
DISABLED = os.path.join(STATE, "disabled.json")

# the universe ceiling: every model may choose from up to this many symbols.
# Set high enough to cover the whole liquid book, low enough that a scan is affordable.
MAX_SYMBOLS = 300

PY_ = sys.executable or "python3"

# name -> argv (relative to HERE)
ENGINES = {
    "alpharank":   [PY_, "-u", "alpha_rank.py", "--start", "5000", "--risk", "10",
                    "--every", "2", "--lock", "0.35", "--stop", "0.7"],
    "models10":    [PY_, "-u", "models10.py", "--start", "5000", "--top", "300",
                    "--loop", "2", "--probation", "300", "--only", "funding_fade"],
    "alphasignals":[PY_, "-u", "alpha_signals.py", "--every", "2", "--start", "5000", "--risk", "10"],
    "cryptopack":  [PY_, "-u", "crypto_pack.py", "--risk", "10", "--start", "5000",
                    "--top", "300", "--min-liq", "100000000", "--loop", "2"],
    "executor":    [PY_, "-u", "executor.py", "--risk", "10", "--lock", "0.50",
                    "--clock", "900", "--min-alert", "60", "--start", "5000"],
    "skimpump":    [PY_, "-u", "skim_pump.py", "--hold", "8", "--tp", "0.15",
                    "--sl", "0.30", "--max-open", "10", "--min-score", "65"],
    "curverider":  [PY_, "-u", "curve_rider.py", "--hold", "20", "--wait", "60",
                    "--risk", "10", "--start", "5000"],
    "marketagent": [PY_, "-u", "market_agent.py", "--every", "5", "--top", "300"],
    "movers":      [PY_, "-u", "movers.py", "--start", "5000", "--risk", "10",
                    "--check-at", "3", "--target", "0.05", "--stop", "1.0",
                    "--clock", "600", "--gap", "2", "--max-open", "6",
                    "--short-weight", "1.5"],
    # every model in the tree, each on its own $5,000 book
    "alpha":       [PY_, "-u", "alpha.py", "--top", "300", "--only", "funding_carry,xsec_momentum"],
    "multimodels": [PY_, "-u", "multi_models.py", "--equity", "5000"],
    "statsmodels": [PY_, "-u", "stats_models.py", "--equity", "5000"],
    "swingv2":     [PY_, "-u", "swing_v2.py", "--equity", "5000"],
    "skimai":      [PY_, "-u", "skim_ai.py", "--equity", "5000"],
    "sympathy":    [PY_, "-u", "sympathy_lag.py"],
    "ignition":    [PY_, "-u", "fast_ignition.py"],
    "flash":       [PY_, "-u", "flash.py", "--risk", "10"],
    "cryptostrat": [PY_, "-u", "crypto_strategies.py", "--only", "model_stack"],
    "cryptoedges": [PY_, "-u", "crypto_edges.py"],
    "churn":       [PY_, "-u", "churn.py", "--every", "10"],
    "slowedges":   [PY_, "-u", "slow_edges.py"],
        "hybrid_trend_pullback": [PY_, "-u", "hybrids.py", "--strategy", "trend_pullback", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_breakout_atr": [PY_, "-u", "hybrids.py", "--strategy", "breakout_atr", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_mean_revert_z": [PY_, "-u", "hybrids.py", "--strategy", "mean_revert_z", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_vwap_reclaim": [PY_, "-u", "hybrids.py", "--strategy", "vwap_reclaim", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_momentum_ignition": [PY_, "-u", "hybrids.py", "--strategy", "momentum_ignition", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_oi_squeeze": [PY_, "-u", "hybrids.py", "--strategy", "oi_squeeze", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_funding_fade": [PY_, "-u", "hybrids.py", "--strategy", "funding_fade", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_taker_imbalance": [PY_, "-u", "hybrids.py", "--strategy", "taker_imbalance", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_flow_confirm": [PY_, "-u", "hybrids.py", "--strategy", "flow_confirm", "--start", "5000", "--risk", "10", "--top", "300"],
    "hybrid_range_break": [PY_, "-u", "hybrids.py", "--strategy", "range_break", "--start", "5000", "--risk", "10", "--top", "300"],
"turtlemulti": [PY_, "-u", "turtle_multi.py", "--equity", "5000"],
    "swingturtle": [PY_, "-u", "swing_turtle.py", "--equity", "5000"],
    "scalpast":    [PY_, "-u", "scalp_fast.py"],
    "skim":        [PY_, "-u", "skim.py"],
    "grid":        [PY_, "-u", "grid.py", "--start", "5000", "--risk", "10",
                    "--symbols", "300", "--max-symbols", "15", "--levels", "8",
                    "--min-range", "0.05", "--max-range", "1.20", "--min-spacing", "0.10"],
        "hft":         [PY_, "-u", "hft.py", "--start", "5000", "--risk", "10",
                    "--symbols", "300", "--min-z", "1.8", "--min-imb", "0.15",
                    "--max-spread", "5", "--cost-multiple", "2.0",
                    "--target", "0.10", "--stop", "0.15", "--clock", "30",
                    "--max-open", "10"],
}

# pruning rules - evidence, not noise
MIN_TRADES = 10
STOP_RETURN = -2.0          # percent of its own book
STOP_USD = -200.0           # and at least this much in money, so cents never disable
STOP_STREAK = 6             # consecutive losses
GRACE_S = 600               # never judge a book in its first 10 minutes


def sh(cmd):
    return subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)


def running(name):
    pat = ENGINES[name][2]
    return sh(["pgrep", "-f", pat]).returncode == 0


def start(name):
    argv = ENGINES[name]
    log = os.path.join(HERE, "logs", "sup_%s.log" % name)
    with open(log, "ab") as fh:
        subprocess.Popen(argv, cwd=HERE, stdout=fh, stderr=fh, start_new_session=True)


def _norm(name):
    return name.replace("_", "").lower()


def disabled_set():
    return {_norm(n) for n in load_disabled()}


def _holds_positions(engine):
    import glob as _g, json as _j
    for pattern in ("pack_%s.json", "strategy_%s.json"):
        p = os.path.join(STATE, pattern % engine)
        try:
            d = _j.load(open(p))
        except Exception:
            continue
        if d.get("positions"):
            return True
    return False


def bus_guard():
    """Keep the bus honest: a readerless queue that fills is a backlog nobody wants.

    Market data is a snapshot. If a queue has no consumer and holds more than a
    thousand messages, it is deleted rather than left to grow - the lake and the live
    feed already hold the truth, and a deep queue only makes the health view lie.
    """
    import base64 as _b64, json as _json, urllib.request as _u
    auth = {"Authorization": "Basic " + _b64.b64encode(b"cryptalyst:cryptalyst").decode()}
    base = "http://127.0.0.1:15672/api"
    try:
        req = _u.Request(base + "/queues", headers=auth)
        queues = _json.loads(_u.urlopen(req, timeout=5).read())
    except Exception:
        return []
    actions = []
    for q in queues:
        name = q.get("name") or ""
        msgs = q.get("messages", 0)
        if name.startswith("stomp-subscription-"):
            continue
        # A STOMP /queue/ subscription creates a durable, unbounded queue. The intel
        # family must be neither: delete a non-conforming one and let the reader
        # reconnect, which now declares it bounded and self-expiring.
        if name.startswith("i.") and not q.get("auto_delete"):
            try:
                _u.urlopen(_u.Request(base + "/queues/%%2F/" + name, method="DELETE",
                                      headers=auth), timeout=5).read()
                actions.append("bus: reset %s to a bounded queue" % name)
            except Exception:
                pass
            continue
        if q.get("consumers", 0) == 0 and (msgs >= 1000 or (name.startswith("i.") and msgs)):
            try:
                _u.urlopen(_u.Request(base + "/queues/%%2F/" + name, method="DELETE",
                                      headers=auth), timeout=5).read()
                actions.append("bus: dropped %s (%d msgs, no consumer)" % (name, msgs))
            except Exception:
                pass
    return actions


def load_disabled():
    try:
        return set(json.load(open(DISABLED)).get("books") or [])
    except Exception:
        return set()


def save_disabled(books):
    with open(DISABLED + ".t", "w") as fh:
        json.dump({"books": sorted(books), "ts": int(time.time() * 1000)}, fh)
    os.replace(DISABLED + ".t", DISABLED)


def books():
    out = []
    for fn in sorted(os.listdir(STATE)):
        if fn.startswith("pack_") and fn.endswith(".json"):
            try:
                with open(os.path.join(STATE, fn)) as fh:
                    out.append(json.load(fh))
            except Exception:
                pass
    return out


def grade():
    rows = []
    now = time.time()
    for b in books():
        trades = b.get("trades") or []
        n = len(trades)
        eq = b.get("equity") or 0
        start = b.get("start_equity") or 5000
        ret = 100 * (eq / start - 1) if start else 0
        wins = sum(1 for t in trades if (t.get("net_pct") or 0) > 0)
        streak = 0
        for t in reversed(trades):
            if (t.get("net_pct") or 0) <= 0:
                streak += 1
            else:
                break
        avg = (sum(t.get("net_pct") or 0 for t in trades) / n) if n else 0
        age = now - min([t.get("entry_ms") or 0 for t in trades] or [0]) / 1000 if trades else 0
        rows.append({"engine": b.get("engine"), "trades": n, "equity": round(eq, 2),
                     "return_pct": round(ret, 3), "win_pct": round(100 * wins / n, 1) if n else 0,
                     "avg_net": round(avg, 4), "streak": streak,
                     "open": len(b.get("positions") or []),
                     "age_s": round(now - (b.get("ts") or 0) / 1000, 0) if b.get("ts") else 0})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=60.0)
    ap.add_argument("--hours", type=float, default=3.0)
    a = ap.parse_args()
    deadline = time.time() + a.hours * 3600
    history = []
    print("SUPERVISOR — every %.0fs for %.1fh" % (a.every, a.hours), flush=True)

    while time.time() < deadline:
        actions = []
        # 1. keep the fleet alive
        dis_norm = disabled_set()
        for name in ENGINES:
            # the disabled list names scripts as often as registry keys, so both are
            # checked: "fast_ignition" in disabled.json is the "ignition" registry entry
            script = os.path.basename(ENGINES[name][2])
            stem = script[:-3] if script.endswith(".py") else script
            if _norm(name) in dis_norm or _norm(stem) in dis_norm:
                continue          # A6: a disabled book influences nothing
            if not running(name):
                start(name)
                actions.append("restarted %s" % name)

        # 2. grade and prune
        # owners' cash-in rules: bank a book that reached its % or its dollar target
        try:
            import master as _m
            for _a in _m.check_cashins():
                actions.append(_a)
            for _a in _m.check_short_bank():
                actions.append(_a)
        except Exception as _e:
            actions.append("cashin: %s" % _e)

        # the hard stop runs as its own process too
        try:
            if subprocess.run(["pgrep", "-f", "stop_loss.py"],
                              capture_output=True).returncode != 0:
                with open(os.path.join(HERE, "logs", "stop_loss.log"), "ab") as fh:
                    subprocess.Popen([PY_, "-u", os.path.join("tools", "stop_loss.py")],
                                     cwd=HERE, stdout=fh, stderr=fh, start_new_session=True)
                actions.append("stop_loss started")
        except Exception as _e:
            actions.append("stop_loss: %s" % _e)

        # the 30-second loser rule runs as its own process, kept alive here
        try:
            if subprocess.run(["pgrep", "-f", "loss_watch.py"], capture_output=True).returncode != 0:
                with open(os.path.join(HERE, "logs", "loss_watch.log"), "ab") as fh:
                    subprocess.Popen([PY_, "-u", os.path.join("tools", "loss_watch.py")],
                                     cwd=HERE, stdout=fh, stderr=fh, start_new_session=True)
                actions.append("loss_watch started")
        except Exception as _e:
            actions.append("loss_watch: %s" % _e)

        for _a in bus_guard():
            actions.append(_a)

        # earned capital: recompute the allocation table from the packs each tick
        try:
            import capital as _cap
            _rows = _cap.compute()
            _cap.write(_rows)
            promoted = [r["engine"] for r in _rows if r["risk_mult"] > 1.0]
            if promoted:
                actions.append("capital: %s" % ", ".join(promoted[:6]))
        except Exception as _e:
            actions.append("capital: %s" % _e)

        rows = grade()
        dis = load_disabled()
        for r in rows:
            eng = r["engine"]
            if eng in dis or r["trades"] < MIN_TRADES:
                continue
            # a book holding positions is left alone: disabling it would settle a loss
            if _holds_positions(eng):
                continue
            if False:
                continue
            if r["return_pct"] <= STOP_RETURN and (r.get("pnl") or 0) <= STOP_USD:
                dis.add(eng); actions.append("DISABLED %s (return %.2f%%)" % (eng, r["return_pct"]))
            elif r["streak"] >= STOP_STREAK:
                dis.add(eng); actions.append("DISABLED %s (%d straight losses)" % (eng, r["streak"]))
        save_disabled(dis)
        try:
            import sys as _s
            _s.path.insert(0, HERE)
            from lib import bus as _bus
            for r in rows:
                _bus.pub("model.pnl", r["engine"],
                         {"engine": r["engine"], "equity": r["equity"],
                          "return_pct": r["return_pct"], "trades": r["trades"],
                          "open": r["open"]}, "supervisor")
        except Exception:
            pass

        equity = sum(r["equity"] for r in rows)
        entry = {"ts": int(time.time() * 1000), "equity": round(equity, 2),
                 "books": len(rows), "open": sum(r["open"] for r in rows),
                 "closed": sum(r["trades"] for r in rows),
                 "actions": actions,
                 "standings": sorted(rows, key=lambda x: -x["return_pct"])[:20]}
        history.append(entry)
        history = history[-500:]
        with open(LOG + ".t", "w") as fh:
            json.dump({"updated": int(time.time() * 1000),
                       "hours": a.hours, "history": history,
                       "disabled": sorted(dis)}, fh)
        os.replace(LOG + ".t", LOG)
        print("%s  equity $%.0f  books %d  open %d  closed %d  %s" % (
            time.strftime("%H:%M:%S"), equity, len(rows), entry["open"], entry["closed"],
            "; ".join(actions) if actions else ""), flush=True)
        time.sleep(a.every)

    print("supervisor finished its %.1fh window" % a.hours, flush=True)


if __name__ == "__main__":
    main()
