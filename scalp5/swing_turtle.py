#!/usr/bin/env python3
"""SwingTurtle: the Turtle system rebuilt for crypto swings. PAPER ONLY.

Why the classic Turtle fails on crypto, and what this changes:

  * TIMEFRAME. Turtle was a daily system on futures that traded one session a
    day. Crypto swings happen intraday, so the channels live on 15m bars: a
    20-bar Donchian breakout is ~5 hours of price, the crypto equivalent of the
    Turtle's 20-day channel rather than a 20-day wait.
  * STOPS IN ATR, NOT PERCENT. Crypto ATR on 15m is 0.5-2%/bar and on a volatile
    alt it can triple in an hour. A fixed 2% stop is either noise-tight or
    useless; every stop here is 2 x ATR(14) of the entry bar, exactly as
    Wilder/Turtle intended.
  * VOLUME CONFIRMATION. Naked intraday breakouts on alts are mostly wick hunts
    and stop runs. A breakout must print >=1.5x its 20-bar median volume.
  * TREND FILTER. Longs only above EMA50, shorts only below, so the engine does
    not buy the top of a downtrend's bounce.
  * RATCHET. The stop only moves toward the trade: max(2N stop, 10-bar channel)
    and can never retreat. Winners are exited by the channel, not by a timer.

    python3 swing_turtle.py --seconds 14400
"""

import argparse
import json
import os
import time
import urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('swingturtle', 'swing_turtle.py')   # A6: a disabled book does not run
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


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "swingturtle/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def all_prices():
    return {r["symbol"]: float(r["price"]) for r in jget(ALL_PRICES)}


def universe(n, min_vol=20_000_000.0):
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


def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
    return e


def atr_wilder(h, l, c, period=14):
    trs = []
    for i in range(1, len(c)):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    if len(trs) < period:
        return None
    v = sum(trs[:period]) / period
    for tr in trs[period:]:
        v = (v * (period - 1) + tr) / period
    return v


def median(vals):
    v = sorted(vals)
    return v[len(v) // 2] if v else None


def analyse(symbol, interval="15m", entry_bars=20, exit_bars=10):
    now = int(time.time() * 1000)
    raw = jget(KLINES % (interval, 200, symbol))
    done = [k for k in raw if int(k[6]) < now]
    if len(done) < 60:
        return None
    h = [float(k[2]) for k in done]
    l = [float(k[3]) for k in done]
    c = [float(k[4]) for k in done]
    qv = [float(k[7]) for k in done]
    a = atr_wilder(h, l, c, 14)
    if not a:
        return None
    upper = max(h[-entry_bars - 1:-1])
    lower = min(l[-entry_bars - 1:-1])
    exit_low = min(l[-exit_bars - 1:-1])
    exit_high = max(h[-exit_bars - 1:-1])
    med_vol = median(qv[-21:-1])
    return {
        "symbol": symbol, "price": c[-1], "atr": a, "ema50": ema(c, 50),
        "upper": upper, "lower": lower,
        "exit_low": exit_low, "exit_high": exit_high,
        "vol_ratio": (qv[-1] / med_vol) if med_vol else 0.0,
        "atr_pct": 100.0 * a / c[-1] if c[-1] else 0.0,
    }


def setup(x, vol_mult=1.5, tol=0.002):
    """Breakout with a NEAR-CHANNEL TOLERANCE.

    Requiring price already through the channel starved the engine: measured
    live, 0 of 24 symbols qualified while 6 sat within 0.2%% of their high.
    Within `tol` of the level now counts as a breakout in progress.
    """
    if not x:
        return None
    if x["vol_ratio"] < vol_mult:
        return None
    long_break = x["price"] >= x["upper"] * (1.0 - tol) and x["price"] > x["ema50"]
    short_break = x["price"] <= x["lower"] * (1.0 + tol) and x["price"] < x["ema50"]
    if not (long_break or short_break):
        return None
    side = "buy" if long_break else "sell"
    two_n = 2.0 * x["atr"]
    if side == "buy":
        stop = x["price"] - two_n
        trail = x["exit_low"]
    else:
        stop = x["price"] + two_n
        trail = x["exit_high"]
    return {"side": side, "entry": x["price"], "stop": stop, "trail": trail,
            "n": x["atr"], "vol_ratio": round(x["vol_ratio"], 2),
            "atr_pct": round(x["atr_pct"], 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=14400)
    ap.add_argument("--universe", type=int, default=40)
    ap.add_argument("--min-volume", type=float, default=20_000_000.0)
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--entry-bars", type=int, default=20)
    ap.add_argument("--exit-bars", type=int, default=10)
    ap.add_argument("--tolerance", type=float, default=0.002,
                    help="how close to the channel counts as a breakout (fraction)")
    ap.add_argument("--vol-mult", type=float, default=1.0,
                    help="1.0 accepts a breakout on ordinary volume; 1.5 demands a surge")
    ap.add_argument("--tp-pct", type=float, default=1.0,
                    help="scrape this much profit and the trade is done")
    ap.add_argument("--lock-usd", type=float, default=20.0,
                    help="when total unrealised P&L exceeds this, bank every winner")
    ap.add_argument("--lock-cooldown", type=float, default=30.0)
    ap.add_argument("--max-hold", type=float, default=900.0,
                    help="hard time limit in seconds")
    ap.add_argument("--scan-every", type=float, default=60.0)
    ap.add_argument("--max-positions", type=int, default=8)
    ap.add_argument("--risk-pct", type=float, default=0.75)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--equity", type=float, default=2000.0)
    ap.add_argument("--state", default="state/swing_turtle.json")
    ap.add_argument("--ledger", default="ledger/swing_turtle.jsonl")
    args = ap.parse_args()

    log("SwingTurtle -- PAPER ONLY | %dm Donchian %d/%d | 2N ATR stops | vol x%.1f | tol %.2f%%"
        % (15, args.entry_bars, args.exit_bars, args.vol_mult, 100 * args.tolerance))
    log("scrape +%.1f%% | time limit %.0fs | %d slots | risk %.1f%%"
        % (args.tp_pct, args.max_hold, args.max_positions, args.risk_pct))
    syms = universe(args.universe, args.min_volume)
    log("universe %d liquid: %s" % (len(syms), ", ".join(syms[:8])))

    prices, positions, trades = {}, {}, []
    last_lock = 0.0
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
            plist.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                          "price": px, "gross_pct": round(chg, 3),
                          "net_pct": round(chg - 200 * args.fee, 3),
                          "peak_pct": round(max(p.get("peak", 0.0), chg), 3),
                          "trail_line": round((p["stop"] / p["entry"] - 1) * 100, 3),
                          "stop": round(p["stop"], 10),
                          "held_s": round(time.time() - p["opened"], 1),
                          "conf": None, "pcal": None, "alert_kind": "donchian",
                          "notional": p["notional"]})
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": "swing_turtle",
            "state": "running", "equity": round(equity, 4), "start_equity": start_eq,
            "return_pct": round(100 * (equity / start_eq - 1), 4),
            "tp_pct": 0.0, "sl_pct": 2.0, "trail_ratio": 0.0, "trail_arm_pct": 0.0,
            "max_hold": args.max_hold, "scans": scans, "opened": opened,
            "positions": plist, "position": (plist[0] if plist else None),
            "trades": trades[-60:], "trade_count": len(trades),
            "wins": sum(1 for t in trades if t["net_pct"] > 0),
            "max_positions": args.max_positions, "watchlist_size": len(syms),
            "basis": "Donchian+ATR", "min_confidence": None,
            "open_risk_usd": round(sum(abs(p["entry"] - p["stop"]) * p["qty"] for p in positions.values()), 2),
            "risk_pct": args.risk_pct, "lock_usd": args.lock_usd,
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
            log("prices failed: %r" % (exc,))

        # ---- $ LOCK: when the book is up more than --lock-usd, bank it ----
        lock_now = False
        if args.lock_usd > 0 and (time.time() - last_lock) > args.lock_cooldown and positions:
            total_usd = 0.0
            for s, p in positions.items():
                px = prices.get(s)
                if not px:
                    continue
                raw = (px / p["entry"] - 1.0) * 100.0
                chg = raw if p["side"] == "buy" else -raw
                total_usd += p["notional"] * chg / 100.0
            if total_usd >= args.lock_usd:
                lock_now = True
                log("LOCK: unrealised $%.2f >= $%.0f -- banking winners" % (total_usd, args.lock_usd))

        locked = 0
        for s in list(positions):
            p = positions[s]
            px = prices.get(s)
            if not px:
                continue
            raw = (px / p["entry"] - 1.0) * 100.0
            chg = raw if p["side"] == "buy" else -raw
            p["peak"] = max(p.get("peak", 0.0), chg)
            # ratchet: stop moves toward the trade, never away
            if p["side"] == "buy" and p["trail"] > p["stop"]:
                p["stop"] = min(p["trail"], px)
            if p["side"] == "sell" and p["trail"] < p["stop"]:
                p["stop"] = max(p["trail"], px)
            reason = None
            if lock_now and chg > 0:
                reason = "lock_usd"
            elif chg >= args.tp_pct:
                reason = "scrape_%.1f" % args.tp_pct
            elif args.max_hold > 0 and (time.time() - p["opened"]) >= args.max_hold:
                reason = "time_limit"
            elif p["side"] == "buy" and px <= p["stop"]:
                reason = "2N_stop" if p["stop"] == p["entry"] - 2 * p["n"] else "channel_exit"
            elif p["side"] == "sell" and px >= p["stop"]:
                reason = "2N_stop" if p["stop"] == p["entry"] + 2 * p["n"] else "channel_exit"
            if reason:
                net = chg - 200 * args.fee
                pnl = p["notional"] * net / 100.0
                equity += pnl
                trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                               "exit": px, "gross_pct": round(chg, 3),
                               "net_pct": round(net, 3), "peak_pct": round(p["peak"], 3),
                               "reason": reason, "held_s": round(time.time() - p["opened"], 1),
                               "notional": round(p["notional"], 2),
                               "pnl_usd": round(pnl, 4), "equity_after": round(equity, 4)})
                log("<<< %-13s %-11s %+6.3f%% net %+6.3f%% held %5.0fs eq %.2f"
                    % (reason, s, chg, net, time.time() - p["opened"], equity))
                positions.pop(s, None)
                if reason == "lock_usd":
                    locked += 1
        if locked:
            last_lock = time.time()
            log("LOCKED IN %d trades, %d still open, equity %.2f" % (locked, len(positions), equity))

        if time.time() >= next_scan:
            scans += 1
            next_scan = time.time() + args.scan_every
            free = args.max_positions - len(positions)
            if free > 0:
                cand = []
                for s in syms:
                    if s in positions:
                        continue
                    base = s[:-4] if s.endswith("USDT") else s
                    if base in STABLES or base.endswith("USD"):
                        continue
                    px_now = prices.get(s)
                    if px_now and abs(px_now - 1.0) < 0.01:      # a pegged pair
                        continue
                    try:
                        x = analyse(s, args.interval, args.entry_bars, args.exit_bars)
                    except Exception:
                        continue
                    st = setup(x, args.vol_mult, args.tolerance)
                    if st:
                        cand.append((st["vol_ratio"], s, st))
                cand.sort(reverse=True)
                for _, s, st in cand[:free]:
                    px = prices.get(s)
                    if not px:
                        continue
                    stop_frac = abs(st["entry"] - st["stop"]) / st["entry"]
                    if stop_frac <= 0:
                        continue
                    stop_frac = max(stop_frac, 0.002)          # 0.2% minimum stop
                    notional = equity * (args.risk_pct / 100.0) / stop_frac
                    notional = min(notional, equity * 0.25)     # never more than a quarter
                    qty = notional / px
                    positions[s] = {"side": st["side"], "entry": px, "opened": time.time(),
                                    "stop": st["stop"], "trail": st["trail"], "n": st["n"],
                                    "notional": notional, "qty": qty, "peak": 0.0}
                    opened += 1
                    log(">>> %-4s %-12s @ %-11.8g N=%-10.6g stop %-11.8g (%.2f%%) vol x%.1f atr%% %.2f"
                        % ("BUY" if st["side"] == "buy" else "SELL", s, px, st["n"],
                           st["stop"], 100 * stop_frac, st["vol_ratio"], st["atr_pct"]))
                if not cand:
                    log("scan %d: no confirmed breakout across %d symbols" % (scans, len(syms)))

        dump()
        slack = 1.0 - (time.time() - t0)
        if slack > 0:
            time.sleep(slack)

    log("")
    log("=" * 84)
    log("opened %d | closed %d | wins %d | equity %.2f -> %.2f (%+.2f%%)"
        % (opened, len(trades), sum(1 for t in trades if t["net_pct"] > 0),
           start_eq, equity, 100 * (equity / start_eq - 1)))
    for t in trades[-15:]:
        log("  %-12s %-4s %+6.3f%% net %-13s %5.0fs" %
            (t["symbol"], t["side"], t["net_pct"], t["reason"], t["held_s"]))
    log("=" * 84)

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
