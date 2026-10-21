#!/usr/bin/env python3
"""flash - take a PUMP/DUMP signal, hold 1-2 seconds, close.

Requested behaviour: enter on the alert, exit after 1-2s regardless of price.
It is a throughput machine, not an edge: a 0.2% round trip against a 1-2 second
move will bleed by construction. That is measured and published, not hidden.

    python3 flash.py --hold-min 1 --hold-max 2 --risk 2.5
"""
import argparse, json, os, random, time, urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('flash', 'flash.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"; SITE = "http://127.0.0.1:8787"
FEE = 0.001


def jget(u, t=4.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "flash/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold-min", type=float, default=1.0)
    ap.add_argument("--hold-max", type=float, default=2.0)
    ap.add_argument("--risk", type=float, default=2.5)
    ap.add_argument("--min-score", type=float, default=70)
    ap.add_argument("--min-liq", type=float, default=50e6)
    ap.add_argument("--seconds", type=int, default=86400)
    a = ap.parse_args()
    print("FLASH — PUMP/DUMP in, hold %.1f-%.1fs, out. risk %.1f%%" %
          (a.hold_min, a.hold_max, a.risk), flush=True)

    equity, start = 5000.0, 5000.0
    pos, trades, seen, opened = {}, [], set(), 0
    t0 = time.time()

    while time.time() - t0 < a.seconds:
        now = time.time()
        # ---- exits first
        if pos:
            try:
                tick = {r["s"]: r["price"] for r in jget(MASTER + "/api/tick?syms=" +
                        ",".join(pos.keys()), 3).get("p", {}).items()} if False else {}
                d = jget(MASTER + "/api/tick?syms=" + ",".join(pos.keys()), 3)
                tick = d.get("p") or {}
            except Exception:
                tick = {}
            for sym in list(pos):
                p = pos[sym]
                if now - p["t"] < p["hold"]:
                    continue
                px = tick.get(sym) or p["entry"]
                chg = (px / p["entry"] - 1) * 100
                if p["side"] == "sell":
                    chg = -chg
                net = chg - 200 * FEE
                equity += p["notional"] * net / 100
                trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                               "exit": px, "gross_pct": round(chg, 4),
                               "net_pct": round(net, 4), "peak_pct": round(chg, 4),
                               "reason": "flash_%ds" % round(p["hold"]),
                               "held_s": round(now - p["t"], 2),
                               "notional": round(p["notional"], 2),
                               "entry_ms": int(p["t"] * 1000),
                               "exit_ms": int(now * 1000), "why": p["why"]})
                pos.pop(sym, None)

        # ---- entries on new alerts
        try:
            alerts = jget(SITE + "/api/alerts?limit=30", 4).get("alerts") or []
        except Exception:
            alerts = []
        for x in alerts:
            sym, kind = x.get("symbol"), x.get("kind")
            if kind not in ("PUMP", "DUMP") or not sym or sym in pos:
                continue
            if (x.get("score") or 0) < a.min_score:
                continue
            eid = x.get("event_id") or (sym, x.get("ts_ms"))
            if eid in seen:
                continue
            seen.add(eid)
            if len(pos) >= 20:
                break
            try:
                d = jget(MASTER + "/api/tick?syms=" + sym, 3)
                px = (d.get("p") or {}).get(sym)
            except Exception:
                px = None
            if not px:
                continue
            side = "buy" if kind == "PUMP" else "sell"
            notional = min(equity * (a.risk / 100) / 0.005, equity * 3)
            pos[sym] = {"side": side, "entry": px, "t": now,
                        "hold": random.uniform(a.hold_min, a.hold_max),
                        "notional": notional,
                        "why": "%s score %d" % (kind, x.get("score") or 0)}
            opened += 1
            print("%s  OPEN %-11s %-4s %.6g" % (time.strftime("%H:%M:%S"), sym, side, px), flush=True)

        out = {"ts": int(time.time() * 1000), "mode": "paper", "engine": "flash",
               "state": "running", "equity": round(equity, 4), "start_equity": start,
               "return_pct": round(100 * (equity / start - 1), 4),
               "realised": round(equity - start, 4), "unrealised": 0.0,
               "tp_pct": 0.0, "sl_pct": 0.0, "max_hold": a.hold_max, "trail_ratio": 0.0,
               "trail_arm_pct": 0.0,
               "positions": [{"symbol": s, "side": p["side"], "entry": p["entry"],
                              "price": p["entry"], "gross_pct": 0.0, "net_pct": -0.2,
                              "peak_pct": 0.0, "held_s": round(time.time() - p["t"], 1),
                              "notional": round(p["notional"], 2),
                              "risk_usd": round(p["notional"] * 0.005, 2),
                              "why": p["why"]} for s, p in pos.items()],
               "position": None, "trades": trades[-80:], "trade_count": len(trades),
               "wins": sum(1 for t in trades if t["net_pct"] > 0), "opened": opened,
               "max_positions": 20, "basis": "flash scalp 1-2s",
               "open_risk_usd": 0.0,
               "settings": {"risk_pct": a.risk, "hold_s": [a.hold_min, a.hold_max]}}
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "slow_flash.json.t"), "w") as fh:
            json.dump(out, fh)
        os.replace(os.path.join(STATE, "slow_flash.json.t"), os.path.join(STATE, "slow_flash.json"))
        time.sleep(0.25)


if __name__ == "__main__":
    main()
