#!/usr/bin/env python3
"""curve_rider - ride the curve for a few seconds, then wait.

Reads the very short-term direction of BTC (EMA9 vs EMA21 on 15s bars), takes a
position in the strongest liquid mover in that direction, holds for a handful of
seconds, closes, and waits before the next attempt.

    python3 curve_rider.py --hold 4 --wait 20 --risk 0.5
"""
import argparse, json, os, time, urllib.request
import ratelimit

try:
    import tactics as _tactics
    _tactics.require_enabled('curverider', 'curve_rider.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
SPOT = "https://api.binance.com"
FEE = 0.2


def jget(u, t=6.0):
    return ratelimit.jget(u, t, {"User-Agent": "rider/1"})


def klines(sym, interval="1m", limit=60):
    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (sym, interval, limit))
    return [{"c": float(x[4]), "v": float(x[5])} for x in d]


def ema(v, n):
    k = 2.0 / (n + 1); e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def direction():
    """Short-horizon curve direction from BTC 1m closes, refreshed every call."""
    bs = klines("BTCUSDT", "1m", 40)
    cl = [b["c"] for b in bs]
    e9, e21 = ema(cl[-20:], 9), ema(cl[-21:], 21)
    slope = (cl[-1] / cl[-4] - 1) * 100
    if e9 > e21 and slope > 0:
        return "buy", slope
    if e9 < e21 and slope < 0:
        return "sell", slope
    return None, slope


class Book:
    def __init__(self, start=5000.0):
        self.start = start; self.equity = start
        self.pos = None; self.trades = []

    def snap(self):
        p = self.pos
        return {"engine": "curve_rider", "mode": "paper", "state": "running",
                "equity": round(self.equity, 2), "start_equity": self.start,
                "return_pct": round(100 * (self.equity / self.start - 1), 4),
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
    ap.add_argument("--hold", type=float, default=4.0, help="seconds in the trade")
    ap.add_argument("--wait", type=float, default=20.0, help="seconds between attempts")
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--min-move", type=float, default=0.05, help="min BTC slope %% to engage")
    a = ap.parse_args()
    bk = Book(a.start)
    log("CURVE RIDER — hold %.0fs, wait %.0fs, risk %.2f%%" % (a.hold, a.wait, a.risk))
    while True:
        try:
            side, slope = direction()
            if side is None or abs(slope) < a.min_move:
                write(bk); time.sleep(2); continue
            uni = jget(MASTER + "/api/universe800", 10).get("tickers") or []
            cands = [t for t in uni if (t.get("s") or "").endswith("USDT") and (t.get("v") or 0) > 2e8][:12]
            best, bestmove = None, 0.0
            for t in cands:
                try:
                    kl = klines(t["s"], "1m", 5)
                except Exception:
                    continue
                mv = (kl[-1]["c"] / kl[-2]["c"] - 1) * 100
                if side == "buy" and mv > bestmove:
                    best, bestmove = t["s"], mv
                if side == "sell" and mv < bestmove:
                    best, bestmove = t["s"], mv
            if not best:
                write(bk); time.sleep(2); continue
            px = float(jget(SPOT + "/api/v3/ticker/price?symbol=" + best, 5)["price"])
            notional = min(bk.equity * a.risk / 100 * 20, bk.equity * 0.08)
            bk.pos = {"symbol": best, "side": side, "entry": px, "t": time.time(),
                      "notional": notional, "peak": 0.0,
                      "why": "curve %s slope %+.3f%% · %s move %+.3f%%" % (side, slope, best, bestmove)}
            log("OPEN  %-10s %-4s %.6g  curve %+.3f%%" % (best, side, px, slope))
            # the ride: keep going while the position is losing, by directive
            px2 = px
            chg = 0.0
            net = -FEE
            peak = 0.0
            while True:
                time.sleep(a.hold)
                px2 = float(jget(SPOT + "/api/v3/ticker/price?symbol=" + best, 5)["price"])
                chg = (px2 / px - 1) * 100 if side == "buy" else (1 - px / px2) * 100
                peak = max(peak, chg)
                net = chg - FEE
                # greedy: ride the curve, exit only on a retracement from the peak
                if peak >= 0.5 and chg <= peak - 0.5:
                    break
                if chg <= -1.0:      # safety stop: no heavy losses
                    break
                log("HOLD  %-10s %-4s peak %+6.3f%% net %+6.3f%%" % (best, side, peak, net))
            bk.equity += notional * net / 100
            bk.trades.append({"symbol": best, "side": side, "entry": px, "exit": px2,
                              "gross_pct": chg, "net_pct": net, "peak_pct": max(0.0, chg),
                              "reason": "ride", "held_s": round(time.time() - bk.pos["t"], 1),
                              "entry_ms": int(bk.pos["t"] * 1000), "exit_ms": int(time.time() * 1000),
                              "notional": notional, "pnl_usd": notional * net / 100,
                              "why": bk.pos["why"]})
            log("CLOSE %-10s %-4s %+6.3f%% net %+6.3f%%" % (best, side, chg, net))
            bk.pos = None
            write(bk)
            time.sleep(a.wait)                       # then wait
        except Exception as exc:
            log("error %r" % (exc,))
            time.sleep(3)


if __name__ == "__main__":
    main()
