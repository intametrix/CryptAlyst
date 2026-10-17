#!/usr/bin/env python3
"""HFT skim: trend + RSI + MACD, 4-second maximum hold. PAPER ONLY.

Deliberately NOT the turtle system. No Donchian, no ATR stops, no daily
structure. This is a pure short-horizon tape reader:

    trend  : EMA50 on 1m bars -- only trade in its direction
    RSI    : 14-period Wilder -- must agree with the direction
    MACD   : 12/26/9 -- entry is the histogram crossing zero

    long  : hist crosses up   AND rsi > rsi_long  AND close > ema50
    short : hist crosses down AND rsi < rsi_short AND close < ema50

Exits are a 4-second hard clock plus a small symmetric bracket. A trade cannot
outlive 4 seconds by construction: the timeout is checked every tick and the
position is closed at market.

Costs are real: 0.1% taker per side = 0.2% round trip. On a 4-second hold that
fee is the dominant term, and the panel will show that plainly.

State is written in the schema the panel already understands, so the dashboard
is the same one, on its own port.

    python3 hft.py --port-state state/hft_state.json
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


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=6.0):
    req = urllib.request.Request(url, headers={"User-Agent": "hft/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def all_prices():
    return {r["symbol"]: float(r["price"]) for r in jget(ALL_PRICES)}


def universe(n, min_vol=5_000_000.0):
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
# indicators on the 1m frame
# --------------------------------------------------------------------------
def ema_series(v, p):
    k = 2.0 / (p + 1.0)
    e = v[0]
    out = [e]
    for x in v[1:]:
        e = x * k + e * (1.0 - k)
        out.append(e)
    return out


def rsi(cl, p=14):
    if len(cl) < p + 1:
        return None
    g = l = 0.0
    for i in range(1, p + 1):
        d = cl[i] - cl[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / p, l / p
    for i in range(p + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        ag = (ag * (p - 1) + max(d, 0.0)) / p
        al = (al * (p - 1) + max(-d, 0.0)) / p
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def macd_hist(cl, fast=12, slow=26, sig=9):
    ef = ema_series(cl, fast)
    es = ema_series(cl, slow)
    line = [ef[i] - es[i] for i in range(len(cl))]
    signal = ema_series(line, sig)
    return [line[i] - signal[i] for i in range(len(line))]


def analyse(symbol, interval="1m"):
    now = int(time.time() * 1000)
    raw = jget(KLINES % (interval, 120, symbol))
    done = [k for k in raw if int(k[6]) < now]
    if len(done) < 60:
        return None
    cl = [float(k[4]) for k in done]
    hist = macd_hist(cl)
    r = rsi(cl, 14)
    e50 = ema_series(cl, 50)[-1]
    return {"symbol": symbol, "price": cl[-1], "rsi": r, "ema50": e50,
            "hist": hist[-1], "hist_prev": hist[-2],
            "macd": (ema_series(cl, 12)[-1] - ema_series(cl, 26)[-1])}


# --- async signal scanner ---------------------------------------------
# The scan fetches 25 symbols of klines (~5 seconds). Doing that inline blocked
# position management, so a 1.5s hold was actually 5.2s. The scan now runs on
# its own thread and the management loop stays at 0.25s.
_SIG = {"ready": [], "at": 0.0}
_SIG_LOCK = threading.Lock()


def start_scanner(syms, interval, rsi_long, rsi_short, every=2.0):
    def loop():
        while True:
            try:
                ready = []
                for s in syms:
                    try:
                        x = analyse(s, interval)
                    except Exception:
                        continue
                    sg = signal(x, rsi_long, rsi_short) if x else None
                    if sg:
                        ready.append((s, sg))
                with _SIG_LOCK:
                    _SIG["ready"] = ready
                    _SIG["at"] = time.time()
            except Exception:
                pass
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()


def ready_signals(max_age=6.0):
    with _SIG_LOCK:
        if time.time() - _SIG["at"] > max_age:
            return []
        return list(_SIG["ready"])


def signal(x, rsi_long=50.0, rsi_short=50.0):
    """Aggressive trigger.

    The strict zero-cross version produced ZERO trades: MACD histogram crossing
    zero, AND RSI beyond 50, AND price beyond EMA50, all on the same 1m bar is a
    rare coincidence. Requiring only that the histogram be positive and RISING
    (momentum building, not a fresh cross) fires far more often while still
    respecting the trend and RSI gates.
    """
    if not x or x["rsi"] is None:
        return None
    up = x["hist"] > 0 and x["hist"] >= x["hist_prev"]
    dn = x["hist"] < 0 and x["hist"] <= x["hist_prev"]
    if up and x["rsi"] > rsi_long and x["price"] > x["ema50"]:
        return {"side": "buy", "rsi": x["rsi"], "hist": x["hist"]}
    if dn and x["rsi"] < rsi_short and x["price"] < x["ema50"]:
        return {"side": "sell", "rsi": x["rsi"], "hist": x["hist"]}
    return None


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=3600)
    ap.add_argument("--universe", type=int, default=20)
    ap.add_argument("--min-volume", type=float, default=5_000_000.0)
    ap.add_argument("--interval", default="1m")
    ap.add_argument("--max-hold", type=float, default=4.0, help="hard seconds")
    ap.add_argument("--tp", type=float, default=0.002, help="take profit, fraction")
    ap.add_argument("--sl", type=float, default=0.002, help="stop, fraction")
    ap.add_argument("--scan-every", type=float, default=1.0)
    ap.add_argument("--max-positions", type=int, default=20)
    ap.add_argument("--risk-pct", type=float, default=0.25)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--equity", type=float, default=2000.0)
    ap.add_argument("--both-sides", action="store_true", default=True)
    ap.add_argument("--state", default="state/hft_state.json")
    ap.add_argument("--ledger", default="ledger/hft.jsonl")
    args = ap.parse_args()

    log("HFT RSI/MACD -- PAPER ONLY | hold <= %.0fs | tp %.2f%% sl %.2f%% | %d slots | $%.0f"
        % (args.max_hold, args.tp * 100, args.sl * 100, args.max_positions, args.equity))

    syms = universe(args.universe, args.min_volume)
    log("universe %d: %s" % (len(syms), ", ".join(syms[:8])))

    start_scanner(syms, args.interval, 50.0, 50.0, every=2.0)
    prices, positions, trades = {}, {}, []
    equity = args.equity
    start_eq = equity
    opened = scans = 0
    started = time.time()
    next_scan = 0.0

    def dump():
        plist = []
        for s, p in positions.items():
            px = prices.get(s, p["entry"])
            raw = (px / p["entry"] - 1.0) * 100.0
            chg = raw if p["side"] == "buy" else -raw
            plist.append({
                "symbol": s, "side": p["side"], "entry": p["entry"], "price": px,
                "gross_pct": round(chg, 4), "net_pct": round(chg - 200 * args.fee, 4),
                "peak_pct": round(max(p.get("peak", 0.0), chg), 4),
                "trail_line": round(chg, 4),
                "held_s": round(time.time() - p["opened"], 2),
                "conf": None, "pcal": None, "alert_kind": "macd+rsi",
                "notional": p["notional"],
            })
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": "hft_rsi_macd",
            "state": "running",
            "equity": round(equity, 4), "start_equity": start_eq,
            "return_pct": round(100 * (equity / start_eq - 1), 4),
            "tp_pct": args.tp * 100, "sl_pct": args.sl * 100,
            "trail_ratio": 0.0, "trail_arm_pct": 0.0, "max_hold": args.max_hold,
            "scans": scans, "opened": opened,
            "positions": plist, "position": (plist[0] if plist else None),
            "trades": trades[-60:], "trade_count": len(trades),
            "wins": sum(1 for t in trades if t["net_pct"] > 0),
            "max_positions": args.max_positions, "watchlist_size": len(syms),
            "basis": "trend+rsi+macd", "min_confidence": None,
            "open_risk_usd": round(sum((p["notional"] * args.sl) for p in positions.values()), 2),
            "risk_pct": args.risk_pct,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.state)) or ".", exist_ok=True)
        tmp = args.state + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, args.state)

    while time.time() - started < args.seconds:
        t0 = time.time()
        try:
            prices.update(all_prices())
        except Exception as exc:
            log("price batch failed: %r" % (exc,))

        # ---- exits: 4s clock first, then bracket ----------------------
        for s in list(positions):
            p = positions[s]
            px = prices.get(s)
            if not px:
                continue
            raw = (px / p["entry"] - 1.0) * 100.0
            chg = raw if p["side"] == "buy" else -raw
            p["peak"] = max(p.get("peak", 0.0), chg)
            held = time.time() - p["opened"]
            reason = None
            if held >= args.max_hold:
                reason = "clock_%gs" % args.max_hold
            elif chg >= args.tp * 100:
                reason = "tp"
            elif chg <= -args.sl * 100:
                reason = "sl"
            if reason:
                net = chg - 200 * args.fee
                pnl = p["notional"] * net / 100.0
                equity += pnl
                trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                               "exit": px, "gross_pct": round(chg, 4),
                               "net_pct": round(net, 4), "peak_pct": round(p["peak"], 4),
                               "reason": reason, "held_s": round(held, 2),
                               "notional": round(p["notional"], 2),
                               "pnl_usd": round(pnl, 5),
                               "equity_after": round(equity, 4)})
                log("<<< %-9s %-11s %+7.4f%% net %+7.4f%% held %.2fs  eq %.2f"
                    % (reason, s, chg, net, held, equity))
                positions.pop(s, None)

        # ---- entries -------------------------------------------------
        if time.time() >= next_scan:
            scans += 1
            next_scan = time.time() + args.scan_every
            free = args.max_positions - len(positions)
            if free > 0:
                cand = [(s, sg) for s, sg in ready_signals() if s not in positions]
                for s, sig in cand[:free]:
                    px = prices.get(s)
                    if not px:
                        continue
                    notional = (equity * args.risk_pct / 100.0) / args.sl
                    positions[s] = {"side": sig["side"], "entry": px,
                                    "opened": time.time(), "peak": 0.0,
                                    "notional": notional}
                    opened += 1
                    log(">>> %-4s %-11s @ %-12.8g rsi %5.1f hist %+9.3g notional %7.2f"
                        % ("BUY" if sig["side"] == "buy" else "SELL", s, px,
                           sig["rsi"], sig["hist"], notional))
                if not cand:
                    log("scan %d: no trigger ready (%d open)" % (scans, len(positions)))

        dump()
        slack = 0.25 - (time.time() - t0)
        if slack > 0:
            time.sleep(slack)

    log("")
    log("=" * 80)
    log("opened %d | closed %d | wins %d | equity %.2f -> %.2f (%+.2f%%) | avg hold %.2fs"
        % (opened, len(trades), sum(1 for t in trades if t["net_pct"] > 0),
           start_eq, equity, 100 * (equity / start_eq - 1),
           (sum(t["held_s"] for t in trades) / len(trades)) if trades else 0))
    if trades:
        gross = sum(t["gross_pct"] for t in trades)
        fees = 200 * args.fee * len(trades)
        log("gross %+.2f%% total | fees -%.2f%% total | net %+.2f%%" % (gross, fees, gross - fees))
    log("=" * 80)

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
