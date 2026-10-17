#!/usr/bin/env python3
"""crypto_edges - five edges that only exist in crypto.

  1. funding_fade     perpetual funding extremes = crowded positioning -> fade it
  2. session_breakout range break only when the market is actually awake
                      (London/NY hours, never weekends)
  3. vwap_reversion   >2 ATR away from session VWAP -> fade back to it
  4. btc_regime       alts follow BTC: long only while BTC is above its 1h EMA50
  5. flow_divergence  price up but taker flow selling = absorption -> short

Data: Binance spot klines + indicators from the Go backend, futures
premiumIndex for funding, and the platform's own alert stream.

Each edge has its own $2,000 book and writes state/edge_<name>.json.

    python3 crypto_edges.py --seconds 86400
"""

import argparse
import json
import math
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
BACKEND = "http://127.0.0.1:8787"
FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
FEE = 0.001

EDGES = {
    "funding_fade":    {"tp": 1.0, "sl": 1.2, "hold": 1800, "risk": 0.8},
    "session_breakout": {"tp": 1.2, "sl": 0.9, "hold": 1200, "risk": 1.0},
    "vwap_reversion":  {"tp": 0.7, "sl": 1.0, "hold": 900, "risk": 0.8},
    "btc_regime":      {"tp": 1.5, "sl": 1.0, "hold": 2400, "risk": 1.0},
    "flow_divergence": {"tp": 0.9, "sl": 1.1, "hold": 900, "risk": 0.8},
}


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=10.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "crypto-edges/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


class Book:
    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.equity = 2000.0
        self.positions, self.trades = {}, []
        self.opened = 0

    def mark(self, sym, px):
        p = self.positions.get(sym)
        if not p:
            return None
        raw = (px / p["entry"] - 1.0) * 100.0
        return raw if p["side"] == "buy" else -raw

    def manage(self, px):
        for sym in list(self.positions):
            p = self.positions[sym]
            mark = px.get(sym)
            if not mark:
                continue
            chg = self.mark(sym, mark)
            p["peak"] = max(p.get("peak", 0.0), chg)
            p["mark"] = chg
            reason = None
            if chg >= self.cfg["tp"]:
                reason = "target"
            elif chg <= -self.cfg["sl"]:
                reason = "stop"
            elif (time.time() - p["opened"]) >= self.cfg["hold"]:
                reason = "clock"
            if reason:
                net = chg - 200 * FEE
                self.equity += p["notional"] * net / 100.0
                self.trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                                    "exit": mark, "gross_pct": round(chg, 3),
                                    "net_pct": round(net, 3), "peak_pct": round(p["peak"], 3),
                                    "reason": reason, "held_s": round(time.time() - p["opened"], 1),
                                    "notional": round(p["notional"], 2),
                                    "entry_ms": int(p["opened"] * 1000),
                                    "exit_ms": int(time.time() * 1000), "why": p.get("why")})
                self.positions.pop(sym, None)

    def open(self, sym, side, px, why, stop_pct):
        notional = min(self.equity * (self.cfg["risk"] / 100.0) / (stop_pct / 100.0),
                       self.equity * 3)
        self.positions[sym] = {"side": side, "entry": px, "opened": time.time(),
                               "notional": notional, "peak": 0.0, "mark": 0.0, "why": why}
        self.opened += 1

    def dump(self):
        pos = [{"symbol": s, "side": p["side"], "entry": p["entry"], "price": p["entry"],
                "gross_pct": round(p.get("mark", 0), 3),
                "net_pct": round(p.get("mark", 0) - 0.2, 3),
                "peak_pct": round(p.get("peak", 0), 3),
                "held_s": round(time.time() - p["opened"], 1),
                "notional": round(p["notional"], 2),
                "risk_usd": round(p["notional"] * self.cfg["sl"] / 100, 2),
                "why": p.get("why")} for s, p in self.positions.items()]
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": self.name,
            "state": "running", "equity": round(self.equity, 4), "start_equity": 2000.0,
            "return_pct": round(100 * (self.equity / 2000.0 - 1), 4),
            "realised": round(self.equity - 2000.0, 4),
            "unrealised": round(sum(p["notional"] * p.get("mark", 0) / 100 for p in
                                    self.positions.values()), 4),
            "tp_pct": self.cfg["tp"], "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"],
            "trail_ratio": 0.0, "trail_arm_pct": 0.0, "positions": pos,
            "position": (pos[0] if pos else None), "trades": self.trades[-60:],
            "trade_count": len(self.trades),
            "wins": sum(1 for t in self.trades if t["net_pct"] > 0),
            "opened": self.opened, "max_positions": 6, "basis": "crypto edge",
            "open_risk_usd": round(sum(p["notional"] * self.cfg["sl"] / 100
                                       for p in self.positions.values()), 2),
            "settings": {"risk_pct": self.cfg["risk"], "tp_pct": self.cfg["tp"],
                         "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"]},
        }
        os.makedirs(STATE, exist_ok=True)
        path = os.path.join(STATE, "edge_%s.json" % self.name)
        with open(path + ".t", "w") as fh:
            json.dump(payload, fh)
        os.replace(path + ".t", path)


# ---------------------------------------------------------------- indicators
def klines(sym, interval="15m", limit=100):
    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (sym, interval, limit))
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5]),
             "tb": float(r[9]) if len(r) > 9 else 0.0} for r in d]


def ema(vals, n):
    k = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def atr(bars, n=20):
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / max(1, len(trs[-n:]))


def vwap(bars):
    pv = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in bars)
    vv = sum(b["v"] for b in bars)
    return pv / vv if vv else bars[-1]["c"]


def funding_rates():
    try:
        d = jget(FAPI + "/fapi/v1/premiumIndex")
        return {r["symbol"]: float(r["lastFundingRate"]) for r in d}
    except Exception:
        return {}


def is_active_session(now=None):
    t = time.gmtime(now or time.time())
    if t.tm_wday >= 5:                     # weekend: thin books, no breakout edge
        return False
    return 7 <= t.tm_hour < 17             # London + NY overlap


def btc_regime():
    """+1 above the 1h EMA50, -1 below."""
    bars = klines("BTCUSDT", "1h", 120)
    if len(bars) < 60:
        return 0
    e = ema([b["c"] for b in bars], 50)
    return 1 if bars[-1]["c"] > e else -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=86400)
    ap.add_argument("--tick", type=float, default=5.0)
    args = ap.parse_args()

    log("CRYPTO EDGES — paper only")
    for k, v in EDGES.items():
        log("  %-17s tp %.1f sl %.1f hold %ds risk %.1f%%" % (
            k, v["tp"], v["sl"], v["hold"], v["risk"]))

    books = {k: Book(k, v) for k, v in EDGES.items()}
    started = time.time()
    cache = {}

    while time.time() - started < args.seconds:
        try:
            raw = jget(BACKEND + "/api/universe").get("symbols") or []
        except Exception:
            raw = []
        # the universe is a list of objects, newest format; tolerate plain strings
        names = [x["symbol"] if isinstance(x, dict) else str(x) for x in raw]
        syms = [x for x in names if x.endswith("USDT")][:40]

        try:
            alerts = jget(BACKEND + "/api/alerts?limit=120").get("alerts") or []
        except Exception:
            alerts = []
        funds = funding_rates()
        extremes = sorted(funds.items(), key=lambda kv: kv[1])
        cheap = extremes[:4]          # most negative funding (crowded shorts)
        rich = extremes[-4:][::-1]    # most positive funding (crowded longs)
        regime = btc_regime()
        active = is_active_session()

        for sym in syms:
            try:
                bars = klines(sym, "15m", 96)
            except Exception:
                continue
            if len(bars) < 30:
                continue
            px = bars[-1]["c"]
            a = atr(bars)
            vw = vwap(bars)
            e20 = ema([b["c"] for b in bars], 20)
            hi = max(b["h"] for b in bars[-20:])
            lo = min(b["l"] for b in bars[-20:])
            fr = funds.get(sym)
            last = bars[-1]
            tbr = (last["tb"] / last["v"]) if last["v"] else None
            marks = {"px": px}

            for name, b in books.items():
                b.manage({sym: px})
                if len(b.positions) >= 6 or sym in b.positions:
                    continue
                side = why = None
                if name == "funding_fade":
                    continue    # handled from the futures extremes below
                elif name == "session_breakout" and active:
                    if px > hi * 0.9995 and px > e20:
                        side, why = "buy", "break of %.4f in London/NY" % hi
                    elif px < lo * 1.0005 and px < e20:
                        side, why = "sell", "break below %.4f in London/NY" % lo
                elif name == "vwap_reversion" and a > 0:
                    dev = (px - vw) / a
                    if dev > 2.0:
                        side, why = "sell", "%.1f ATR above VWAP %.4f" % (dev, vw)
                    elif dev < -2.0:
                        side, why = "buy", "%.1f ATR below VWAP %.4f" % (dev, vw)
                elif name == "btc_regime" and regime:
                    if regime > 0 and px > e20 and px > vw:
                        side, why = "buy", "BTC regime up, %s above EMA20+VWAP" % sym
                    elif regime < 0 and px < e20 and px < vw:
                        side, why = "sell", "BTC regime down, %s below EMA20+VWAP" % sym
                elif name == "flow_divergence" and tbr is not None:
                    up = px > bars[-2]["c"]
                    if up and tbr < 0.45:
                        side, why = "sell", "price up, taker buy %.0f%% — absorption" % (100 * tbr)
                    elif (not up) and tbr > 0.55:
                        side, why = "buy", "price down, taker buy %.0f%% — absorption" % (100 * tbr)
                if side:
                    b.open(sym, side, px, why, b.cfg["sl"])
                    log("%-17s %-4s %-11s %s" % (name, side.upper(), sym, why))

        # funding_fade works the extremes of the whole futures board
        fb = books["funding_fade"]
        for side, group in (("sell", rich), ("buy", cheap)):
            for sym, fr in group:
                if len(fb.positions) >= 6 or sym in fb.positions:
                    continue
                if side == "sell" and fr <= 0.0002:
                    continue
                if side == "buy" and fr >= -0.0001:
                    continue
                try:
                    bars = klines(sym, "15m", 40)
                except Exception:
                    continue
                if len(bars) < 20:
                    continue
                px = bars[-1]["c"]
                why = "funding %+.4f%%/8h — %s crowded" % (
                    100 * fr, "longs" if side == "sell" else "shorts")
                fb.open(sym, side, px, why, fb.cfg["sl"])
                log("%-17s %-4s %-11s %s" % ("funding_fade", side.upper(), sym, why))

        for b in books.values():
            b.dump()
        time.sleep(max(1.0, args.tick))

    for name, b in books.items():
        log("%-17s equity %.2f (%+.2f%%) opened %d closed %d" % (
            name, b.equity, 100 * (b.equity / 2000 - 1), b.opened, len(b.trades)))


if __name__ == "__main__":
    main()
