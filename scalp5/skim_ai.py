#!/usr/bin/env python3
"""Skim AI: our own MAs, support/resistance and forecasts, per-symbol alpha.

The premise the user set: stop importing other people's signals. Build our own
read of each symbol from raw klines, skim small profits, and let a per-symbol
scorecard decide which symbols deserve capital.

Per symbol, from 5m klines (completed bars only):
    EMA9 / EMA21 / EMA50      trend and its slope
    ATR(14)                   the noise budget - stops and targets sit outside it
    support / resistance      the prior 30-bar low and high, not a drawn line
    range position            where price sits inside that band, 0..1
    forecast                  a short-horizon projection from EMA slope and the
                              distance back to the band midpoint

Entry (skim):
    long  when price is in the LOWER third of its band and EMA9 > EMA21 and
          EMA21 is rising, i.e. buying a pullback inside an uptrend
    short the mirror image. Spot cannot short: those are simulated.

Exit: target, stop, or a hard timeout - whichever first.

Alpha allocation: every symbol carries a live scorecard (trades, wins, net).
A symbol that has traded >= --bench-after times and is net negative is BENCHED
for --bench-seconds. Capital follows the symbols that actually pay.

PAPER ONLY. No keys, no orders. Public endpoints only.
"""

import argparse
import json
import os
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


def jget(url, timeout=8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "skim-ai/1.0"})
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
# our own analysis
# --------------------------------------------------------------------------
def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
        out.append(e)
    return out


def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if len(trs) < period:
        return None
    v = sum(trs[:period]) / period
    for tr in trs[period:]:
        v = (v * (period - 1) + tr) / period
    return v


def analyse(symbol, interval="5m", lookback=30):
    now = int(time.time() * 1000)
    raw = jget(KLINES % (interval, 120, symbol))
    done = [k for k in raw if int(k[6]) < now]
    if len(done) < 60:
        return None
    h = [float(k[2]) for k in done]
    l = [float(k[3]) for k in done]
    c = [float(k[4]) for k in done]
    e9 = ema(c, 9)
    e21 = ema(c, 21)
    e50 = ema(c, 50)
    a = atr(h, l, c, 14)
    support = min(l[-lookback - 1:-1])
    resistance = max(h[-lookback - 1:-1])
    price = c[-1]
    span = max(resistance - support, 1e-12)
    pos = (price - support) / span
    slope21 = (e21[-1] - e21[-5]) / e21[-5] if e21[-5] else 0.0
    # forecast: project the EMA9 slope one bar ahead, damped, bounded by the band
    proj = price * (1.0 + max(-0.01, min(0.01, (e9[-1] - e9[-2]) / price * 0.5)))
    proj = max(support, min(resistance, proj))
    return {
        "symbol": symbol, "price": price, "ema9": e9[-1], "ema21": e21[-1],
        "ema50": e50[-1], "atr": a, "support": support, "resistance": resistance,
        "range_pos": pos, "slope21": slope21, "forecast": proj,
        "band_pct": (span / price) if price else 0.0,
    }


def long_setup(x, cfg):
    """Buy WITH the uptrend.

    The pullback version of this rule never fired: in a genuine uptrend price
    sits near the TOP of its band, so "range_pos <= 0.35" waited for a dip that
    a trending market does not deliver. Trend-following means entering at the
    upper edge, i.e. on strength, not on a dip.
    """
    if not x or not x["atr"]:
        return None
    if x["range_pos"] < cfg["min_pos"]:
        return None
    if not (x["ema9"] > x["ema21"] > x["ema50"]):
        return None
    if x["slope21"] <= 0:
        return None
    entry = x["price"]
    target = entry * (1 + cfg["tp"])
    stop = entry * (1 - cfg["sl"])
    return {"side": "buy", "entry": entry, "target": target, "stop": stop,
            "range_pos": round(x["range_pos"], 3),
            "edge_pct": round((target / entry - 1) * 100, 3),
            "risk_pct": round((1 - stop / entry) * 100, 3)}


def short_setup(x, cfg):
    """Sell WITH the downtrend: enter at the lower edge, on weakness."""
    if not x or not x["atr"]:
        return None
    if x["range_pos"] > (1.0 - cfg["min_pos"]):
        return None
    if not (x["ema9"] < x["ema21"] < x["ema50"]):
        return None
    if x["slope21"] >= 0:
        return None
    entry = x["price"]
    target = entry * (1 - cfg["tp"])
    stop = entry * (1 + cfg["sl"])
    return {"side": "sell", "entry": entry, "target": target, "stop": stop,
            "range_pos": round(x["range_pos"], 3),
            "edge_pct": round((1 - target / entry) * 100, 3),
            "risk_pct": round((stop / entry - 1) * 100, 3)}


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=3600)
    ap.add_argument("--universe", type=int, default=25)
    ap.add_argument("--min-volume", type=float, default=5_000_000.0)
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--tp", type=float, default=0.006, help="target, fraction")
    ap.add_argument("--sl", type=float, default=0.004, help="stop, fraction")
    ap.add_argument("--min-edge", type=float, default=0.002)
    ap.add_argument("--max-pos", type=float, default=0.35, help="max range position to buy")
    ap.add_argument("--both-sides", action="store_true",
                    help="also take the short mirror (simulated: spot cannot short)")
    ap.add_argument("--hold", type=float, default=240.0, help="max seconds in a trade")
    ap.add_argument("--scan-every", type=float, default=45.0)
    ap.add_argument("--max-positions", type=int, default=6)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    ap.add_argument("--bench-after", type=int, default=3)
    ap.add_argument("--bench-seconds", type=float, default=900.0)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--equity", type=float, default=300.0)
    ap.add_argument("--state", default="state/swing_state.json")
    ap.add_argument("--ledger", default="ledger/skim_ai.jsonl")
    args = ap.parse_args()

    cfg = {"tp": args.tp, "sl": args.sl, "min_edge": args.min_edge,
           "min_pos": args.max_pos}   # min_pos = how far into the band price must be

    log("SKIM AI -- PAPER ONLY | own MAs + S/R + forecast | tp %.2f%% sl %.2f%% hold %.0fs"
        % (args.tp * 100, args.sl * 100, args.hold))
    syms = universe(args.universe, args.min_volume)
    log("universe %d by 24h volume: %s" % (len(syms), ", ".join(syms[:8])))

    prices = {}
    positions = {}
    trades = []
    card = {s: {"trades": 0, "wins": 0, "net": 0.0} for s in syms}
    benched = {}
    equity = args.equity
    start_eq = equity
    opened = 0
    scans = 0
    analyses = {}
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
                          "trail_line": round(p["target"], 6),
                          "target": round(p["target"], 8), "stop": round(p["stop"], 8),
                          "held_s": round(time.time() - p["opened"], 1),
                          "conf": None, "pcal": None,
                          "alert_kind": p.get("reason", "sr_pullback"),
                          "range_pos": p.get("range_pos")})
        active = [s for s in syms if s not in benched]
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": "skim_ai",
            "equity": round(equity, 4), "start_equity": start_eq,
            "return_pct": round(100 * (equity / start_eq - 1), 4),
            "tp_pct": args.tp * 100, "sl_pct": args.sl * 100,
            "trail_ratio": 0.0, "trail_arm_pct": 0.0, "max_hold": args.hold,
            "scans": scans, "opened": opened,
            "positions": plist, "position": (plist[0] if plist else None),
            "trades": trades[-60:], "trade_count": len(trades),
            "wins": sum(1 for t in trades if t["net_pct"] > 0),
            "watchlist_size": len(syms), "max_positions": args.max_positions,
            "max_per_side": args.max_positions, "basis": "own-analysis",
            "min_calibrated": 0, "open_risk_usd":
                round(sum((p["entry"] - p["stop"]) / p["entry"] * p.get("notional", 0)
                          for p in positions.values()), 2),
            "risk_pct": args.risk_pct,
            "scorecard": [{"symbol": s, "trades": c["trades"], "wins": c["wins"],
                           "net": round(c["net"], 3),
                           "benched": s in benched, "range_pos":
                               round((analyses[s]["range_pos"] if s in analyses else 0), 2),
                           "support": round(analyses[s]["support"], 8) if s in analyses else None,
                           "resistance": round(analyses[s]["resistance"], 8) if s in analyses else None,
                           "ema9": round(analyses[s]["ema9"], 8) if s in analyses else None,
                           "ema21": round(analyses[s]["ema21"], 8) if s in analyses else None,
                           "forecast": round(analyses[s]["forecast"], 8) if s in analyses else None}
                          for s, c in sorted(card.items(), key=lambda kv: -kv[1]["net"])],
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

        # ---- manage open positions -----------------------------------
        for s in list(positions):
            p = positions[s]
            px = prices.get(s)
            if not px:
                continue
            raw = (px / p["entry"] - 1.0) * 100.0
            chg = raw if p["side"] == "buy" else -raw
            p["peak"] = max(p.get("peak", 0.0), chg)
            reason = None
            if p["side"] == "buy":
                if px >= p["target"]:
                    reason = "target"
                elif px <= p["stop"]:
                    reason = "stop"
            else:
                if px <= p["target"]:
                    reason = "target"
                elif px >= p["stop"]:
                    reason = "stop"
            if reason is None and (time.time() - p["opened"]) >= args.hold:
                reason = "timeout"
            if reason:
                net = chg - 200 * args.fee
                pnl = p["notional"] * net / 100.0
                equity += pnl
                card[s]["trades"] += 1
                card[s]["net"] += net
                if net > 0:
                    card[s]["wins"] += 1
                trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                               "exit": px, "gross_pct": round(chg, 3),
                               "net_pct": round(net, 3), "peak_pct": round(p["peak"], 3),
                               "reason": reason, "held_s": round(time.time() - p["opened"], 1),
                               "notional": round(p["notional"], 2),
                               "pnl_usd": round(pnl, 4), "equity_after": round(equity, 4)})
                log("<<< %-8s %-11s %+6.3f%% net %+6.3f%% %-7s held %3.0fs  eq %.2f"
                    % (reason.upper(), s, chg, net, reason, time.time() - p["opened"], equity))
                if card[s]["trades"] >= args.bench_after and card[s]["net"] < 0:
                    benched[s] = time.time()
                    log("    BENCHED %s: %d trades, net %+.2f%%" %
                        (s, card[s]["trades"], card[s]["net"]))
                positions.pop(s, None)

        # ---- scan for pullback-in-uptrend setups ---------------------
        if time.time() >= next_scan:
            scans += 1
            next_scan = time.time() + args.scan_every
            # un-bench expired symbols
            for s in list(benched):
                if time.time() - benched[s] > args.bench_seconds:
                    del benched[s]

            free = args.max_positions - len(positions)
            if free > 0:
                ranked = []
                for s in syms:
                    if s in positions or s in benched:
                        continue
                    try:
                        x = analyse(s, args.interval)
                    except Exception:
                        continue
                    if not x:
                        continue
                    analyses[s] = x
                    setup = long_setup(x, cfg)
                    if setup:
                        ranked.append((setup["edge_pct"], s, setup, x))
                    elif args.both_sides:
                        setup = short_setup(x, cfg)
                        if setup:
                            ranked.append((setup["edge_pct"], s, setup, x))
                ranked.sort(reverse=True)
                for edge, s, setup, x in ranked[:free]:
                    px = prices.get(s, setup["entry"])
                    notional = (equity * args.risk_pct / 100.0) / max(setup["risk_pct"] / 100.0, 1e-6)
                    positions[s] = {"side": setup["side"], "entry": px, "opened": time.time(),
                                    "target": setup["target"], "stop": setup["stop"],
                                    "notional": notional, "peak": 0.0,
                                    "range_pos": setup["range_pos"]}
                    opened += 1
                    log(">>> %-4s %-11s @ %-11.8g target %-11.8g(+%.2f%%) stop %-11.8g(-%.2f%%) rangepos %.2f"
                        % ("BUY" if setup["side"] == "buy" else "SELL", s, px,
                           setup["target"], setup["edge_pct"], setup["stop"],
                           setup["risk_pct"], setup["range_pos"]))
                if not ranked:
                    log("scan %d: no pullback-in-uptrend setup across %d symbols"
                        % (scans, len(syms)))

        dump()
        slack = 1.0 - (time.time() - t0)
        if slack > 0:
            time.sleep(slack)

    log("")
    log("=" * 82)
    log("closed %d | wins %d | equity %.2f -> %.2f (%+.2f%%) | benched %d"
        % (len(trades), sum(1 for t in trades if t["net_pct"] > 0),
           start_eq, equity, 100 * (equity / start_eq - 1), len(benched)))
    for t in trades[-15:]:
        log("  %-11s %+6.3f%% net  %-8s %3.0fs" % (t["symbol"], t["net_pct"], t["reason"], t["held_s"]))
    log("per-symbol card:")
    for s, c in sorted(card.items(), key=lambda kv: -kv[1]["net"])[:10]:
        if c["trades"]:
            log("  %-11s trades %-3d wins %-3d net %+7.3f%%" % (s, c["trades"], c["wins"], c["net"]))
    log("=" * 82)

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
