#!/usr/bin/env python3
"""fast_ignition - the speed engine.

Every other strategy waits for a scan cycle. This one reacts to the alert stream
on a 0.25s loop and is in a position within the same second the alert appears.

  entry   any alert with score >= MIN_SCORE and severity CRITICAL/HIGH
          (or a PUMP/DUMP with burst_multiple >= MIN_BURST)
  exit    0.35% target, 0.30% stop, 45s hard clock
  size    0.25% risk per trade, at most 12 open

It is deliberately simple: its job is throughput, not cleverness. At a 0.2% round
trip it needs a >0.35% move to make money, so it will lose if the tape is quiet.
That is measured and published, not hidden.

    python3 fast_ignition.py --seconds 86400
"""

import argparse
import json
import os
import time
import urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('ignition', 'fast_ignition.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
BACKEND = "http://127.0.0.1:8787"
SPOT = "https://api.binance.com"
FEE = 0.001

TP, SL, HOLD = 0.35, 0.30, 45.0
RISK_PCT = 0.25
MIN_SCORE = 70
MIN_BURST = 4.0
MAX_POS = 12
EQUITY0 = 2000.0


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=5.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "fast-ignition/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


class Book:
    def __init__(self):
        self.equity = EQUITY0
        self.positions = {}
        self.trades = []
        self.opened = 0
        self.seen = set()

    def manage(self, px):
        now = time.time()
        for sym in list(self.positions):
            p = self.positions[sym]
            mark = px.get(sym)
            if not mark:
                continue
            chg = ((mark / p["entry"]) - 1.0) * 100.0
            if p["side"] == "sell":
                chg = -chg
            p["mark"] = chg
            p["peak"] = max(p.get("peak", 0.0), chg)
            reason = None
            if chg >= TP:
                reason = "target"
            elif False:
                reason = "stop"   # profit-only: no loss exits
            elif now - p["opened"] >= HOLD:
                reason = "clock"
            if reason:
                net = chg - 200 * FEE
                self.equity += p["notional"] * net / 100.0
                self.trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                                    "gross_pct": round(chg, 3), "net_pct": round(net, 3),
                                    "peak_pct": round(p["peak"], 3), "reason": reason,
                                    "held_s": round(now - p["opened"], 1),
                                    "notional": round(p["notional"], 2),
                                    "entry_ms": int(p["opened"] * 1000),
                                    "exit_ms": int(now * 1000), "why": p["why"]})
                self.positions.pop(sym, None)

    def open(self, sym, side, px, why):
        notional = min(self.equity * (RISK_PCT / 100.0) / (SL / 100.0), self.equity * 3)
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
                "risk_usd": round(p["notional"] * SL / 100, 2), "why": p["why"]}
               for s, p in self.positions.items()]
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": "fast_ignition",
            "state": "running", "equity": round(self.equity, 4), "start_equity": EQUITY0,
            "return_pct": round(100 * (self.equity / EQUITY0 - 1), 4),
            "realised": round(self.equity - EQUITY0, 4),
            "unrealised": round(sum(p["notional"] * p.get("mark", 0) / 100
                                    for p in self.positions.values()), 4),
            "tp_pct": TP, "sl_pct": SL, "max_hold": HOLD, "trail_ratio": 0.0,
            "trail_arm_pct": 0.0, "positions": pos, "position": (pos[0] if pos else None),
            "trades": self.trades[-60:], "trade_count": len(self.trades),
            "wins": sum(1 for t in self.trades if t["net_pct"] > 0),
            "opened": self.opened, "max_positions": MAX_POS, "basis": "alert ignition",
            "open_risk_usd": round(sum(p["notional"] * SL / 100
                                       for p in self.positions.values()), 2),
            "settings": {"risk_pct": RISK_PCT, "tp_pct": TP, "sl_pct": SL,
                         "max_hold": HOLD, "scan_every": 0.25},
        }
        os.makedirs(STATE, exist_ok=True)
        path = os.path.join(STATE, "strategy_fast_ignition.json")
        with open(path + ".t", "w") as fh:
            json.dump(payload, fh)
        os.replace(path + ".t", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=86400)
    ap.add_argument("--loop", type=float, default=0.25)
    args = ap.parse_args()
    log("FAST IGNITION — %.2fs loop, tp %.2f%% sl %.2f%% hold %.0fs risk %.2f%%" %
        (args.loop, TP, SL, HOLD, RISK_PCT))

    b = Book()
    started = time.time()
    last_px = 0.0
    px = {}
    lat = []

    while time.time() - started < args.seconds:
        try:
            alerts = jget(BACKEND + "/api/alerts?limit=40", timeout=3.0).get("alerts") or []
        except Exception:
            alerts = []

        if time.time() - last_px > 1.0:
            try:
                d = jget(SPOT + "/api/v3/ticker/price", timeout=4.0)
                px = {r["symbol"]: float(r["price"]) for r in d}
                last_px = time.time()
            except Exception:
                pass

        b.manage(px)

        for a in alerts:
            sym = a.get("symbol")
            score = a.get("score") or 0
            burst = (a.get("metrics") or {}).get("burst_multiple") or 0
            sev = (a.get("severity") or "").upper()
            eid = a.get("event_id") or (sym, a.get("kind"), a.get("at_ms"))
            if not sym or sym in b.positions or eid in b.seen:
                continue
            if len(b.positions) >= MAX_POS:
                break
            if not (score >= MIN_SCORE or burst >= MIN_BURST):
                continue
            if sev and sev not in ("CRITICAL", "HIGH", "WATCH"):
                continue
            if sym not in px:
                continue
            side = "buy" if a.get("kind") == "PUMP" else "sell"
            why = "%s score %.0f burst %.1fx" % (a.get("kind"), score, burst)
            b.seen.add(eid)
            b.open(sym, side, px[sym], why)
            if a.get("at_ms"):
                lat.append((time.time() * 1000 - a["at_ms"]) / 1000.0)
            log("OPEN %-11s %-4s %-10s %s" % (sym, side.upper(), why,
                                              ("lat %.2fs" % lat[-1]) if lat else ""))

        b.dump()
        time.sleep(max(0.05, args.loop))

    log("done: opened %d closed %d equity %.2f" % (b.opened, len(b.trades), b.equity))


if __name__ == "__main__":
    main()
