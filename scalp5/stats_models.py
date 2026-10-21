#!/usr/bin/env python3
"""Statistical and regression models, traded as an ensemble. PAPER ONLY.

Five models that are fitted to the data rather than configured by hand:

  1. ols_trend      ordinary least squares on the last N closes: slope + R^2.
                    Only trades when the fit is strong (R^2 above a floor).
  2. zscore_revert  price z-score vs its own 50-bar mean; fades 2-sigma extremes.
  3. logistic       online logistic regression (SGD) on 5 features, trained on
                    every new bar as it completes. Weights start at zero and are
                    learned live -- nothing hard-coded.
  4. autocorr       lag-1 autocorrelation of returns. Positive -> momentum,
                    negative -> mean reversion. The regime picks the rule.
  5. squeeze_break  Bollinger-width percentile detects a squeeze, then trades the
                    first break of the 20-bar range.

Votes are summed; a position needs --min-agree models pointing the same way.

    python3 stats_models.py --seconds 14400
"""

import argparse
import json
import math
import os
import threading
import time
import urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('statsmodels', 'stats_models.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


BASE = "https://api.binance.com"
ALL_PRICES = BASE + "/api/v3/ticker/price"
KLINES = BASE + "/api/v3/klines?interval=%s&limit=%d&symbol=%s"
TICKER = BASE + "/api/v3/ticker/24hr"

STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USD1", "USDP", "DAI", "USDE",
           "AEUR", "EUR", "GBP", "TRY", "BRL", "ARS", "JPY", "XUSD", "USD"}
MODELS = ["ols_trend", "zscore_revert", "logistic", "autocorr", "squeeze_break"]


EDITABLE = ("risk_pct", "max_open_risk_pct", "max_positions", "tp_pct", "sl_pct",
            "trail_ratio", "trail_arm_pct", "lock_trade_pct", "lock_profit_pct",
            "lock_usd", "max_hold", "min_agree", "min_confidence_pct",
            "scan_every", "conviction_cap", "vol_mult", "tolerance", "min_stop_frac")


def settings_snapshot(args):
    return {k: getattr(args, k) for k in EDITABLE if hasattr(args, k)}


def apply_settings(args, c, extra=None):
    """Generic: any key that exists on args is coerced to its type and set."""
    changed = []
    for k, v in c.items():
        if k == "ts":
            continue
        if k == "hold_sec" and extra is not None:
            extra(float(v)); changed.append("hold_sec=" + str(v)); continue
        if not hasattr(args, k):
            continue
        cur = getattr(args, k)
        try:
            setattr(args, k, type(cur)(v))
            changed.append("%s=%s" % (k, getattr(args, k)))
        except Exception:
            pass
    return changed

def TRADING_ENABLED():
    """False when the sentinel has quarantined this model ('enabled': 0)."""
    try:
        import json as _j, os as _o
        p = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "state", "control_stats.json")
        with open(p) as fh:
            return bool(int(_j.load(fh).get("enabled", 1)))
    except Exception:
        return True


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "stats/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def all_prices():
    return {r["symbol"]: float(r["price"]) for r in jget(ALL_PRICES)}


def universe(n, min_vol):
    rows = []
    for r in jget(TICKER):
        s = r["symbol"]
        if not s.endswith("USDT") or s.endswith(("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        if s[:-4] in STABLES:
            continue
        try:
            if float(r["quoteVolume"]) < min_vol:
                continue
        except Exception:
            continue
        rows.append((float(r["quoteVolume"]), s))
    rows.sort(reverse=True)
    return [s for _, s in rows[:n]]


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def mean(v):
    return sum(v) / len(v) if v else 0.0


def std(v):
    if len(v) < 2:
        return 0.0
    m = mean(v)
    return (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5


def ols(y):
    """Least squares on (index, value): returns slope, r2, intercept."""
    n = len(y)
    if n < 3:
        return 0.0, 0.0, 0.0
    xs = list(range(n))
    mx, my = mean(xs), mean(y)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((xs[i] - mx) * (y[i] - my) for i in range(n))
    if sxx == 0:
        return 0.0, 0.0, my
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((v - my) ** 2 for v in y)
    ss_res = sum((y[i] - (intercept + slope * xs[i])) ** 2 for i in range(n))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, r2, intercept


def autocorr(xs, lag=1):
    n = len(xs)
    if n <= lag + 2:
        return 0.0
    m = mean(xs)
    den = sum((x - m) ** 2 for x in xs[lag:])
    if den <= 0:
        return 0.0
    num = sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, n))
    return num / den


def sigmoid(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


# --------------------------------------------------------------------------
# online logistic regression -- learned live, per symbol
# --------------------------------------------------------------------------
LOGIT = {}


def logit_features(c, rsi14, macd_hist, vol_ratio):
    r1 = (c[-1] / c[-2] - 1.0) if len(c) > 1 else 0.0
    r2 = (c[-2] / c[-3] - 1.0) if len(c) > 2 else 0.0
    return [r1 * 100, r2 * 100, (rsi14 - 50.0) / 50.0, macd_hist * 100, vol_ratio - 1.0]


def logit_predict(sym, feats):
    w = LOGIT.setdefault(sym, [0.0] * (len(feats) + 1))
    z = w[-1]
    for i, f in enumerate(feats):
        z += w[i] * f
    return sigmoid(z), w


def logit_train(sym, feats, label, lr=0.05):
    """One SGD step. label is 1 if the NEXT bar closed up, else 0."""
    p, w = logit_predict(sym, feats)
    err = label - p
    for i, f in enumerate(feats):
        w[i] += lr * err * f
    w[-1] += lr * err


# --------------------------------------------------------------------------
# the five models
# --------------------------------------------------------------------------
def m_ols_trend(x):
    c = x["close"]
    slope, r2, _ = ols(c[-30:])
    thr = 0.15
    if r2 >= thr and slope > 0:
        return 1, {"slope": round(slope, 8), "r2": round(r2, 3)}
    if r2 >= thr and slope < 0:
        return -1, {"slope": round(slope, 8), "r2": round(r2, 3)}
    return 0, {"r2": round(r2, 3)}


def m_zscore_revert(x):
    c = x["close"]
    window = c[-50:]
    m, s = mean(window), std(window)
    if s <= 0:
        return 0, {}
    z = (c[-1] - m) / s
    if z <= -2.0:
        return 1, {"z": round(z, 2)}
    if z >= 2.0:
        return -1, {"z": round(z, 2)}
    return 0, {"z": round(z, 2)}


def m_logistic(x):
    feats = x["feats"]
    p, _ = logit_predict(x["symbol"], feats)
    if p > 0.60:
        return 1, {"p": round(p, 3)}
    if p < 0.40:
        return -1, {"p": round(p, 3)}
    return 0, {"p": round(p, 3)}


def m_autocorr(x):
    c = x["close"]
    rets = [(c[i] / c[i - 1] - 1.0) for i in range(max(1, len(c) - 60), len(c))]
    ac = autocorr(rets, 1)
    last = rets[-1] if rets else 0.0
    if ac <= -0.10:                       # reversion regime -> fade the move
        return (1 if last < 0 else -1 if last > 0 else 0), {"ac": round(ac, 3)}
    if ac >= 0.10:                        # momentum regime -> follow it
        return (1 if last > 0 else -1 if last < 0 else 0), {"ac": round(ac, 3)}
    return 0, {"ac": round(ac, 3)}


def m_squeeze_break(x):
    c, h, l = x["close"], x["high"], x["low"]
    if len(c) < 60:
        return 0, {}
    widths = []
    for i in range(len(c) - 40, len(c)):
        w = c[max(0, i - 20):i]
        if len(w) >= 10:
            widths.append(2 * std(w) / mean(w) if mean(w) else 0)
    if not widths:
        return 0, {}
    cur = widths[-1]
    pct = sum(1 for w in widths if w < cur) / len(widths)
    if pct > 0.20:
        return 0, {"squeeze_pct": round(pct, 2)}
    upper, lower = max(h[-21:-1]), min(l[-21:-1])
    if c[-1] > upper:
        return 1, {"squeeze_pct": round(pct, 2)}
    if c[-1] < lower:
        return -1, {"squeeze_pct": round(pct, 2)}
    return 0, {"squeeze_pct": round(pct, 2)}


MODEL_FN = {"ols_trend": m_ols_trend, "zscore_revert": m_zscore_revert,
            "logistic": m_logistic, "autocorr": m_autocorr,
            "squeeze_break": m_squeeze_break}


def rsi(cl, period=14):
    if len(cl) < period + 1:
        return 50.0
    g = l = 0.0
    for i in range(1, period + 1):
        d = cl[i] - cl[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / period, l / period
    for i in range(period + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)


def macd_hist(cl):
    def em(v, p):
        k = 2.0 / (p + 1.0)
        e = v[0]
        for x in v[1:]:
            e = x * k + e * (1.0 - k)
        return e
    if len(cl) < 30:
        return 0.0
    line = em(cl, 12) - em(cl, 26)
    return line


def analyse(symbol, interval="5m"):
    import gofeed
    try:
        f = gofeed.bars(symbol, interval, 200)
    except Exception:
        return None
    c, h, l, qv = f["close"], f["high"], f["low"], f["quote"]
    if len(c) < 70:
        return None
    med = gofeed.indicator(f, "volume_median_20") or mean(qv[-21:-1])
    vol_ratio = (qv[-1] / med) if med else 1.0
    r = gofeed.indicator(f, "rsi14") or rsi(c)
    feats = logit_features(c, r, macd_hist(c), vol_ratio)
    return {"symbol": symbol, "close": c, "high": h, "low": l, "price": c[-1],
            "vol_ratio": vol_ratio, "feats": feats, "rsi": r}


def ensemble(x):
    votes, net = {}, 0
    for name in MODELS:
        sig, detail = MODEL_FN[name](x)
        votes[name] = {"signal": sig, "detail": detail}
        net += sig
    return net, votes


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=14400)
    ap.add_argument("--universe", type=int, default=300)
    ap.add_argument("--min-volume", type=float, default=20_000_000.0)
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--min-agree", type=int, default=2)
    ap.add_argument("--tp-pct", type=float, default=1.0)
    ap.add_argument("--sl-pct", type=float, default=1.0)
    ap.add_argument("--max-hold", type=float, default=600.0)
    ap.add_argument("--lock-usd", type=float, default=20.0)
    ap.add_argument("--scan-every", type=float, default=20.0)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--risk-pct", type=float, default=3.0)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--equity", type=float, default=2000.0)
    ap.add_argument("--state", default="state/stats_models.json")
    ap.add_argument("--ledger", default="ledger/stats_models.jsonl")
    args = ap.parse_args()

    log("STATS MODELS -- PAPER ONLY | %d fitted models | need %d agreeing"
        % (len(MODELS), args.min_agree))
    log("OLS slope/R2 · z-score · online logistic (SGD) · autocorrelation · squeeze breakout")
    syms = universe(args.universe, args.min_volume)
    log("universe %d: %s" % (len(syms), ", ".join(syms[:8])))

    prices, positions, trades = {}, {}, []
    equity = args.equity
    start_eq = equity
    FUNNEL = {"candidates": 0, "below_votes": 0, "opened": 0}
    opened = scans = 0
    last_lock = 0.0
    started = time.time()
    next_scan = 0.0
    votes_cache = {}
    _sig = {"ready": [], "at": 0.0}
    _lock = threading.Lock()

    def scanner():
        while True:
            try:
                ready = []
                for s in syms:
                    try:
                        x = analyse(s, args.interval)
                    except Exception:
                        continue
                    if not x:
                        continue
                    # online training: label = last completed bar's direction
                    c = x["close"]
                    if len(c) >= 2:
                        prev_feats = logit_features(c[:-1], x["rsi"],
                                                    macd_hist(c[:-1]), x["vol_ratio"])
                        label = 1.0 if c[-1] > c[-2] else 0.0
                        logit_train(s, prev_feats, label)
                    net, votes = ensemble(x)
                    votes_cache[s] = {"net": net, "votes": votes}
                    FUNNEL["candidates"] += 1
                    if abs(net) >= args.min_agree:
                        ready.append((abs(net), net, s, votes))
                    else:
                        FUNNEL["below_votes"] += 1
                ready.sort(reverse=True)
                with _lock:
                    _sig["ready"] = ready
                    _sig["at"] = time.time()
            except Exception:
                pass
            time.sleep(args.scan_every)
    threading.Thread(target=scanner, daemon=True).start()

    def dump():
        plist = []
        for s, p in positions.items():
            px = prices.get(s, p["entry"])
            raw = (px / p["entry"] - 1.0) * 100.0
            chg = raw if p["side"] == "buy" else -raw
            plist.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                          "price": px, "gross_pct": round(chg, 3),
                          "net_pct": round(chg - 200 * args.fee, 3),
                          "peak_pct": round(max(p.get("peak", 0.0), chg), 3),
                          "trail_line": round(-args.sl_pct, 3),
                          "held_s": round(time.time() - p["opened"], 1),
                "entry_ms": int(p["opened"] * 1000),
                          "conf": None, "pcal": None,
                          "alert_kind": "votes_%d" % p.get("net_votes", 0),
                          "why": p.get("why"), "decision": p.get("decision"),
                          "notional": p["notional"]})
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": "stats_models",
            "state": "running", "equity": round(equity, 4), "start_equity": start_eq,
            "return_pct": round(100 * (equity / start_eq - 1), 4),
            "realised": round(equity - start_eq, 4),
            "unrealised": round(sum((pp.get("notional") or 0) * (pp.get("gross_pct") or 0) / 100.0
                                    for pp in plist), 4),
            "tp_pct": args.tp_pct, "sl_pct": args.sl_pct,
            "trail_ratio": 0.0, "trail_arm_pct": 0.0, "max_hold": args.max_hold,
            "lock_usd": args.lock_usd, "scans": scans, "opened": opened,
            "positions": plist, "position": (plist[0] if plist else None),
            "trades": trades[-60:], "trade_count": len(trades),
            "wins": sum(1 for t in trades if t["net_pct"] > 0),
            "max_positions": args.max_positions, "watchlist_size": len(syms),
            "basis": "5 statistical models", "min_confidence": None,
            "open_risk_usd": round(sum(p["notional"] * args.sl_pct / 100.0
                                       for p in positions.values()), 2),
            "risk_pct": args.risk_pct,
            "settings": settings_snapshot(args),
            "funnel": dict(FUNNEL),
            "models": [{"name": m,
                        "voting": sum(1 for v in votes_cache.values()
                                      if v["votes"].get(m, {}).get("signal", 0) != 0)}
                       for m in MODELS],
            "logit_trained": {s: [round(w, 4) for w in LOGIT.get(s, [])]
                              for s in list(LOGIT)[:3]},
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.state)) or ".", exist_ok=True)
        tmp = args.state + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, args.state)

    CTRL = {"path": os.path.join(os.path.dirname(os.path.abspath(args.state)),
                                 "control_stats.json"), "mtime": 0.0}

    def apply_control():
        try:
            mt = os.path.getmtime(CTRL["path"])
        except OSError:
            return
        if mt <= CTRL["mtime"]:
            return
        CTRL["mtime"] = mt
        try:
            with open(CTRL["path"]) as fh:
                c = json.load(fh)
        except Exception:
            return
        ch = apply_settings(args, c, None)
        if ch:
            log("SETTINGS APPLIED -> " + ", ".join(ch))

    while time.time() - started < args.seconds:
        t0 = time.time()
        try:
            prices.update(all_prices())
        except Exception:
            pass



        apply_control()
        lock_now = False
        if args.lock_usd > 0 and (time.time() - last_lock) > 30 and positions:
            total = 0.0
            for s, p in positions.items():
                px = prices.get(s)
                if not px:
                    continue
                raw = (px / p["entry"] - 1.0) * 100.0
                total += p["notional"] * (raw if p["side"] == "buy" else -raw) / 100.0
            if total >= args.lock_usd:
                lock_now = True

        locked = 0
        for s in list(positions):
            p = positions[s]
            px = prices.get(s)
            if not px:
                continue
            raw = (px / p["entry"] - 1.0) * 100.0
            chg = raw if p["side"] == "buy" else -raw
            p["peak"] = max(p.get("peak", 0.0), chg)
            reason = None
            if lock_now and chg > 0:
                reason = "lock_usd"
            elif chg >= args.tp_pct:
                reason = "scrape"
            elif False:
                reason = "stop"   # profit-only: no loss exits
            elif (time.time() - p["opened"]) >= args.max_hold:
                reason = "clock"
            if reason:
                net = chg - 200 * args.fee
                pnl = p["notional"] * net / 100.0
                equity += pnl
                trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                               "exit": px, "gross_pct": round(chg, 3),
                               "net_pct": round(net, 3), "peak_pct": round(p["peak"], 3),
                               "reason": reason, "held_s": round(time.time() - p["opened"], 1),
                               "notional": round(p["notional"], 2),
                               "pnl_usd": round(pnl, 4), "equity_after": round(equity, 4),
                               "entry_ms": int(p["opened"] * 1000),
                               "exit_ms": int(time.time() * 1000),
                               "decision": p.get("decision")})
                log("<<< %-8s %-11s %-4s %+7.3f%% net %+7.3f%% eq %.2f"
                    % (reason, s, p["side"], chg, net, equity))
                positions.pop(s, None)
                if reason == "lock_usd":
                    locked += 1
        if locked:
            last_lock = time.time()

        _force = os.path.join(os.path.dirname(os.path.abspath(args.state)), "force_stats.json")
        if os.path.exists(_force):
            try:
                os.remove(_force)
            except Exception:
                pass
            next_scan = 0.0

        if time.time() >= next_scan:
            scans += 1
            next_scan = time.time() + args.scan_every
            free = (args.max_positions - len(positions)) if TRADING_ENABLED() else 0
            with _lock:
                ready = list(_sig["ready"])
            for _, net, s, votes in ready:
                if free <= 0:
                    break
                if s in positions:
                    continue
                px = prices.get(s)
                if not px:
                    continue
                side = "buy" if net > 0 else "sell"
                notional = min(equity * (args.risk_pct / 100.0) / (args.sl_pct / 100.0),
                               equity * 3)
                who = ",".join(m for m in MODELS if votes[m]["signal"] != 0)
                lg = votes["logistic"]["detail"].get("p")
                positions[s] = {"side": side, "entry": px, "opened": time.time(),
                                "notional": notional, "peak": 0.0, "net_votes": int(net),
                                "why": "%d/%d models: %s%s" % (
                                    abs(int(net)), len(MODELS), who,
                                    ("  p=%.2f" % lg) if lg is not None else "") ,
                                "decision": {"gates": {"min_agree": args.min_agree,
                                                       "net_votes": int(net)},
                                             "votes": {m: votes[m]["signal"] for m in MODELS},
                                             "features": {"p_logistic": lg, "score": net},
                                             "size_usd": round(notional, 2),
                                             "risk_usd": round(notional * args.sl_pct / 100.0, 2)}}
                opened += 1
                FUNNEL["opened"] += 1
                free -= 1
                who = ",".join(m for m in MODELS if votes[m]["signal"] != 0)
                log(">>> %-4s %-12s @ %-11.8g votes %+d [%s]"
                    % ("BUY" if side == "buy" else "SELL", s, px, net, who))

        dump()
        slack = 0.5 - (time.time() - t0)
        if slack > 0:
            time.sleep(slack)

    log("")
    log("=" * 80)
    log("opened %d | closed %d | wins %d | equity %.2f -> %.2f (%+.2f%%)"
        % (opened, len(trades), sum(1 for t in trades if t["net_pct"] > 0),
           start_eq, equity, 100 * (equity / start_eq - 1)))
    log("=" * 80)
    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
