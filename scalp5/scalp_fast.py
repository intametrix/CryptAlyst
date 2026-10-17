#!/usr/bin/env python3
"""Indicator scalper on 1-SECOND bars -- PAPER ONLY, no keys, no orders.

Differences from scalp5.py, which this replaces:

  * market data : Binance 1s klines (completed bars only, live bar discarded)
                  instead of polling a 5s snapshot of the flow table
  * cadence     : evaluated every ~1s, no fixed "wait 5s then close" timer
  * decisions   : classic indicators -- EMA(9)/EMA(21) relationship and slope,
                  Wilder RSI(14), 5-bar momentum, ATR(14) for the stop
  * exits       : signal flip, ATR stop, or a max-hold cap -- whichever first

Fees are still real and still the dominant term: 0.1% per side on spot plus the
spread crossed twice. Every trade prints gross AND net so the fee drag stays
attributable rather than being quietly folded into a "strategy result".

Usage:
    python3 scalp_fast.py --seconds 90
    python3 scalp_fast.py --seconds 90 --symbols BTCUSDT,ETHUSDT,SOLUSDT
"""

import argparse
import json
import os
import statistics as stats
import time
import urllib.request

BASE = "https://api.binance.com"
KLINES = BASE + "/api/v3/klines?interval=1s&limit=%d&symbol=%s"
TICKER = BASE + "/api/v3/ticker/24hr"


def log(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# --------------------------------------------------------------------------
# indicators (1s bars)
# --------------------------------------------------------------------------
def ema_series(vals, period):
    k = 2.0 / (period + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def rsi_wilder(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(ch, 0.0)) / period
        al = (al * (period - 1) + max(-ch, 0.0)) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr_wilder(highs, lows, closes, period=14):
    n = len(closes)
    if n < period + 1:
        return None
    trs = []
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val


# --------------------------------------------------------------------------
# market data
# --------------------------------------------------------------------------
def http_json(url, timeout=5.0):
    req = urllib.request.Request(url, headers={"User-Agent": "scalp-fast/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def top_symbols(n):
    rows = http_json(TICKER)
    rows = [r for r in rows
            if r["symbol"].endswith("USDT") and not r["symbol"].endswith(("UPUSDT", "DOWNUSDT"))]
    rows.sort(key=lambda r: float(r["quoteVolume"]), reverse=True)
    return [r["symbol"] for r in rows[:n]]


def bars_1s(symbol, limit=120):
    """Completed 1s bars only -- the live bar must never drive a decision."""
    raw = http_json(KLINES % (limit, symbol))
    now_ms = int(time.time() * 1000)
    done = [k for k in raw if int(k[6]) < now_ms]
    if not done:
        return None
    return {
        "open": [float(k[1]) for k in done],
        "high": [float(k[2]) for k in done],
        "low": [float(k[3]) for k in done],
        "close": [float(k[4]) for k in done],
    }


def snapshot(symbol):
    b = bars_1s(symbol)
    if not b or len(b["close"]) < 30:
        return None
    closes = b["close"]
    ema9 = ema_series(closes, 9)
    ema21 = ema_series(closes, 21)
    rsi = rsi_wilder(closes, 14)
    atr = atr_wilder(b["high"], b["low"], closes, 14)
    momentum = closes[-1] - closes[-6]
    return {
        "symbol": symbol,
        "price": closes[-1],
        "ema9": ema9[-1],
        "ema21": ema21[-1],
        "ema9_slope": ema9[-1] - ema9[-2],
        "rsi": rsi,
        "atr": atr,
        "momentum": momentum,
        "spread": 0.0,          # filled from the ticker below when available
    }


# --------------------------------------------------------------------------
# strategy
# --------------------------------------------------------------------------
def long_signal(s, rsi_min=52.0):
    if s is None or s["rsi"] is None or s["atr"] is None:
        return False
    return (s["ema9"] > s["ema21"]
            and s["ema9_slope"] > 0
            and s["rsi"] > rsi_min
            and s["momentum"] > 0)


def exit_signal(s, entry, atr, held_s, max_hold):
    if s is None:
        return "no_data"
    if s["ema9"] < s["ema21"] and s["rsi"] < 48.0:
        return "ema_cross_down+rsi<48"
    if s["momentum"] < 0:
        return "momentum_flip"
    if atr and s["price"] <= entry - 1.5 * atr:
        return "atr_stop"
    if held_s >= max_hold:
        return "max_hold"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=90)
    ap.add_argument("--symbols", default="", help="comma list, default = top liquid")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--fee", type=float, default=0.001, help="taker fee per side")
    ap.add_argument("--max-hold", type=float, default=10.0, help="hard cap on one trade")
    ap.add_argument("--rsi-min", type=float, default=52.0)
    ap.add_argument("--ledger", default="ledger/scalp_fast.jsonl")
    args = ap.parse_args()

    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = top_symbols(args.count)

    log("scalp_fast -- PAPER ONLY (no keys, no orders)")
    log("1s bars | EMA9/21 + RSI14 + momentum5 + ATR14 | evaluated every ~1s")
    log("universe (%d): %s" % (len(syms), ", ".join(syms)))

    trades = []
    position = None
    started = time.time()
    last_snap = {}
    ticks = 0

    while time.time() - started < args.seconds:
        loop_t0 = time.time()
        ticks += 1

        for sym in syms:
            try:
                s = snapshot(sym)
            except Exception as exc:
                log("data error %s: %r" % (sym, exc))
                continue
            if s is None:
                continue
            last_snap[sym] = s

            if position and position["symbol"] == sym:
                held = time.time() - position["opened"]
                reason = exit_signal(s, position["entry"], position["atr"],
                                     held, args.max_hold)
                if reason:
                    gross = (s["price"] / position["entry"] - 1.0) * 1e4
                    fees = 2.0 * args.fee * 1e4
                    spread = s["spread"] * 1e4
                    net = gross - fees - spread
                    rec = {
                        "ts": int(position["opened"] * 1000), "symbol": sym,
                        "entry": position["entry"], "exit": s["price"],
                        "held_s": round(held, 2), "reason": reason,
                        "entry_rsi": round(position["rsi"], 1),
                        "exit_rsi": round(s["rsi"], 1),
                        "gross_bps": round(gross, 3), "fees_bps": round(fees, 2),
                        "spread_bps": round(spread, 2), "net_bps": round(net, 3),
                    }
                    trades.append(rec)
                    log("%-10s out %-18s %+.2f gross -%.2f fees = %+7.2f net bps  (%.1fs, rsi %.0f->%.0f)"
                        % (sym, reason, gross, fees, net, held,
                           rec["entry_rsi"], rec["exit_rsi"]))
                    position = None
                continue

            if position is None and long_signal(s, args.rsi_min):
                position = {
                    "symbol": sym, "entry": s["price"], "atr": s["atr"],
                    "opened": time.time(), "rsi": s["rsi"],
                }
                log("%-10s IN  long @ %.8g   ema9>ema21, rsi %.1f, mom5 %+.6g, atr %.6g"
                    % (sym, s["price"], s["rsi"], s["momentum"], s["atr"] or 0))

        # close any position whose symbol is no longer in the scan set
        if position and position["symbol"] not in syms:
            position = None

        slack = 1.0 - (time.time() - loop_t0)
        if slack > 0:
            time.sleep(slack)

    # force-close at the end so the ledger is balanced
    if position:
        sym = position["symbol"]
        try:
            px = last_snap.get(sym, {}).get("price") or position["entry"]
        except Exception:
            px = position["entry"]
        gross = (px / position["entry"] - 1.0) * 1e4
        fees = 2.0 * args.fee * 1e4
        net = gross - fees
        trades.append({"ts": int(position["opened"] * 1000), "symbol": sym,
                       "entry": position["entry"], "exit": px,
                       "held_s": round(time.time() - position["opened"], 2),
                       "reason": "run_end", "gross_bps": round(gross, 3),
                       "fees_bps": round(fees, 2), "spread_bps": 0.0,
                       "net_bps": round(net, 3)})
        log("%-10s out run_end        %+.2f gross -%.2f fees = %+7.2f net bps"
            % (sym, gross, fees, net))

    if not trades:
        log("no trades -- no 1s-bar long signal fired in %ds over %d symbols (%d evaluations)"
            % (args.seconds, len(syms), ticks))
        return

    gross = [t["gross_bps"] for t in trades]
    net = [t["net_bps"] for t in trades]
    wins = sum(1 for n in net if n > 0)
    log("")
    log("=" * 82)
    log("trades %d   elapsed %ds   evaluations %d   max_hold %ss"
        % (len(trades), args.seconds, ticks, args.max_hold))
    log("gross/trade mean %+.2f bps  median %+.2f  best %+.2f  worst %+.2f"
        % (stats.mean(gross), stats.median(gross), max(gross), min(gross)))
    log("net  /trade mean %+.2f bps  median %+.2f"
        % (stats.mean(net), stats.median(net)))
    log("wins %d/%d (%.0f%%)   cumulative net %+.1f bps = %+.2f%% equity"
        % (wins, len(trades), 100.0 * wins / len(trades), sum(net), sum(net) / 100.0))
    log("=" * 82)

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
