#!/usr/bin/env python3
"""alpha_signals - ten models that trade the extended signal set.

Every model here reads state/signals.json (order book, derivatives positioning,
taker flow, slippage) instead of OHLCV. One shared file, so ten models cost the
same upstream as one.

    python3 alpha_signals.py --every 15 --start 5000 --risk 0.5
"""
import argparse, json, os, time
import maker, risk, tactics as T

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
SIG = os.path.join(STATE, "signals.json")
LOCK = 1.00
STOP = 1.50
CLOCK = 3600
FEE = 0.04          # maker round trip


def load_signals():
    try:
        with open(SIG) as fh:
            return json.load(fh).get("rows") or []
    except Exception:
        return []


def f(sig, k, d=0.0):
    v = sig.get(k)
    return d if v is None else v


# ---- the ten rule sets: each returns "buy", "sell" or None -----------------

def m_book_imb(sig):
    v = f(sig, "book_imbalance")
    if v > 0.12: return "buy"
    if v < -0.12: return "sell"

def m_taker_flow(sig):
    v = f(sig, "buy_ratio", 0.5)
    if v > 0.54: return "buy"
    if v < 0.46: return "sell"

def m_smart_dumb(sig):
    v = f(sig, "smart_vs_dumb", 1.0)
    if v > 1.15: return "buy"
    if v < 0.87: return "sell"

def m_oi_divergence(sig):
    oi = f(sig, "oi_change_1h_pct")
    px = f(sig, "px_vs_sma50")
    if oi > 1.0 and px < 0: return "buy"    # money entering a falling price
    if oi > 1.0 and px > 0: return "sell"   # late longs stacking
    if oi < -1.0 and px > 0: return "sell"  # shorts covering into strength
    if oi < -1.0 and px < 0: return "buy"

def m_funding_extreme(sig):
    v = f(sig, "funding_pct")
    if v > 0.012: return "sell"
    if v < -0.012: return "buy"

def m_poc_revert(sig):
    v = f(sig, "px_vs_poc")
    if v < -0.50: return "buy"
    if v > 0.50: return "sell"

def m_vol_z(sig):
    z = f(sig, "vol_z"); b = f(sig, "buy_ratio", 0.5)
    if z > 1.4 and b > 0.51: return "buy"
    if z > 1.4 and b < 0.49: return "sell"

def m_tight_spread(sig):
    sp = f(sig, "spread_bps", 99); im = f(sig, "book_imbalance")
    if sp <= 2.5 and im > 0.08: return "buy"
    if sp <= 2.5 and im < -0.08: return "sell"

def m_mtf(sig):
    v = f(sig, "mtf_agreement", 0)
    if v >= 2: return "buy"
    if v <= -2: return "sell"

def m_trend_structure(sig):
    st = (sig.get("structure") or ""); hl = (sig.get("hl_structure") or "")
    if st == "higher high" and hl == "higher low": return "buy"
    if st == "lower high" and hl == "lower low": return "sell"


MODELS = {
    "book_imb": m_book_imb, "taker_flow": m_taker_flow, "smart_dumb": m_smart_dumb,
    "oi_divergence": m_oi_divergence, "funding_extreme": m_funding_extreme,
    "poc_revert": m_poc_revert, "vol_z": m_vol_z, "tight_spread": m_tight_spread,
    "mtf_stack": m_mtf, "structure": m_trend_structure,
}


class Book:
    def __init__(self, name, start, risk_pct):
        self.name, self.start, self.risk_pct = name, start, risk_pct
        self.equity, self.pos, self.trades = start, {}, []

    def snap(self):
        return {"engine": self.name, "mode": "paper-maker", "state": "running",
                "equity": round(self.equity, 2), "start_equity": self.start,
                "return_pct": round(100 * (self.equity / self.start - 1), 4),
                "realised": round(self.equity - self.start, 2), "unrealised": 0.0,
                "trade_count": len(self.trades),
                "positions": [{"symbol": s, "side": p["side"], "entry": p["entry"],
                               "notional": p["notional"], "entry_ms": int(p["t"] * 1000),
                               "held_s": time.time() - p["t"], "why": p["why"]}
                              for s, p in self.pos.items()],
                "trades": self.trades[-200:]}


def write(bk):
    p = os.path.join(STATE, "pack_%s.json" % bk.name)
    with open(p + ".t", "w") as fh:
        json.dump(bk.snap(), fh)
    os.replace(p + ".t", p)


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--every", type=float, default=15.0)
    a = ap.parse_args()
    books = {n: Book(n, a.start, a.risk) for n in MODELS}
    log("ALPHA SIGNALS — %d models on the extended signal set" % len(books))

    while True:
        rows = load_signals()
        if rows:
            px = {r["symbol"]: None for r in rows}
            for bk in books.values():
                # exits
                for sym in list(bk.pos):
                    p = bk.pos[sym]
                    try:
                        cur = float(T.jget("https://api.binance.com/api/v3/ticker/price?symbol=" + sym, 6)["price"])
                    except Exception:
                        continue
                    chg = (cur / p["entry"] - 1) * 100 if p["side"] == "buy" else (1 - cur / p["entry"]) * 100
                    held = time.time() - p["t"]
                    reason = ("lock" if chg >= LOCK else "stop" if chg <= -STOP else
                              "clock" if held >= CLOCK else None)
                    if reason:
                        net = chg - FEE
                        bk.equity += p["notional"] * net / 100
                        bk.trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                                          "exit": cur, "gross_pct": chg, "net_pct": net,
                                          "peak_pct": max(0.0, chg), "reason": reason,
                                          "held_s": held, "entry_ms": int(p["t"] * 1000),
                                          "exit_ms": int(time.time() * 1000),
                                          "notional": p["notional"], "pnl_usd": p["notional"] * net / 100,
                                          "why": p["why"]})
                        log("%-15s CLOSE %-10s %-4s %+6.3f%% net %+6.3f%% %s" % (
                            bk.name, sym, p["side"], chg, net, reason))
                        bk.pos.pop(sym)
                write(bk)

            # entries
            for sig in rows:
                sym = sig["symbol"]
                for name, rule in MODELS.items():
                    bk = books[name]
                    if sym in bk.pos or len(bk.pos) >= 6:
                        continue
                    side = rule(sig)
                    if not side:
                        continue
                    conf = sig.get("flow_conf") or 0.0
                    base = min(bk.equity * bk.risk_pct / 100 * 40, bk.equity * 0.08)
                    notional = risk.size_for(base, conf)
                    ok, why = risk.allow(sym, side, notional, bk.equity,
                                         (sig.get("score") or 0) / 100.0,
                                         sig.get("slippage_bps_5k"), confidence=conf,
              book_positions=list(bk.pos.values()) if isinstance(getattr(bk, 'pos', None), dict) else None)
                    risk.record(sym, side, ok, why)
                    if not ok:
                        continue
                    q_px, q_prob, _ = maker.quote(sym, side, notional)
                    if q_px is None or q_prob < 0.30:
                        continue
                    bk.pos[sym] = {"side": side, "entry": q_px, "t": time.time(),
                                   "notional": notional,
                                   "why": "MAKER %s score %+.1f flow %+.0f (lvl %+.2f trd %+.2f div %+.2f) $%.0f" % (
                                       name, sig.get("score") or 0, conf,
                                       (sig.get("flow_parts") or {}).get("level", 0),
                                       (sig.get("flow_parts") or {}).get("trend", 0),
                                       (sig.get("flow_parts") or {}).get("divergence", 0),
                                       notional)}
                    log("%-15s OPEN  %-10s %-4s %.6g" % (name, sym, side, q_px))
                    write(bk)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
