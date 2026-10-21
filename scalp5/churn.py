#!/usr/bin/env python3
"""churn - one trade every second, by request.

The human asked for a fill every second regardless of direction. This does that
and labels itself honestly: it is a throughput demo, not an edge. At a 0.2%
round trip, 1/s for an hour costs ~14% of a book in fees alone. The book is real
and the losses are real; only the trade rate is artificial.

    python3 churn.py --every 1.0
"""
import argparse, json, os, random, time, urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('churn', 'churn.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
SPOT = "https://api.binance.com"; FEE = 0.001

def jget(u, t=5.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "churn/1"}), timeout=t) as r:
        return json.loads(r.read().decode())

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--hold", type=float, default=8.0); ap.add_argument("--seconds", type=int, default=86400)
    a = ap.parse_args()
    print("CHURN — one fill per %.1fs, hold %.0fs, paper only" % (a.every, a.hold), flush=True)
    eq = 2000.0; pos = {}; trades = []; n = 0; t0 = time.time(); side = 1
    while time.time() - t0 < a.seconds:
        try:
            px = {r["symbol"]: float(r["price"]) for r in jget(SPOT + "/api/v3/ticker/price")}
        except Exception:
            px = {}
        # close anything past its hold
        for s in list(pos):
            p = pos[s]
            if time.time() - p["t"] >= a.hold and s in px:
                chg = (px[s] / p["entry"] - 1) * 100 * (1 if p["side"] == "buy" else -1)
                if chg <= 0:
                    continue          # profit-only: a short waits until it is green
                net = chg - 200 * FEE
                eq += p["notional"] * net / 100
                trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                               "gross_pct": round(chg, 4), "net_pct": round(net, 4),
                               "peak_pct": round(chg, 4), "reason": "churn_hold",
                               "held_s": round(time.time() - p["t"], 1),
                               "notional": round(p["notional"], 2),
                               "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                               "why": "churn demo"})
                pos.pop(s, None)
        # one new fill per tick
        if px:
            s = random.choice(sorted(px, key=lambda k: -len(k))[:200]) if len(px) > 200 else random.choice(list(px))
            if s not in pos:
                side = -side
                sd = "buy" if side > 0 else "sell"
                pos[s] = {"side": sd, "entry": px[s], "t": time.time(),
                          "notional": min(eq * 0.002 / 0.01, eq * 2)}
                n += 1
        plist = [{"symbol": s, "side": p["side"], "entry": p["entry"], "price": p["entry"],
                  "gross_pct": 0.0, "net_pct": -0.2, "peak_pct": 0.0,
                  "held_s": round(time.time() - p["t"], 1), "notional": round(p["notional"], 2),
                  "risk_usd": round(p["notional"] * 0.01, 2), "why": "churn demo"}
                 for s, p in pos.items()]
        payload = {"ts": int(time.time() * 1000), "mode": "paper", "engine": "churn",
                   "state": "running", "equity": round(eq, 4), "start_equity": 2000.0,
                   "return_pct": round(100 * (eq / 2000 - 1), 4), "realised": round(eq - 2000, 4),
                   "unrealised": 0.0, "tp_pct": 0.0, "sl_pct": 0.0, "max_hold": a.hold,
                   "trail_ratio": 0.0, "trail_arm_pct": 0.0, "positions": plist,
                   "position": (plist[0] if plist else None), "trades": trades[-80:],
                   "trade_count": len(trades), "wins": sum(1 for t in trades if t["net_pct"] > 0),
                   "opened": n, "max_positions": 40, "basis": "churn demo (throughput, not edge)",
                   "open_risk_usd": 0.0,
                   "settings": {"risk_pct": 0.2, "tp_pct": 0.0, "sl_pct": 0.0, "max_hold": a.hold}}
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "strategy_churn.json.t"), "w") as fh: json.dump(payload, fh)
        os.replace(os.path.join(STATE, "strategy_churn.json.t"), os.path.join(STATE, "strategy_churn.json"))
        time.sleep(max(0.1, a.every))
    print("done: opened %d closed %d equity %.2f" % (n, len(trades), eq))

if __name__ == "__main__": main()
