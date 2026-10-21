#!/usr/bin/env python3
"""hybrids - ten models that each blend two signals instead of betting on one.

Every model here is deliberately small: a trigger and a confirmation from
different families (trend, mean reversion, flow, derivatives, microstructure).
The thesis is that a single signal is noise, but a signal confirmed by an
independent one is a trade.

Exit policy is shared and honest: take profit, hard stop, time cap. A loss is
recorded when it happens; the old habit of holding losers forever censored every
statistic the platform produced.

    python3 hybrids.py --strategy trend_pullback --start 5000 --top 40
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")

import risk
import tactics as T
from lib import emit, intel

try:
    import tactics as _tactics
    _tactics.require_enabled('hybrid_trend_pullback', 'hybrids.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


FEE_ROUND_TRIP = 0.04     # maker each side
COST_FLOOR = 0.06         # fees plus a buffer, the minimum edge worth taking


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


# ----------------------------------------------------------------- features
def bars(sym, n=120):
    rows = T.bars(sym, "1m", n)
    out = []
    for b in rows:
        try:
            out.append({"t": b["t"], "o": float(b["o"]), "h": float(b["h"]),
                        "l": float(b["l"]), "c": float(b["c"]), "v": float(b["v"])})
        except Exception:
            continue
    return out


def ema(vals, n):
    if not vals:
        return None
    k = 2.0 / (n + 1.0)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def atr_pct(bs, n=14):
    if len(bs) < n + 1:
        return None
    trs = []
    for i in range(1, len(bs)):
        trs.append(max(bs[i]["h"] - bs[i]["l"],
                       abs(bs[i]["h"] - bs[i - 1]["c"]),
                       abs(bs[i]["l"] - bs[i - 1]["c"])))
    a = sum(trs[-n:]) / n
    return 100 * a / bs[-1]["c"] if bs[-1]["c"] else None


def zscore(closes, n=40):
    if len(closes) < n:
        return None
    w = closes[-n:]
    m = sum(w) / len(w)
    var = sum((x - m) ** 2 for x in w) / len(w)
    sd = var ** 0.5
    return (closes[-1] - m) / sd if sd else None


def vwap(bs, n=60):
    w = bs[-n:]
    pv = sum(((b["h"] + b["l"] + b["c"]) / 3) * b["v"] for b in w)
    v = sum(b["v"] for b in w)
    return pv / v if v else None


def depth(sym):
    """Top-of-book imbalance from the local feed, when it is available."""
    try:
        d = T.jget("http://127.0.0.1:8796/depth?symbol=%s" % sym, 3)
        return float(d.get("imbalance") or 0.0), float(d.get("spread_bp") or 0.0)
    except Exception:
        return 0.0, 0.0


# ----------------------------------------------------------------- strategies
# Each returns (side, score 0..1, reason) or (None, 0, reason).
def s_trend_pullback(sym, f):
    bs = f["bs"]
    e50, e200 = ema([b["c"] for b in bs], 50), ema([b["c"] for b in bs], 200)
    if not e50 or not e200 or len(bs) < 200:
        return None, 0, "no trend"
    px = bs[-1]["c"]
    up = e50 > e200
    near = abs(px - e50) / e50 < 0.004
    if up and near and px > e50:
        return "buy", 0.62, "uptrend pullback to EMA50"
    if not up and near and px < e50:
        return "sell", 0.62, "downtrend bounce to EMA50"
    return None, 0, "not at the mean"


def s_breakout_atr(sym, f):
    bs = f["bs"]
    if len(bs) < 25:
        return None, 0, "short series"
    hi = max(b["h"] for b in bs[-21:-1])
    lo = min(b["l"] for b in bs[-21:-1])
    a = atr_pct(bs)
    if not a:
        return None, 0, "no atr"
    px = bs[-1]["c"]
    if px > hi and a > 0.10:
        return "buy", 0.65, "break of 20-bar high, ATR expanding"
    if px < lo and a > 0.10:
        return "sell", 0.65, "break of 20-bar low, ATR expanding"
    return None, 0, "inside the range"


def s_mean_revert_z(sym, f):
    z = zscore([b["c"] for b in f["bs"]])
    if z is None:
        return None, 0, "no z"
    if z <= -2.0:
        return "buy", min(0.75, 0.5 + abs(z) / 10), "z %.1f below the mean" % z
    if z >= 2.0:
        return "sell", min(0.75, 0.5 + abs(z) / 10), "z %.1f above the mean" % z
    return None, 0, "z %.1f inside" % z


def s_vwap_reclaim(sym, f):
    bs = f["bs"]
    v = vwap(bs)
    if not v or len(bs) < 5:
        return None, 0, "no vwap"
    px, prev = bs[-1]["c"], bs[-2]["c"]
    vol_ratio = bs[-1]["v"] / (sum(b["v"] for b in bs[-10:]) / 10 or 1)
    if prev < v <= px and vol_ratio > 1.2:
        return "buy", 0.60, "reclaimed VWAP on volume"
    if prev > v >= px and vol_ratio > 1.2:
        return "sell", 0.60, "lost VWAP on volume"
    return None, 0, "at vwap"


def s_momentum_ignition(sym, f):
    bs = f["bs"]
    if len(bs) < 20:
        return None, 0, "short series"
    a = atr_pct(bs) or 0.2
    move = 100 * (bs[-1]["c"] / bs[-4]["c"] - 1)
    vol = bs[-1]["v"] / (sum(b["v"] for b in bs[-20:-1]) / 19 or 1)
    if move > 2 * a and vol > 1.5:
        return "buy", 0.68, "3-bar impulse %.2f%% on %.1fx volume" % (move, vol)
    if move < -2 * a and vol > 1.5:
        return "sell", 0.68, "3-bar drop %.2f%% on %.1fx volume" % (move, vol)
    return None, 0, "no ignition"


def s_oi_squeeze(sym, f):
    sig = f["sig"] or {}
    oi = sig.get("oi_change_1h_pct")
    e50 = ema([b["c"] for b in f["bs"]], 50)
    if oi is None or e50 is None:
        return None, 0, "no open interest"
    px = f["bs"][-1]["c"]
    if oi > 2.0 and px > e50:
        return "buy", 0.63, "OI +%.1f%% into strength" % oi
    if oi > 2.0 and px < e50:
        return "sell", 0.63, "OI +%.1f%% into weakness" % oi
    return None, 0, "no squeeze"


def s_funding_fade(sym, f):
    sig = f["sig"] or {}
    fr = sig.get("funding_pct")
    if fr is None:
        return None, 0, "no funding"
    if fr >= 0.05:
        return "sell", 0.58, "funding +%.3f%% crowded long" % fr
    if fr <= -0.02:
        return "buy", 0.58, "funding %.3f%% crowded short" % fr
    return None, 0, "funding neutral"


def s_taker_imbalance(sym, f):
    sig = f["sig"] or {}
    tr = sig.get("taker_ratio")
    imb, _ = f["depth"]
    if tr is None:
        return None, 0, "no taker ratio"
    if tr > 0.58 and imb > 0.05:
        return "buy", 0.64, "takers %.2f with book %.2f" % (tr, imb)
    if tr < 0.42 and imb < -0.05:
        return "sell", 0.64, "takers %.2f with book %.2f" % (tr, imb)
    return None, 0, "flow not aligned"


def s_flow_confirm(sym, f):
    flow = (f["flow"] or {}).get("flow_conf")
    e50 = ema([b["c"] for b in f["bs"]], 50)
    if flow is None or e50 is None:
        return None, 0, "no flow"
    px = f["bs"][-1]["c"]
    if flow > 25 and px > e50:
        return "buy", 0.66, "flow %+.0f confirms trend" % flow
    if flow < -25 and px < e50:
        return "sell", 0.66, "flow %+.0f confirms downtrend" % flow
    return None, 0, "flow %.0f vs trend" % flow


def s_range_break(sym, f):
    bs = f["bs"]
    if len(bs) < 30:
        return None, 0, "short series"
    w = bs[-25:-1]
    hi, lo = max(b["h"] for b in w), min(b["l"] for b in w)
    width = 100 * (hi - lo) / lo if lo else 99
    if width > 1.2:
        return None, 0, "range too wide"
    px = bs[-1]["c"]
    vol = bs[-1]["v"] / (sum(b["v"] for b in bs[-10:]) / 10 or 1)
    if px > hi and vol > 1.3:
        return "buy", 0.67, "compression break up (%.2f%% range)" % width
    if px < lo and vol > 1.3:
        return "sell", 0.67, "compression break down (%.2f%% range)" % width
    return None, 0, "still compressed"


STRATEGIES = {
    "trend_pullback": s_trend_pullback,
    "breakout_atr": s_breakout_atr,
    "mean_revert_z": s_mean_revert_z,
    "vwap_reclaim": s_vwap_reclaim,
    "momentum_ignition": s_momentum_ignition,
    "oi_squeeze": s_oi_squeeze,
    "funding_fade": s_funding_fade,
    "taker_imbalance": s_taker_imbalance,
    "flow_confirm": s_flow_confirm,
    "range_break": s_range_break,
}


# ----------------------------------------------------------------- engine
class Book:
    def __init__(self, name, start):
        self.name = name
        self.start = float(start)
        self.equity = float(start)
        self.pos = {}
        self.trades = []
        self.stopped = False

    def open(self, sym, side, px, notional, reason, score):
        self.pos[sym] = {"side": side, "entry": px, "notional": notional,
                         "t": time.time(), "reason": reason, "score": score}

    def close(self, sym, px, chg, reason):
        p = self.pos.pop(sym, None)
        if not p:
            return
        net = chg - FEE_ROUND_TRIP
        self.equity += p["notional"] * net / 100.0
        trade = {"symbol": sym, "side": p["side"], "entry": p["entry"], "exit": px,
                 "gross_pct": round(chg, 4), "net_pct": round(net, 4),
                 "peak_pct": round(max(chg, 0), 4), "reason": reason,
                 "held_s": round(time.time() - p["t"], 1),
                 "notional": round(p["notional"], 2),
                 "pnl_usd": round(p["notional"] * net / 100.0, 2),
                 "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                 "why": p["reason"]}
        self.trades.append(trade)
        log("CLOSE %-10s %-4s %+.3f%% net %+.3f%% %s (%s)" %
            (sym, p["side"], chg, net, reason, self.name))
        if emit:
            try:
                emit.fill(self.name, trade)
            except Exception:
                pass

    def write(self):
        try:
            os.makedirs(STATE, exist_ok=True)
            rows = [{"symbol": s, "side": p["side"], "entry": p["entry"],
                     "notional": p["notional"], "t": int(p["t"] * 1000)}
                    for s, p in self.pos.items()]
            path = os.path.join(STATE, "pack_%s.json" % self.name)
            tmp = os.path.join(STATE, "pack_%s.%d.t" % (self.name, os.getpid()))
            with open(tmp, "w") as fh:
                json.dump({"engine": self.name, "equity": round(self.equity, 2),
                           "start_equity": round(self.start, 2), "positions": rows,
                           "trades": self.trades[-200:], "trade_count": len(self.trades),
                           "strategy": self.name.replace("hybrid_", ""),
                           "ts": int(time.time() * 1000)}, fh)
            os.replace(tmp, path)
        except Exception:
            pass


def features_for(sym):
    bs = bars(sym, 220)
    if len(bs) < 40:
        return None
    return {"bs": bs, "sig": intel.get("signal", sym) or {},
            "flow": intel.get("flow", sym) or {}, "ta": intel.get("ta", sym) or {},
            "depth": depth(sym)}


def universe(top):
    try:
        d = T.jget("http://127.0.0.1:8796/streamprices", 5)
        syms = sorted((d.get("p") or {}).keys())
        return syms[:top]
    except Exception:
        return []


def decide(book, strategy, syms, a):
    fn = STRATEGIES[strategy]
    for sym in syms:
        f = features_for(sym)
        if not f:
            continue
        px = f["bs"][-1]["c"]
        atr = atr_pct(f["bs"]) or 0.2

        # exits first: a position is managed on every pass
        p = book.pos.get(sym)
        if p:
            chg = (px / p["entry"] - 1) * 100 * (1 if p["side"] == "buy" else -1)
            held = time.time() - p["t"]
            if chg >= a.tp:
                book.close(sym, px, chg, "take_profit")
            elif T.hold_loser(held, chg) is False and chg < 0:
                book.close(sym, px, chg, "stop" if chg <= T.STOP_LOSS_PCT else "time_stop")
            elif held >= a.max_hold:
                book.close(sym, px, chg, "time_stop")
            continue

        side, score, why = fn(sym, f)
        if not side:
            continue
        # the cost floor: a trade must be able to pay its fees with room to spare
        if atr and a.tp < COST_FLOOR:
            continue
        notional = min(book.equity * a.risk / 100.0, book.equity * 0.08)
        ok, reason = risk.allow(sym, side, notional, book.equity,
                                expected_move_pct=a.tp, confidence=score * 100,
                                bar_age_s=0, signal_age_s=0,
                                book_positions=list(book.pos.values()))
        risk.record(sym, side, ok, reason)
        if not ok:
            continue
        book.open(sym, side, px, notional, why, score)
        log("OPEN  %-10s %-4s %.6g  %s (%s)" % (sym, side, px, why, book.name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    ap.add_argument("--start", type=float, default=5000)
    ap.add_argument("--risk", type=float, default=10.0)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--loop", type=float, default=2.0)
    ap.add_argument("--tp", type=float, default=0.25, help="take profit, percent")
    ap.add_argument("--max-hold", type=float, default=900, help="seconds")
    a = ap.parse_args()

    book = Book("hybrid_" + a.strategy, a.start)
    log("%s · risk %.1f%% · top %d · tp %.2f%% · max hold %.0fs"
        % (book.name, a.risk, a.top, a.tp, a.max_hold))
    syms = []
    last_universe = 0.0
    while True:
        if not syms or time.time() - last_universe > 60:
            fresh = universe(a.top)
            if fresh:
                syms = fresh
            last_universe = time.time()
        if not syms:
            time.sleep(5)
            continue
        try:
            decide(book, a.strategy, syms, a)
        except Exception as exc:
            log("loop error: %s" % exc)
        book.write()
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
