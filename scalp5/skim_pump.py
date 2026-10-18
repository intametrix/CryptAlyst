#!/usr/bin/env python3
"""skim_pump - skim pump/dump flow the second it appears.

Pipeline: Go detector -> signal_bus :8793 -> this skimmer -> maker fill.

Rules: enter on a fresh PUMP/DUMP alert, hold a few seconds, take a small profit,
cut a small loss, and never hold past the clock. High turnover, maker fees only.

    python3 skim_pump.py --hold 6 --tp 0.12 --sl 0.35
"""
import argparse, json, os, time
import maker, risk, tactics as T

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
BUS = "http://127.0.0.1:8793"
MD = "http://127.0.0.1:8796"
FEE = 0.04


def jget(u, t=3.0):
    with __import__("urllib.request", fromlist=["request"]).urlopen(
            __import__("urllib.request", fromlist=["request"]).Request(u, headers={"User-Agent": "skim/1"}),
            timeout=t) as r:
        return json.loads(r.read().decode())


class Book:
    def __init__(self, start=5000.0):
        self.start = start; self.equity = start; self.pos = {}; self.trades = []

    def snap(self):
        return {"engine": "skim_pump", "mode": "paper-maker", "state": "running",
                "equity": round(self.equity, 2), "start_equity": self.start,
                "return_pct": round(100 * (self.equity / self.start - 1), 4),
                "realised": round(self.equity - self.start, 2), "unrealised": 0.0,
                "trade_count": len(self.trades),
                "positions": [{"symbol": s, "side": p["side"], "entry": p["entry"],
                               "notional": p["notional"], "entry_ms": int(p["t"] * 1000),
                               "held_s": time.time() - p["t"], "why": p["why"]}
                              for s, p in self.pos.items()],
                "trades": self.trades[-300:]}


def write(bk):
    p = os.path.join(STATE, "pack_skim_pump.json")
    with open(p + ".t", "w") as fh:
        json.dump(bk.snap(), fh)
    os.replace(p + ".t", p)


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--hold", type=float, default=6.0)
    ap.add_argument("--tp", type=float, default=0.12)
    ap.add_argument("--sl", type=float, default=0.35)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--max-open", type=int, default=12)
    ap.add_argument("--min-score", type=float, default=60.0)
    a = ap.parse_args()
    bk = Book(a.start)
    log("SKIM PUMP — hold %.0fs tp %.2f%% sl %.2f%% min score %.0f" % (
        a.hold, a.tp, a.sl, a.min_score))
    cur = {"v": 0}
    seen = set()
    while True:
        # ---- exits first, on streamed prices
        prices = {}
        try:
            prices = jget(MD + "/streamprices").get("p") or {}
        except Exception:
            pass
        for sym in list(bk.pos):
            p = bk.pos[sym]
            px = prices.get(sym)
            if not px:
                continue
            chg = (px / p["entry"] - 1) * 100 if p["side"] == "buy" else (1 - px / p["entry"]) * 100
            held = time.time() - p["t"]
            reason = ("tp" if chg >= a.tp else "sl" if chg <= -a.sl else
                      "clock" if held >= a.hold else None)
            if reason:
                net = chg - FEE
                bk.equity += p["notional"] * net / 100
                bk.trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                                  "exit": px, "gross_pct": chg, "net_pct": net,
                                  "peak_pct": max(0.0, chg), "reason": reason, "held_s": held,
                                  "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                                  "notional": p["notional"], "pnl_usd": p["notional"] * net / 100,
                                  "why": p["why"]})
                log("CLOSE %-10s %-4s %+6.3f%% net %+6.3f%% %s %.1fs" % (
                    sym, p["side"], chg, net, reason, held))
                bk.pos.pop(sym)
        write(bk)

        # ---- new alerts
        try:
            d = jget(BUS + "/latest?since=%d" % cur["v"], 2)
            cur["v"] = d.get("cursor") or cur["v"]
            fresh = d.get("signals") or []
        except Exception:
            fresh = []
        for sig in fresh:
            sym = sig.get("symbol"); kind = sig.get("kind"); score = sig.get("score") or 0
            if not sym or kind not in ("PUMP", "DUMP") or score < a.min_score:
                continue
            eid = sig.get("event_id") or (sym, sig.get("ts_ms"))
            if eid in seen:
                continue
            seen.add(eid)
            if len(bk.pos) >= a.max_open or sym in bk.pos:
                continue
            side = "buy" if kind == "PUMP" else "sell"
            notional = min(bk.equity * a.risk / 100 * 40, bk.equity * 0.08)
            ok, why = risk.allow(sym, side, notional, bk.equity, confidence=0.0)
            risk.record(sym, side, ok, why)
            if not ok:
                continue
            q_px, q_prob, _ = maker.quote(sym, side, notional)
            if q_px is None or q_prob < 0.20:
                continue
            bk.pos[sym] = {"side": side, "entry": q_px, "t": time.time(),
                           "notional": notional,
                           "why": "SKIM %s score %.0f %s" % (kind, score, sig.get("detector") or "")}
            log("OPEN  %-10s %-4s %.6g  score %.0f" % (sym, side, q_px, score))
            write(bk)
        if len(seen) > 4000:
            seen.clear()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
