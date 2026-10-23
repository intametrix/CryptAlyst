#!/usr/bin/env python3
"""curve_rider - stream the order book and ride the strongest signal (never poll).

BPF-inspired: the depth imbalance arrives over WebSocket (real-time), the rest is
the 24h direction from the universe. Enter the strongest imbalance and ride it
with a trailing stop until it reverses.

    python3 curve_rider.py --trail 2.0 --stop 5.0
"""
import argparse, json, os, time
import ratelimit
import streamfeed

try:
    import tactics as _tactics
    _tactics.require_enabled('curverider', 'curve_rider.py')
except SystemExit:
    raise
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
FEE = 0.2


def jget(u, t=8.0):
    return ratelimit.jget(u, t, {"User-Agent": "rider/1"})


class Book:
    def __init__(self, start=5000.0):
        self.start = start; self.equity = start
        self.pos = None; self.trades = []

    def snap(self):
        p = self.pos
        return {"engine": "curve_rider", "mode": "paper", "state": "running",
                "equity": round(self.equity, 2), "start_equity": self.start,
                "realised": round(self.equity - self.start, 2), "unrealised": 0.0,
                "trade_count": len(self.trades),
                "positions": ([{"symbol": p["symbol"], "side": p["side"], "entry": p["entry"],
                                "notional": p["notional"], "entry_ms": int(p["t"] * 1000),
                                "held_s": time.time() - p["t"], "why": p["why"]}] if p else []),
                "trades": self.trades[-200:]}


def write(bk):
    p = os.path.join(STATE, "pack_curve_rider.json")
    with open(p + ".t", "w") as fh:
        json.dump(bk.snap(), fh)
    os.replace(p + ".t", p)


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trail", type=float, default=2.0)
    ap.add_argument("--stop", type=float, default=5.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()
    bk = Book(a.start)
    log("CURVE RIDER — streamed book imbalance, trail %.1f%%, stop %.1f%%" % (a.trail, a.stop))

    # one universe fetch for the symbol list + 24h direction (not per-trade polling)
    uni = jget(MASTER + "/api/universe800", 12).get("tickers") or []
    syms = [t["s"] for t in uni if str(t.get("s", "")).endswith("USDT")
            and t.get("price")][:20]
    meta = {t["s"]: (t.get("c") or 0.0) for t in uni if t.get("s") in syms}
    feed = streamfeed.Feed(syms)          # real-time depth imbalance
    log("streaming %d symbols" % len(syms))

    while True:
        try:
            if bk.pos is None:
                sym, snap = feed.best()
                if not sym:
                    time.sleep(1); continue
                side = "buy" if snap["imb"] >= 0 else "sell"
                notional = min(bk.equity * a.risk / 100 * 20, bk.equity * 0.25)
                bk.pos = {"symbol": sym, "side": side, "entry": snap["mid"], "t": time.time(),
                          "notional": notional, "peak": 0.0,
                          "why": "imb %+.2f 24h %+.1f%%" % (snap["imb"], meta.get(sym, 0))}
                log("OPEN  %-10s %-4s imb %+.2f  24h %+.1f%%" %
                    (sym, side, snap["imb"], meta.get(sym, 0)))
                write(bk)
                continue
            p = bk.pos
            snap = feed.snapshot(p["symbol"])
            if not snap:
                time.sleep(a.poll); continue
            mv = (snap["mid"] / p["entry"] - 1) * 100 if p["side"] == "buy" else (1 - snap["mid"] / p["entry"]) * 100
            p["peak"] = max(p["peak"], mv)
            net = mv - FEE
            if mv <= p["peak"] - a.trail or mv <= -a.stop:
                bk.equity += p["notional"] * net / 100
                bk.trades.append({"symbol": p["symbol"], "side": p["side"], "entry": p["entry"],
                                  "exit": snap["mid"], "gross_pct": mv, "net_pct": net,
                                  "peak_pct": p["peak"], "reason": "trail" if mv > -a.stop else "stop",
                                  "held_s": time.time() - p["t"],
                                  "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                                  "notional": p["notional"], "pnl_usd": p["notional"] * net / 100,
                                  "why": p["why"]})
                log("CLOSE %-10s %-4s %+6.2f%% net %+6.2f%% (peak %+6.2f%%)" %
                    (p["symbol"], p["side"], mv, net, p["peak"]))
                bk.pos = None
                write(bk)
            time.sleep(a.poll)
        except Exception as exc:
            log("error %r" % (exc,))
            time.sleep(2)


if __name__ == "__main__":
    main()
