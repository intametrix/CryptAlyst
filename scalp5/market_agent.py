#!/usr/bin/env python3
"""market_agent - watches the liquid universe and tells the models what matters.

It does not trade. It scores every liquid symbol on the information we actually
have and publishes a ranked opportunity board that the models can read.

Score components (each normalised, weighted, then summed):
  alert      newest pump/dump alert score for the symbol        weight 0.30
  funding    absolute funding rate on the perp                  weight 0.20
  oi         open-interest change (5m)                          weight 0.15
  imbal      order-book bid/ask imbalance                       weight 0.15
  move       24h move vs the cross-section (dispersion)         weight 0.10
  depth      liquidity bucket (thin books score lower)          weight 0.10

    python3 market_agent.py --every 5
"""
import argparse, json, os, statistics, time, urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('marketagent', 'market_agent.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"; SITE = "http://127.0.0.1:8787"
FAPI = "https://fapi.binance.com"
OUT = os.path.join(STATE, "opportunities.json")

def jget(u, t=8.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "market-agent/1"}), timeout=t) as r:
        return json.loads(r.read().decode())

def safe(fn, d=None):
    try: return fn()
    except Exception: return d

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--top", type=int, default=40); ap.add_argument("--min-liq", type=float, default=50e6)
    a = ap.parse_args()
    print("MARKET AGENT — watching %d liquid symbols every %.0fs" % (a.top, a.every), flush=True)
    while True:
        tick = safe(lambda: jget(MASTER + "/api/universe800", 12).get("tickers") or [], [])
        tick = [t for t in tick if str(t.get("s", "")).endswith("USDT") and (t.get("v") or 0) >= a.min_liq]
        tick.sort(key=lambda t: -(t.get("v") or 0)); tick = tick[:a.top]

        alerts = safe(lambda: jget(SITE + "/api/alerts?limit=200", 8).get("alerts") or [], [])
        amap = {}
        for x in alerts:
            s = x.get("symbol")
            if s and (s not in amap or (x.get("score") or 0) > (amap[s].get("score") or 0)):
                amap[s] = x
        feats = safe(lambda: json.load(open(os.path.join(STATE, "features.json"))).get("features") or {}, {})
        funds = safe(lambda: {r["symbol"]: float(r["lastFundingRate"])
                              for r in jget(FAPI + "/fapi/v1/premiumIndex", 10)}, {})

        moves = [t.get("c") or 0 for t in tick] or [0]
        sd = statistics.pstdev(moves) or 1.0
        rows = []
        for t in tick:
            sym = t["s"]; comp = {}
            al = amap.get(sym)
            comp["alert"] = (al.get("score") or 0) / 100.0 if al else 0.0
            fr = abs(funds.get(sym) or 0)
            comp["funding"] = min(1.0, fr / 0.0015)          # 0.15%/8h saturates
            f = feats.get(sym) or {}
            comp["oi"] = min(1.0, abs(f.get("oi_chg_pct") or 0) / 1.0)
            comp["imbal"] = min(1.0, abs(f.get("imbalance") or 0) / 0.25)
            comp["move"] = min(1.0, abs((t.get("c") or 0)) / (3 * sd))
            comp["depth"] = min(1.0, (t.get("v") or 0) / 500e6)
            score = (0.30 * comp["alert"] + 0.20 * comp["funding"] + 0.15 * comp["oi"]
                     + 0.15 * comp["imbal"] + 0.10 * comp["move"] + 0.10 * comp["depth"])
            bits = []
            if al: bits.append("%s %d" % (al.get("kind"), al.get("score") or 0))
            if fr > 0.0005: bits.append("funding %+.3f%%" % (100 * (funds.get(sym) or 0)))
            if abs(f.get("oi_chg_pct") or 0) > 0.3: bits.append("oi %+.2f%%" % f["oi_chg_pct"])
            if abs(f.get("imbalance") or 0) > 0.15: bits.append("book %+.2f" % f["imbalance"])
            rows.append({"symbol": sym, "score": round(score, 4),
                         "price": t.get("price"), "chg": t.get("c"),
                         "liq": t.get("v"), "why": " · ".join(bits) or "liquidity only",
                         "components": {k: round(v, 3) for k, v in comp.items()}})
        rows.sort(key=lambda r: -r["score"])

        # --- narrate what changed, so the advisor's reasoning is visible
        prev = {}
        try:
            with open(OUT) as fh:
                prev = {r["symbol"]: r["score"] for r in (json.load(fh).get("opportunities") or [])}
        except Exception:
            prev = {}
        thoughts = []
        now_syms = {r["symbol"]: r["score"] for r in rows[:15]}
        for sym, sc in now_syms.items():
            if sym not in prev:
                thoughts.append({"kind": "new", "sym": sym, "score": sc,
                                 "text": "%s entered the board at %.2f" % (sym, sc)})
            elif sc - prev[sym] >= 0.05:
                thoughts.append({"kind": "up", "sym": sym, "score": sc,
                                 "text": "%s strengthened %.2f -> %.2f" % (sym, prev[sym], sc)})
            elif prev[sym] - sc >= 0.05:
                thoughts.append({"kind": "down", "sym": sym, "score": sc,
                                 "text": "%s faded %.2f -> %.2f" % (sym, prev[sym], sc)})
        for sym in prev:
            if sym not in now_syms:
                thoughts.append({"kind": "out", "sym": sym, "score": 0.0,
                                 "text": "%s dropped off the board" % sym})
        flag = [r["symbol"] for r in rows[:10] if r["score"] >= 0.30]
        thoughts.insert(0, {"kind": "flag", "sym": None, "score": None,
                            "text": "flagging %d names to the models: %s" % (
                                len(flag), ", ".join(flag))})
        for t in thoughts:
            t["ts"] = int(time.time() * 1000)
        board = {"ts": int(time.time() * 1000), "universe": len(tick),
                 "opportunities": rows[:15], "thoughts": thoughts[:20],
                 "flagged": [r["symbol"] for r in rows[:10] if r["score"] >= 0.30]}
        os.makedirs(STATE, exist_ok=True)
        with open(OUT + ".t", "w") as fh: json.dump(board, fh)
        os.replace(OUT + ".t", OUT)
        top = rows[:3]
        print("%s  %d scored | top: %s" % (time.strftime("%H:%M:%S"), len(rows),
              ", ".join("%s %.2f" % (r["symbol"], r["score"]) for r in top)), flush=True)
        time.sleep(max(2.0, a.every))

if __name__ == "__main__": main()
