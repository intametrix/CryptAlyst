#!/usr/bin/env python3
"""curve_rider - read what the market is thinking and ride the curve.

Signals combine order-book depth imbalance, OI change, price momentum and
volume — the microstructure that shows real buying/selling pressure. Picks the
strongest signal and rides it with a trailing stop until it reverses.

    python3 curve_rider.py --trail 2.0 --stop 5.0
"""
import argparse, json, os, time
import ratelimit

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
SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
FEE = 0.2


def jget(u, t=6.0):
    return ratelimit.jget(u, t, {"User-Agent": "rider/1"})


def px(sym):
    return float(jget(SPOT + "/api/v3/ticker/price?symbol=" + sym, 5)["price"])


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


def market_signal():
    """Scan liquid markets for the strongest depth/OI/price/volume signal."""
    uni = jget(MASTER + "/api/universe800", 10).get("tickers") or []
    cands = [t for t in uni if str(t.get("s", "")).endswith("USDT") and t.get("price")]
    cands.sort(key=lambda t: -(t.get("qvol") or t.get("v") or 0))
    cands = cands[:12]          # probe the 12 most liquid
    best = None
    for t in cands:
        sym = t["s"]
        # order-book depth imbalance: are bids or asks heavier?
        try:
            d = jget(SPOT + "/api/v3/depth?symbol=%s&limit=50" % sym, 5)
            bids = sum(float(x[1]) for x in d.get("bids") or [])
            asks = sum(float(x[1]) for x in d.get("asks") or [])
            imb = (bids - asks) / (bids + asks) if (bids + asks) else 0.0
        except Exception:
            continue
        # OI change over the last 5m: new money coming in?
        try:
            oi = jget(FAPI + "/futures/data/openInterestHist?symbol=%s&period=5m&limit=2" % sym, 5)
            oichg = (float(oi[-1]["sumOpenInterest"]) / float(oi[0]["sumOpenInterest"]) - 1) * 100 if len(oi) >= 2 else 0.0
        except Exception:
            oichg = 0.0
        chg = t.get("c") or 0.0
        vol = (t.get("qvol") or 0) / 1e8
        # "what the market is thinking": depth pressure + OI build + price momentum + volume
        score = imb * 50 + oichg * 20 + chg * 0.2 + min(vol, 5) * (1 if imb >= 0 else -1)
        if best is None or abs(score) > abs(best["score"]):
            best = {"symbol": sym, "score": score, "imb": imb, "oi": oichg,
                    "chg": chg, "price": float(t["price"])}
    if best is None:
        return None
    side = "buy" if best["score"] >= 0 else "sell"
    return best["symbol"], side, best["score"], best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trail", type=float, default=2.0)
    ap.add_argument("--stop", type=float, default=5.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--poll", type=float, default=3.0)
    a = ap.parse_args()
    bk = Book(a.start)
    log("CURVE RIDER — depth/OI/price/volume signal, trail %.1f%%, stop %.1f%%" % (a.trail, a.stop))
    while True:
        try:
            if bk.pos is None:
                mv = market_signal()
                if not mv:
                    time.sleep(3); continue
                sym, side, score, info = mv
                notional = min(bk.equity * a.risk / 100 * 20, bk.equity * 0.08)
                bk.pos = {"symbol": sym, "side": side, "entry": info["price"], "t": time.time(),
                          "notional": notional, "peak": 0.0,
                          "why": "imb %+.2f oi %+.2f%% chg %+.1f%% score %+.1f" % (
                              info["imb"], info["oi"], info["chg"], score)}
                log("OPEN  %-10s %-4s score %+.1f (imb %+.2f, oi %+.2f%%, 24h %+.1f%%)" % (
                    sym, side, score, info["imb"], info["oi"], info["chg"]))
                write(bk)
                continue
            p = bk.pos
            cur = px(p["symbol"])
            mv = (cur / p["entry"] - 1) * 100 if p["side"] == "buy" else (1 - cur / p["entry"]) * 100
            p["peak"] = max(p["peak"], mv)
            net = mv - FEE
            if mv <= p["peak"] - a.trail or mv <= -a.stop:
                bk.equity += p["notional"] * net / 100
                bk.trades.append({"symbol": p["symbol"], "side": p["side"], "entry": p["entry"],
                                  "exit": cur, "gross_pct": mv, "net_pct": net,
                                  "peak_pct": p["peak"], "reason": "trail" if mv > -a.stop else "stop",
                                  "held_s": time.time() - p["t"],
                                  "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                                  "notional": p["notional"], "pnl_usd": p["notional"] * net / 100,
                                  "why": p["why"]})
                log("CLOSE %-10s %-4s %+6.2f%% net %+6.2f%% (peak %+6.2f%%)" % (
                    p["symbol"], p["side"], mv, net, p["peak"]))
                bk.pos = None
                write(bk)
            time.sleep(a.poll)
        except Exception as exc:
            log("error %r" % (exc,))
            time.sleep(3)


if __name__ == "__main__":
    main()
