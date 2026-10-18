#!/usr/bin/env python3
"""crypto_pack - five crypto-focused models sharing one playbook.

Every model in this file confirms with technicals, sizes by conviction and locks
profit fast. They differ only in what makes them want to trade:

  ta_momentum    EMA9/21 cross with VWAP agreement and volume
  vwap_scalp     price stretched from VWAP, fade back to it
  breakout_vol   20-bar range break on expanding volume
  oi_squeeze     open-interest rise with flat price (positioning piling in)
  funding_ta     funding extreme plus technical agreement

    python3 crypto_pack.py --risk 2.5
"""
import argparse, json, os, time
import tactics as T

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"

OPPOSITE = {}       # symbol -> side currently flagged against our position

SKIM = 0.30          # above the 0.2% round trip, else skimming is a loss
SKIM_AFTER = 2.5
HARD_STOP = 0.90     # losers get room up to here
CLOCK = 600          # then measured out

CFG = {
    "ta_momentum":  {"lock": 0.30, "trail_arm": 0.50, "stop": 0.40, "clock": 180},
    "vwap_scalp":   {"lock": 0.25, "trail_arm": 0.40, "stop": 0.35, "clock": 120},
    "breakout_vol": {"lock": 0.35, "trail_arm": 0.60, "stop": 0.45, "clock": 240},
    "oi_squeeze":   {"lock": 0.40, "trail_arm": 0.65, "stop": 0.50, "clock": 300},
    "funding_ta":   {"lock": 0.35, "trail_arm": 0.55, "stop": 0.45, "clock": 300},
}


class Book:
    def __init__(self, name, start):
        self.name, self.cfg = name, CFG[name]
        self.equity = self.start = start
        self.pos, self.trades, self.opened = {}, [], 0

    def manage(self, px):
        for s in list(self.pos):
            p = self.pos[s]; m = px.get(s)
            if not m: continue
            chg = (m / p["entry"] - 1) * 100
            if p["side"] == "sell": chg = -chg
            p["peak"] = max(p["peak"], chg)
            held = time.time() - p["t"]
            r = T.exit_reason(chg, p["peak"], self.cfg["lock"], self.cfg["trail_arm"],
                              0.15, self.cfg["stop"], held, CLOCK,
                              skim=SKIM, skim_after=SKIM_AFTER, hard_stop=HARD_STOP,
                              opposing=(OPPOSITE.get(s) == p["side"]))
            if r:
                net = chg - 200 * T.FEE
                self.equity += p["notional"] * net / 100
                self.trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                                    "gross_pct": round(chg, 4), "net_pct": round(net, 4),
                                    "peak_pct": round(p["peak"], 4), "reason": r,
                                    "held_s": round(time.time() - p["t"], 1),
                                    "notional": round(p["notional"], 2),
                                    "entry_ms": int(p["t"] * 1000),
                                    "exit_ms": int(time.time() * 1000), "why": p["why"]})
                self.pos.pop(s, None)

    def open(self, s, side, px, why, risk):
        notional = min(self.equity * (risk / 100) / (self.cfg["stop"] / 100), self.equity * 3)
        self.pos[s] = {"side": side, "entry": px, "t": time.time(), "notional": notional,
                       "peak": 0.0, "why": why}
        self.opened += 1

    def dump(self):
        pl = [{"symbol": s, "side": p["side"], "entry": p["entry"], "price": p["entry"],
               "gross_pct": round(p["peak"], 3), "net_pct": round(p["peak"] - 0.2, 3),
               "peak_pct": round(p["peak"], 3), "held_s": round(time.time() - p["t"], 1),
               "notional": round(p["notional"], 2),
               "risk_usd": round(p["notional"] * self.cfg["stop"] / 100, 2),
               "why": p["why"]} for s, p in self.pos.items()]
        d = {"ts": int(time.time() * 1000), "mode": "paper", "engine": self.name,
             "state": "running", "equity": round(self.equity, 4), "start_equity": self.start,
             "return_pct": round(100 * (self.equity / self.start - 1), 4),
             "realised": round(self.equity - self.start, 4),
             "unrealised": round(sum(p["notional"] * p["peak"] / 100 for p in self.pos.values()), 4),
             "tp_pct": self.cfg["lock"], "sl_pct": self.cfg["stop"],
             "max_hold": self.cfg["clock"], "trail_ratio": 0.15,
             "trail_arm_pct": self.cfg["trail_arm"], "positions": pl, "position": None,
             "trades": self.trades[-60:], "trade_count": len(self.trades),
             "wins": sum(1 for t in self.trades if t["net_pct"] > 0), "opened": self.opened,
             "max_positions": 12, "basis": "crypto pack · TA + lock",
             "open_risk_usd": round(sum(p["notional"] * self.cfg["stop"] / 100 for p in self.pos.values()), 2),
             "settings": {"risk_pct": 0, "lock_pct": self.cfg["lock"],
                          "stop_pct": self.cfg["stop"], "clock_s": self.cfg["clock"]}}
        os.makedirs(STATE, exist_ok=True)
        p = os.path.join(STATE, "pack_%s.json" % self.name)
        with open(p + ".t", "w") as fh: json.dump(d, fh)
        os.replace(p + ".t", p)


ALERT_MIN = 75.0


def alert_map():
    """symbol -> (side, score) for high-confidence pump/dump alerts."""
    try:
        d = T.jget(MASTER + "/api/alerts?limit=120", 6)
    except Exception:
        return {}
    out = {}
    for a in d.get("alerts") or []:
        if a.get("kind") not in ("PUMP", "DUMP"):
            continue
        if (a.get("score") or 0) < ALERT_MIN:
            continue
        out[a["symbol"]] = ("buy" if a["kind"] == "PUMP" else "sell", a.get("score") or 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=2.5)
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-liq", type=float, default=50e6)
    ap.add_argument("--loop", type=float, default=2.0)
    a = ap.parse_args()
    print("CRYPTO PACK — %d models, risk base %.1f%%, lock-fast" % (len(CFG), a.risk), flush=True)
    books = {k: Book(k, a.start) for k in CFG}
    feats, feats_ts = {}, 0.0
    while True:
        now = time.time()
        try:
            tick = T.jget(MASTER + "/api/universe800", 10).get("tickers") or []
        except Exception:
            tick = []
        tick = [t for t in tick if str(t.get("s", "")).endswith("USDT")
                and (t.get("v") or 0) >= a.min_liq]
        tick.sort(key=lambda t: -(t.get("v") or 0)); tick = tick[:a.top]
        px = {t["s"]: t["price"] for t in tick if t.get("price")}
        if now - feats_ts > 20:
            try: feats = json.load(open(os.path.join(STATE, "features.json"))).get("features") or {}
            except Exception: feats = {}
            feats_ts = now
        try: funds = {r["symbol"]: float(r["lastFundingRate"])
                      for r in T.jget("https://fapi.binance.com/fapi/v1/premiumIndex", 8)}
        except Exception: funds = {}

        OPPOSITE.clear()
        for b in books.values():
            for sym2, p2 in b.pos.items():
                pass
        for b in books.values():
            b.manage(px)

        amap = alert_map()
        for t in tick:
            sym, chg = t["s"], (t.get("c") or 0)
            if sym not in px: continue
            if sym not in amap:
                continue          # only trade high-confidence pump/dump names
            alert_side, alert_score = amap[sym]
            try: bs = T.bars(sym, "1m")
            except Exception: continue
            if len(bs) < 30: continue
            cl = [x["c"] for x in bs]; e9, e21 = T.ema(cl[-30:], 9), T.ema(cl[-30:], 21)
            vwap = sum(x["c"] * x["v"] for x in bs[-30:]) / (sum(x["v"] for x in bs[-30:]) or 1)
            hi = max(x["h"] for x in bs[-21:-1]); lo = min(x["l"] for x in bs[-21:-1])
            volmed = sorted(x["v"] for x in bs[-21:-1])[10] or 1e-9
            volx = bs[-1]["v"] / volmed
            dev = (px[sym] - vwap) / (T.atr(bs) or 1e-9)
            f = feats.get(sym) or {}

            ideas = {
                "ta_momentum": ("buy" if e9 > e21 and px[sym] > vwap else
                                "sell" if e9 < e21 and px[sym] < vwap else None),
                "vwap_scalp": ("sell" if dev > 1.8 else "buy" if dev < -1.8 else None),
                "breakout_vol": ("buy" if px[sym] > hi and volx > 1.3 else
                                 "sell" if px[sym] < lo and volx > 1.3 else None),
                "oi_squeeze": ("buy" if (f.get("oi_chg_pct") or 0) > 0.4 and abs(chg) < 1.0 else
                               "sell" if (f.get("oi_chg_pct") or 0) < -0.4 and abs(chg) < 1.0 else None),
                "funding_ta": ("sell" if (funds.get(sym) or 0) > 0.0008 else
                               "buy" if (funds.get(sym) or 0) < -0.0008 else None),
            }
            for _n, _s in ideas.items():
                if _s:
                    OPPOSITE.setdefault(sym, _s)
            for name, side in ideas.items():
                if not side or side != alert_side:
                    continue
                bk = books[name]
                if sym in bk.pos or len(bk.pos) >= 12: continue
                conf, why = T.ta_confirm(sym, side)
                if conf < 2: continue
                risk = T.risk_for(a.risk, conf)
                bk.open(sym, side, px[sym], "TA%d[%s] %s" % (conf, why, name), risk)
                print("%s  %-12s %-4s %-10s TA%d risk %.1f%% %s" %
                      (time.strftime("%H:%M:%S"), name, side, sym, conf, risk, why), flush=True)

        for b in books.values(): b.dump()
        time.sleep(max(1.0, a.loop))


if __name__ == "__main__":
    main()
