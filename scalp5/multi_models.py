#!/usr/bin/env python3
"""Five self-adjusting technical models, traded as one ensemble. PAPER ONLY.

Every model tunes itself to the symbol's CURRENT volatility instead of using a
fixed parameter set. The adaptation is measured, not guessed: the 20-bar ATR%
decides which regime the symbol is in, and each model shifts its own periods,
thresholds or bands accordingly.

  1. adaptive_ma        EMA fast/slow, periods scale with the volatility regime
  2. adaptive_rsi       momentum thresholds widen when the tape is fast
  3. adaptive_macd      MACD periods accelerate in high volatility
  4. adaptive_donchian  channel length shortens as volatility rises
  5. adaptive_bbands    Bollinger multiplier widens with volatility

Each returns -1 / 0 / +1. The ensemble nets them and trades when at least
--min-agree models point the same way, so a single model can never open a
position on its own.

Exits: +tp% scrape, -sl% stop, --max-hold clock, and a $ lock on the book.

    python3 multi_models.py --seconds 14400
"""

import argparse
import json
import os
import threading
import time
import urllib.request

BASE = "https://api.binance.com"
ALL_PRICES = BASE + "/api/v3/ticker/price"
KLINES = BASE + "/api/v3/klines?interval=%s&limit=%d&symbol=%s"
TICKER = BASE + "/api/v3/ticker/24hr"

STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USD1", "USDP", "DAI", "USDE",
           "AEUR", "EUR", "GBP", "TRY", "BRL", "ARS", "JPY", "XUSD", "USD"}
MODELS = ["adaptive_ma", "adaptive_rsi", "adaptive_macd",
          "adaptive_donchian", "adaptive_bbands"]


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
        p = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "state", "control_multi.json")
        with open(p) as fh:
            return bool(int(_j.load(fh).get("enabled", 1)))
    except Exception:
        return True


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "multi/1.0"})
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
# indicators
# --------------------------------------------------------------------------
def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
        out.append(e)
    return out


def atr(h, l, c, period=14):
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
           for i in range(1, len(c))]
    if len(trs) < period:
        return None
    v = sum(trs[:period]) / period
    for tr in trs[period:]:
        v = (v * (period - 1) + tr) / period
    return v


def rsi(cl, period):
    if len(cl) < period + 1:
        return None
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


def stdev(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5


# --------------------------------------------------------------------------
# the five models -- each returns (signal, detail)
# --------------------------------------------------------------------------
def m_adaptive_ma(x):
    """EMA cross with the periods chosen by the volatility regime."""
    c, atr_pct = x["close"], x["atr_pct"]
    if atr_pct is None:
        return 0, {}
    fast = 7 if atr_pct > 1.2 else (9 if atr_pct > 0.6 else 13)
    slow = fast * 3
    if len(c) < slow + 2:
        return 0, {}
    ef, es = ema(c, fast)[-1], ema(c, slow)[-1]
    prev_f, prev_s = ema(c[:-1], fast)[-1], ema(c[:-1], slow)[-1]
    if ef > es and prev_f <= prev_s:
        return 1, {"fast": fast, "slow": slow}
    if ef < es and prev_f >= prev_s:
        return -1, {"fast": fast, "slow": slow}
    return 0, {"fast": fast, "slow": slow}


def m_adaptive_rsi(x):
    """Momentum RSI whose thresholds widen as volatility rises."""
    atr_pct = x["atr_pct"] or 0.5
    band = min(30.0, max(12.0, 8.0 + 12.0 * atr_pct))
    r = rsi(x["close"], 14)
    if r is None:
        return 0, {}
    hi, lo = 50 + band, 50 - band
    if r >= hi:
        return 1, {"rsi": round(r, 1), "band": round(band, 1)}
    if r <= lo:
        return -1, {"rsi": round(r, 1), "band": round(band, 1)}
    return 0, {"rsi": round(r, 1), "band": round(band, 1)}


def m_adaptive_macd(x):
    """MACD periods shorten in fast markets so it reacts sooner."""
    c, atr_pct = x["close"], x["atr_pct"] or 0.5
    fast, slow = (8, 21) if atr_pct > 1.0 else (12, 26)
    if len(c) < slow + 10:
        return 0, {}
    ef, es = ema(c, fast), ema(c, slow)
    line = [ef[i] - es[i] for i in range(len(c))]
    sig = ema(line, 9)
    hist = line[-1] - sig[-1]
    prev = line[-2] - sig[-2]
    if hist > 0 and hist >= prev:
        return 1, {"fast": fast, "slow": slow, "hist": round(hist, 8)}
    if hist < 0 and hist <= prev:
        return -1, {"fast": fast, "slow": slow, "hist": round(hist, 8)}
    return 0, {}


def m_adaptive_donchian(x):
    """Channel length shortens as volatility rises, so breakouts come sooner."""
    atr_pct = x["atr_pct"] or 0.5
    n = 10 if atr_pct > 1.0 else (20 if atr_pct > 0.5 else 30)
    h, l, c = x["high"], x["low"], x["close"]
    if len(c) < n + 2:
        return 0, {}
    upper, lower = max(h[-n - 1:-1]), min(l[-n - 1:-1])
    if c[-1] > upper:
        return 1, {"n": n, "level": round(upper, 8)}
    if c[-1] < lower:
        return -1, {"n": n, "level": round(lower, 8)}
    return 0, {}


def m_adaptive_bbands(x):
    """Bollinger breakout whose multiplier widens with volatility."""
    atr_pct = x["atr_pct"] or 0.5
    k = 2.5 if atr_pct > 1.0 else (2.0 if atr_pct > 0.5 else 1.6)
    c = x["close"]
    n = 20
    if len(c) < n + 2:
        return 0, {}
    window = c[-n:]
    mid = sum(window) / n
    sd = stdev(window)
    if sd <= 0:
        return 0, {}
    if c[-1] > mid + k * sd:
        return 1, {"k": k, "mid": round(mid, 8)}
    if c[-1] < mid - k * sd:
        return -1, {"k": k, "mid": round(mid, 8)}
    return 0, {}


MODEL_FN = {"adaptive_ma": m_adaptive_ma, "adaptive_rsi": m_adaptive_rsi,
            "adaptive_macd": m_adaptive_macd, "adaptive_donchian": m_adaptive_donchian,
            "adaptive_bbands": m_adaptive_bbands}


def analyse(symbol, interval="5m", bars=200):
    import gofeed
    try:
        f = gofeed.bars(symbol, interval, bars)
    except Exception:
        return None
    h, l, c = f["high"], f["low"], f["close"]
    if len(c) < 60:
        return None
    a = gofeed.indicator(f, "atr20") or atr(h, l, c, 14)
    atr_pct = (100.0 * a / c[-1]) if a and c[-1] else None
    return {"symbol": symbol, "close": c, "high": h, "low": l,
            "price": c[-1], "atr": a, "atr_pct": atr_pct}


def ensemble(x):
    votes = {}
    net = 0
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
    ap.add_argument("--min-agree", type=int, default=2,
                    help="at least this many models must point the same way")
    ap.add_argument("--tp-pct", type=float, default=1.0)
    ap.add_argument("--sl-pct", type=float, default=1.0)
    ap.add_argument("--max-hold", type=float, default=600.0)
    ap.add_argument("--lock-usd", type=float, default=20.0)
    ap.add_argument("--scan-every", type=float, default=20.0)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--risk-pct", type=float, default=2.0)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--equity", type=float, default=2000.0)
    ap.add_argument("--state", default="state/multi_models.json")
    ap.add_argument("--ledger", default="ledger/multi_models.jsonl")
    args = ap.parse_args()

    log("MULTI-MODEL -- PAPER ONLY | %d self-adjusting models | need %d agreeing votes"
        % (len(MODELS), args.min_agree))
    log("exits: +%.1f%% scrape | -%.1f%% stop | %.0fs clock | $%.0f book lock"
        % (args.tp_pct, args.sl_pct, args.max_hold, args.lock_usd))
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
    last_votes = {}
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
                    net, votes = ensemble(x)
                    last_votes[s] = {"net": net, "votes": votes,
                                     "atr_pct": x["atr_pct"]}
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
            v = last_votes.get(s, {})
            plist.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                          "price": px, "gross_pct": round(chg, 3),
                          "net_pct": round(chg - 200 * args.fee, 3),
                          "peak_pct": round(max(p.get("peak", 0.0), chg), 3),
                          "trail_line": round(-args.sl_pct, 3),
                          "held_s": round(time.time() - p["opened"], 1),
                "entry_ms": int(p["opened"] * 1000),
                          "conf": None, "pcal": None,
                          "alert_kind": "ensemble_%d" % p.get("net_votes", 0),
                          "why": p.get("why"), "decision": p.get("decision"),
                          "notional": p["notional"]})
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": "multi_models",
            "state": "running", "equity": round(equity, 4), "start_equity": start_eq,
            "return_pct": round(100 * (equity / start_eq - 1), 4),
            "tp_pct": args.tp_pct, "sl_pct": args.sl_pct,
            "trail_ratio": 0.0, "trail_arm_pct": 0.0, "max_hold": args.max_hold,
            "lock_usd": args.lock_usd,
            "scans": scans, "opened": opened,
            "positions": plist, "position": (plist[0] if plist else None),
            "trades": trades[-60:], "trade_count": len(trades),
            "wins": sum(1 for t in trades if t["net_pct"] > 0),
            "max_positions": args.max_positions, "watchlist_size": len(syms),
            "basis": "5 adaptive models", "min_confidence": None,
            "open_risk_usd": round(sum(p["notional"] * args.sl_pct / 100.0
                                       for p in positions.values()), 2),
            "risk_pct": args.risk_pct,
            "settings": settings_snapshot(args),
            "funnel": dict(FUNNEL),
            "models": [{"name": m,
                        "voting": sum(1 for v in last_votes.values()
                                      if v["votes"].get(m, {}).get("signal", 0) != 0)}
                       for m in MODELS],
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.state)) or ".", exist_ok=True)
        tmp = args.state + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, args.state)

    CTRL = {"path": os.path.join(os.path.dirname(os.path.abspath(args.state)),
                                 "control_multi.json"), "mtime": 0.0}

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
        except Exception as exc:
            log("prices failed: %r" % (exc,))



        apply_control()
        # ---- book lock -----------------------------------------------
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
                log("LOCK: unrealised $%.2f >= $%.0f" % (total, args.lock_usd))

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
            elif chg <= -args.sl_pct:
                reason = "stop"
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
            log("LOCKED IN %d, %d still open" % (locked, len(positions)))

        # ---- entries from the ensemble --------------------------------
        _force = os.path.join(os.path.dirname(os.path.abspath(args.state)), "force_multimodels.json")
        if os.path.exists(_force):
            try:
                os.remove(_force)
            except Exception:
                pass
            next_scan = 0.0
            log("FORCED rescan")
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
                notional = equity * (args.risk_pct / 100.0) / (args.sl_pct / 100.0)
                who = ",".join(m.replace("adaptive_", "") for m in MODELS
                               if votes[m]["signal"] != 0)
                positions[s] = {"side": side, "entry": px, "opened": time.time(),
                                "notional": notional, "peak": 0.0, "net_votes": int(net),
                                "why": "%d/%d models agreed: %s" % (abs(int(net)), len(MODELS), who),
                                "decision": {"gates": {"min_agree": args.min_agree,
                                                       "net_votes": int(net)},
                                             "votes": {m: votes[m]["signal"] for m in MODELS},
                                             "features": {"atr_pct": None, "score": net},
                                             "size_usd": round(notional, 2),
                                             "risk_usd": round(notional * args.sl_pct / 100.0, 2)}}
                opened += 1
                FUNNEL["opened"] += 1
                free -= 1
                who = ",".join(m.replace("adaptive_", "") for m in MODELS
                               if votes[m]["signal"] != 0)
                log(">>> %-4s %-12s @ %-11.8g votes %+d [%s]" % (
                    "BUY" if side == "buy" else "SELL", s, px, net, who))

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
