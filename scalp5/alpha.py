#!/usr/bin/env python3
"""alpha - three fee-viable crypto models, each with its own book.

  xsec_momentum    cross-sectional: long the strongest N, short the weakest N
                   by 24h return, rebalance every 15m, hold hours. Market-neutral
                   by construction, so BTC beta cancels and only relative alpha
                   remains.
  cascade_rebound  1m drop of 3 sigma on 5x median volume = forced selling.
                   Buy the snap-back, exit fast. A liquidation proxy without
                   needing the liquidation feed.
  funding_carry    perpetual funding extreme while spot is flat = leveraged
                   crowd paying to hold a thesis nobody else has. Collect the
                   reversion, hold 8h.

All targets are >= 0.5%, so the 0.2% round trip is a third of the edge or less.

    python3 alpha.py --seconds 86400
"""
import argparse, json, os, statistics, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
BACKEND = "http://127.0.0.1:8787"; SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"; FEE = 0.001

CFG = {
    "xsec_momentum":  {"tp": 2.0, "sl": 1.2, "hold": 14400, "risk": 0.5},
    "cascade_rebound": {"tp": 0.8, "sl": 0.6, "hold": 2700,  "risk": 0.5},
    "funding_carry":  {"tp": 1.2, "sl": 1.0, "hold": 28800, "risk": 0.5},
}

def log(m): print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def jget(u, t=8.0, timeout=None):
    if timeout: t = timeout
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha/1"}), timeout=t) as r:
        return json.loads(r.read().decode())

class Book:
    def __init__(self, name):
        self.name, self.cfg = name, CFG[name]
        self.equity = 2000.0; self.pos = {}; self.trades = []; self.opened = 0

    def mark(self, sym, px):
        p = self.pos.get(sym)
        if not p: return None
        ch = (px / p["entry"] - 1) * 100
        return ch if p["side"] == "buy" else -ch

    def manage(self, px):
        for s in list(self.pos):
            p = self.pos[s]; m = px.get(s)
            if not m: continue
            ch = self.mark(s, m); p["mark"] = ch; p["peak"] = max(p.get("peak", 0), ch)
            r = None
            if ch >= self.cfg["tp"]: r = "target"
            elif ch <= -self.cfg["sl"]: r = "stop"
            elif time.time() - p["t"] >= self.cfg["hold"]: r = "clock"
            if r:
                net = ch - 200 * FEE
                self.equity += p["notional"] * net / 100
                self.trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                    "gross_pct": round(ch, 3), "net_pct": round(net, 3),
                    "peak_pct": round(p["peak"], 3), "reason": r,
                    "held_s": round(time.time() - p["t"], 1), "notional": round(p["notional"], 2),
                    "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                    "why": p["why"]})
                self.pos.pop(s, None)

    def open(self, s, side, px, why):
        n = min(self.equity * (self.cfg["risk"] / 100) / (self.cfg["sl"] / 100), self.equity * 3)
        self.pos[s] = {"side": side, "entry": px, "t": time.time(), "notional": n,
                       "peak": 0.0, "mark": 0.0, "why": why}
        self.opened += 1

    def dump(self):
        pl = [{"symbol": s, "side": p["side"], "entry": p["entry"], "price": p["entry"],
               "gross_pct": round(p.get("mark", 0), 3), "net_pct": round(p.get("mark", 0) - 0.2, 3),
               "peak_pct": round(p.get("peak", 0), 3), "held_s": round(time.time() - p["t"], 1),
               "notional": round(p["notional"], 2),
               "risk_usd": round(p["notional"] * self.cfg["sl"] / 100, 2), "why": p["why"]}
              for s, p in self.pos.items()]
        d = {"ts": int(time.time() * 1000), "mode": "paper", "engine": self.name,
             "state": "running", "equity": round(self.equity, 4), "start_equity": 2000.0,
             "return_pct": round(100 * (self.equity / 2000 - 1), 4),
             "realised": round(self.equity - 2000, 4),
             "unrealised": round(sum(p["notional"] * p.get("mark", 0) / 100 for p in self.pos.values()), 4),
             "tp_pct": self.cfg["tp"], "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"],
             "trail_ratio": 0.0, "trail_arm_pct": 0.0, "positions": pl,
             "position": (pl[0] if pl else None), "trades": self.trades[-60:],
             "trade_count": len(self.trades),
             "wins": sum(1 for t in self.trades if t["net_pct"] > 0), "opened": self.opened,
             "max_positions": 10, "basis": "alpha: fee-viable crypto model",
             "open_risk_usd": round(sum(p["notional"] * self.cfg["sl"] / 100 for p in self.pos.values()), 2),
             "settings": {"risk_pct": self.cfg["risk"], "tp_pct": self.cfg["tp"],
                          "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"]}}
        os.makedirs(STATE, exist_ok=True)
        p = os.path.join(STATE, "alpha_%s.json" % self.name)
        with open(p + ".t", "w") as fh: json.dump(d, fh)
        os.replace(p + ".t", p)

def klines(sym, iv, n):
    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (sym, iv, n))
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5])} for r in d]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seconds", type=int, default=86400)
    ap.add_argument("--loop", type=float, default=5.0); ap.add_argument("--universe", type=int, default=60)
    a = ap.parse_args()
    log("ALPHA — xsec_momentum, cascade_rebound, funding_carry")
    books = {k: Book(k) for k in CFG}
    t0 = time.time(); last = 0.0

    while time.time() - t0 < a.seconds:
        if time.time() - last < a.loop:
            for b in books.values(): b.dump()
            time.sleep(2); continue
        last = time.time()

        try:
            uni = jget(MASTER + "/api/universe800", timeout=12).get("tickers") or []
        except Exception:
            uni = []
        uni = [u for u in uni if str(u.get("s", "")).endswith("USDT")][:a.universe]
        px = {u["s"]: u["price"] for u in uni if u.get("price")}

        # ---- 1. cross-sectional momentum
        b = books["xsec_momentum"]; b.manage(px)
        ranked = sorted([u for u in uni if u.get("c") is not None], key=lambda u: -(u["c"] or 0))
        if len(ranked) >= 20:
            longs = ranked[:3]; shorts = ranked[-3:]
            for u in longs:
                if u["s"] not in b.pos and len(b.pos) < 10:
                    b.open(u["s"], "buy", u["price"], "top-3 24h: %+.1f%%" % u["c"])
                    log("%-16s BUY  %-11s %+.1f%% (strongest)" % ("xsec_momentum", u["s"], u["c"]))
            for u in shorts:
                if u["s"] not in b.pos and len(b.pos) < 10 and (u["c"] or 0) < 0:
                    b.open(u["s"], "sell", u["price"], "bottom-3 24h: %+.1f%%" % u["c"])
                    log("%-16s SELL %-11s %+.1f%% (weakest)" % ("xsec_momentum", u["s"], u["c"]))

        # ---- 2. cascade rebound: 1m 3-sigma drop on 5x volume
        cb = books["cascade_rebound"]; cb.manage(px)
        for u in uni[:24]:
            s = u["s"]
            if s in cb.pos or len(cb.pos) >= 6: continue
            try: b1 = klines(s, "1m", 40)
            except Exception: continue
            if len(b1) < 30: continue
            rets = [(b1[i]["c"] / b1[i-1]["c"] - 1) * 100 for i in range(1, len(b1))]
            sd = statistics.pstdev(rets[:-1]) or 1e-9
            last_ret = rets[-1]
            volmed = statistics.median([x["v"] for x in b1[-21:-1]]) or 1e-9
            if last_ret < -3 * sd and b1[-1]["v"] > 5 * volmed:
                cb.open(s, "buy", b1[-1]["c"], "1m %.2f%% (%.1f sigma) on %.1fx volume" %
                        (last_ret, last_ret / sd, b1[-1]["v"] / volmed))
                log("%-16s BUY  %-11s cascade %.1f sigma %.1fx vol" %
                    ("cascade_rebound", s, last_ret / sd, b1[-1]["v"] / volmed))

        # ---- 3. funding carry
        fc = books["funding_carry"]; fc.manage(px)
        try:
            fr = {r["symbol"]: float(r["lastFundingRate"]) for r in jget(FAPI + "/fapi/v1/premiumIndex")}
        except Exception:
            fr = {}
        ext = sorted(fr.items(), key=lambda kv: -abs(kv[1]))[:6]
        for s, rate in ext:
            if s not in px or s in fc.pos or len(fc.pos) >= 6: continue
            if abs(rate) < 0.0005: continue
            side = "sell" if rate > 0 else "buy"
            fc.open(s, side, px[s], "funding %+.4f%%/8h" % (100 * rate))
            log("%-16s %-4s %-11s funding %+.4f%%" % ("funding_carry", side.upper(), s, 100 * rate))

        for b in books.values(): b.dump()
        log("cycle: " + ", ".join("%s %d open" % (k, len(v.pos)) for k, v in books.items()))
        time.sleep(2)

if __name__ == "__main__": main()
