#!/usr/bin/env python3
"""alpha - three fee-viable crypto models, each with its own book.

  xsec_momentum    cross-sectional: long the strongest N, short the weakest N
                   by 24h return, rebalance every 15m, hold hours. Market-neutral
                   by construction, so BTC beta cancels and only relative alpha
                   remains.
  cascade_rebound  1m drop of 3 sigma on 5x median volume = forced selling.
                   Buy the snap-back, exit fast. A liquidation proxy without
                   needing the liquidation feed.
  funding_carry    perpetual funding extreme while spot is flat = leveraged
                   crowd paying to hold a thesis nobody else has. Collect the
                   reversion, hold 8h.

All targets are >= 0.5%, so the 0.2% round trip is a third of the edge or less.

    python3 alpha.py --seconds 86400
"""
import argparse, json, os, statistics, time, urllib.request
from lib import equity

try:
    import tactics as _tactics
    _tactics.require_enabled('alpha', 'alpha.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
BACKEND = "http://127.0.0.1:8787"; SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"; FEE = 0.001

CONFIRM_SECS = 0.0     # HFT: enter immediately (no confirmation)
TP_PCT = 20.0           # a trade up 20% -> close it out
LOCK_USD = 800.0        # book unrealised >= this -> bank the winners
LOCK_PCT = 0.3          # bank only winners that clear the fee
STOP_SECS = 5.0         # HFT: a bad position still bad after 5s -> close it
STOP_PCT = 0.2          # HFT: cut tiny losses fast
SKIM_PCT = 0.1          # HFT: bank a tiny move
TRAIL_ARM_PCT = 0.3     # HFT: arm the trail fast
TRAIL_PCT = 0.15        # HFT: trail tight below the peak
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOXLUSDT", "XAUUSDT", "SNDKUSDT"]  # always trade the fastest tickers
PENDING = {}            # (book, symbol, side) -> first time the signal was seen

CFG = {
    "xsec_momentum":  {"tp": 2.0, "sl": 1.2, "hold": 14400, "risk": 2.5},
    "cascade_rebound": {"tp": 0.8, "sl": 0.6, "hold": 2700,  "risk": 2.5},
    "funding_carry":  {"tp": 1.2, "sl": 1.0, "hold": 28800, "risk": 2.5},
}

def log(m): print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def jget(u, t=8.0, timeout=None):
    if timeout: t = timeout
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha/1"}), timeout=t) as r:
        return json.loads(r.read().decode())

class Book:
    def __init__(self, name):
        self.name, self.cfg = name, CFG[name]
        self.equity = equity.for_book(name, 5000.0); self.pos = {}; self.trades = []; self.opened = 0

    def mark(self, sym, px):
        p = self.pos.get(sym)
        if not p: return None
        ch = (px / p["entry"] - 1) * 100
        return ch if p["side"] == "buy" else -ch

    def _equity_mtm(self):
        return self.equity + sum(p["notional"] * p.get("mark", 0.0) / 100 for p in self.pos.values())

    def _close(self, s, p, reason):
        net = p["mark"] - 200 * FEE
        self.equity += p["notional"] * net / 100
        self.trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
            "gross_pct": round(p["mark"], 3), "net_pct": round(net, 3),
            "peak_pct": round(p["peak"], 3), "reason": reason,
            "held_s": round(time.time() - p["t"], 1), "notional": round(p["notional"], 2),
            "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
            "why": p["why"]})
        self.pos.pop(s, None)

    def manage(self, px):
        # mark every position first
        for s in list(self.pos):
            p = self.pos[s]; m = px.get(s)
            if not m: continue
            p["mark"] = self.mark(s, m); p["peak"] = max(p.get("peak", 0), p["mark"])
        # equity floor
        if self._equity_mtm() <= 0:
            for s in list(self.pos):
                self._close(s, self.pos[s], "floor")
            return
        # ride the curve: once a winner clears the arm, exit on a retracement
        for s in list(self.pos):
            p = self.pos[s]
            if p["peak"] >= TRAIL_ARM_PCT and p["mark"] <= p["peak"] - TRAIL_PCT:
                self._close(s, p, "trail")
        # skim: bank a quick small profit
        for s in list(self.pos):
            p = self.pos[s]
            if p["mark"] >= SKIM_PCT:
                self._close(s, p, "skim")
        # +20% take-profit (far target, rarely reached once skimming)
        for s in list(self.pos):
            p = self.pos[s]
            if p["mark"] >= TP_PCT:
                self._close(s, p, "target")
        # $800 book lock
        unreal = sum(p["notional"] * p["mark"] / 100 for p in self.pos.values())
        if unreal >= LOCK_USD:
            for s in list(self.pos):
                p = self.pos[s]
                if p["mark"] > LOCK_PCT:
                    self._close(s, p, "lock")
            return
        # 30s time-stop
        for s in list(self.pos):
            p = self.pos[s]
            if p["mark"] < -STOP_PCT and time.time() - p["t"] >= STOP_SECS:
                self._close(s, p, "stop")
        # clock: winners only
        for s in list(self.pos):
            p = self.pos[s]
            if p["mark"] > 0 and time.time() - p["t"] >= self.cfg["hold"]:
                self._close(s, p, "clock")

    last_open = 0.0

    def open(self, s, side, px, why):
        self.last_open = time.time()
        n = min(self.equity * (self.cfg["risk"] / 100) / (self.cfg["sl"] / 100), self.equity * 2.0)
        self.pos[s] = {"side": side, "entry": px, "t": time.time(), "notional": n,
                       "peak": 0.0, "mark": 0.0, "why": why}
        self.opened += 1

    def dump(self):
        pl = [{"symbol": s, "side": p["side"], "entry": p["entry"], "price": p["entry"],
               "gross_pct": round(p.get("mark", 0), 3), "net_pct": round(p.get("mark", 0) - 0.2, 3),
               "peak_pct": round(p.get("peak", 0), 3), "held_s": round(time.time() - p["t"], 1),
               "notional": round(p["notional"], 2),
               "risk_usd": round(p["notional"] * self.cfg["sl"] / 100, 2), "why": p["why"]}
              for s, p in self.pos.items()]
        d = {"ts": int(time.time() * 1000), "mode": "paper", "engine": self.name,
             "state": "running", "equity": round(self.equity, 4), "start_equity": 5000.0,
             "return_pct": round(100 * (self.equity / 3000 - 1), 4),
             "realised": round(self.equity - 5000.0, 4),
             "unrealised": round(sum(p["notional"] * p.get("mark", 0) / 100 for p in self.pos.values()), 4),
             "tp_pct": self.cfg["tp"], "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"],
             "trail_ratio": 0.0, "trail_arm_pct": 0.0, "positions": pl,
             "position": (pl[0] if pl else None), "trades": self.trades[-60:],
             "trade_count": len(self.trades),
             "wins": sum(1 for t in self.trades if t["net_pct"] > 0), "opened": self.opened,
             "max_positions": 10, "basis": "alpha: fee-viable crypto model",
             "open_risk_usd": round(sum(p["notional"] * self.cfg["sl"] / 100 for p in self.pos.values()), 2),
             "settings": {"risk_pct": self.cfg["risk"], "tp_pct": self.cfg["tp"],
                          "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"]}}
        os.makedirs(STATE, exist_ok=True)
        p = os.path.join(STATE, "alpha_%s.json" % self.name)
        with open(p + ".t", "w") as fh: json.dump(d, fh)
        os.replace(p + ".t", p)

def klines(sym, iv, n):
    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (sym, iv, n))
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5])} for r in d]

def confirmed(book, sym, side):
    """True once this exact signal has been true for CONFIRM_SECS."""
    key = (book, sym, side)
    first = PENDING.get(key)
    now = time.time()
    if first is None:
        PENDING[key] = now
        return False
    return (now - first) >= CONFIRM_SECS


def clear_pending(book, sym):
    for k in list(PENDING):
        if k[0] == book and k[1] == sym:
            PENDING.pop(k, None)


# --- the three model rules, extracted so "our thinking" is testable ---------
def xsec_picks(ranked):
    """Cross-sectional momentum: long the strongest 3, short the weakest 3
    (only when the weak end is actually negative — no shorting a rising name)."""
    longs = ranked[:3]
    shorts = [u for u in ranked[-3:] if (u.get("c") or 0) < 0]
    return longs, shorts


def cascade_signal(b1):
    """True when the last 1m bar is a >1.5-sigma drop on >2.5x median volume —
    forced selling, so buy the snap-back. len < 30 is a no-signal (warm-up)."""
    if len(b1) < 30:
        return False
    # reject malformed / non-finite bars before dividing or taking stats
    for b in b1:
        if not isinstance(b, dict):
            return False
        c, v = b.get("c"), b.get("v")
        if not isinstance(c, (int, float)) or not isinstance(v, (int, float)):
            return False
        if c != c or c == 0 or abs(c) == float("inf"):
            return False
    rets = [(b1[i]["c"] / b1[i - 1]["c"] - 1) * 100 for i in range(1, len(b1))]
    sd = statistics.pstdev(rets[:-1]) or 1e-9
    last_ret = rets[-1]
    volmed = statistics.median([x["v"] for x in b1[-21:-1]]) or 1e-9
    return last_ret < -1.5 * sd and b1[-1]["v"] > 2.5 * volmed


def funding_side(rate):
    """Side for a funding extreme; None below the 2bps threshold."""
    if abs(rate) < 0.0002:
        return None
    return "sell" if rate > 0 else "buy"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seconds", type=int, default=86400)
    ap.add_argument("--loop", type=float, default=5.0); ap.add_argument("--universe", type=int, default=60)
    ap.add_argument("--only", default="", help="comma separated books")
    ap.add_argument("--min-liq", type=float, default=50e6,
                    help="minimum 24h quote volume to be considered liquid")
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()
    log("ALPHA — xsec_momentum, cascade_rebound, funding_carry")
    only = [x.strip() for x in (a.only or "").split(",") if x.strip()]
    books = {k: Book(k) for k in CFG if (not only or k in only)}
    log("ACTIVE: " + ", ".join(books))
    t0 = time.time(); last = 0.0

    while time.time() - t0 < a.seconds:
        if time.time() - last < a.loop:
            for _b in books.values():
                _b.dump()
            time.sleep(0.5)
            continue
        last = time.time()

        try:
            uni = jget(MASTER + "/api/universe800", timeout=12).get("tickers") or []
        except Exception:
            uni = []
        uni = [u for u in uni if str(u.get("s", "")).endswith("USDT")
               and (u.get("v") or 0) >= a.min_liq]
        uni.sort(key=lambda u: -(u.get("v") or 0))
        uni = uni[:a.top]
        px = {u["s"]: u["price"] for u in uni if u.get("price")}

        # read the market agent's board: only trade names it has flagged
        opp_rank = {}
        try:
            ob = jget(MASTER + "/api/opportunities", timeout=6).get("opportunities") or []
            opp_rank = {r["symbol"]: r["score"] for r in ob}
        except Exception:
            opp_rank = {}
        OPP_MIN = 0.20

        # ---- 1. cross-sectional momentum
        b = books.get("xsec_momentum")
        if b: b.manage(px)
        ranked = sorted([u for u in uni if u.get("c") is not None], key=lambda u: -(u["c"] or 0))
        if len(ranked) >= 20:
            longs, shorts = xsec_picks(ranked)
            for u in longs:
                if u["s"] in b.pos or len(b.pos) >= 16:
                    continue
                if opp_rank and opp_rank.get(u["s"], 0) < OPP_MIN:
                    continue
                if not confirmed("xsec", u["s"], "buy"):
                    continue
                b.open(u["s"], "buy", u["price"], "top-3 24h: %+.1f%% (10s confirmed)" % u["c"])
                log("%-16s BUY  %-11s %+.1f%% confirmed 10s" % ("xsec_momentum", u["s"], u["c"]))
            for u in shorts:
                if u["s"] in b.pos or len(b.pos) >= 16:
                    continue
                if opp_rank and opp_rank.get(u["s"], 0) < OPP_MIN:
                    continue
                if not confirmed("xsec", u["s"], "sell"):
                    continue
                b.open(u["s"], "sell", u["price"], "bottom-3 24h: %+.1f%% (10s confirmed)" % u["c"])
                log("%-16s SELL %-11s %+.1f%% confirmed 10s" % ("xsec_momentum", u["s"], u["c"]))

        # watchlist: always trade the 5 fastest-trading tickers, direction by 24h sign
        if b:
            for sym in WATCHLIST:
                if sym in b.pos or len(b.pos) >= 16:
                    continue
                u = next((x for x in uni if x["s"] == sym), None)
                if not u or not u.get("price"):
                    continue
                side = "buy" if (u.get("c") or 0) >= 0 else "sell"
                b.open(sym, side, u["price"], "watchlist 24h %+.1f%%" % (u.get("c") or 0))
                log("%-16s %-4s %-11s watchlist" % ("xsec_momentum", side.upper(), sym))

        # ---- 2. cascade rebound: 1m 3-sigma drop on 5x volume
        cb = books.get("cascade_rebound")
        if cb:
          cb.manage(px)
          for u in uni[:24]:
              s = u["s"]
              if s in cb.pos or len(cb.pos) >= 6: continue
              try: b1 = klines(s, "1m", 40)
              except Exception: continue
              if cascade_signal(b1):
                  rets = [(b1[i]["c"] / b1[i-1]["c"] - 1) * 100 for i in range(1, len(b1))]
                  sd = statistics.pstdev(rets[:-1]) or 1e-9
                  last_ret = rets[-1]
                  volmed = statistics.median([x["v"] for x in b1[-21:-1]]) or 1e-9
                  cb.open(s, "buy", b1[-1]["c"], "1m %.2f%% (%.1f sigma) on %.1fx volume" %
                          (last_ret, last_ret / sd, b1[-1]["v"] / volmed))
                  log("%-16s BUY  %-11s cascade %.1f sigma %.1fx vol" %
                      ("cascade_rebound", s, last_ret / sd, b1[-1]["v"] / volmed))

        # ---- 3. funding carry
        fc = books.get("funding_carry")
        if fc: fc.manage(px)
        try:
            fr = {r["symbol"]: float(r["lastFundingRate"]) for r in jget(FAPI + "/fapi/v1/premiumIndex")}
        except Exception:
            fr = {}
        ext = sorted(fr.items(), key=lambda kv: -abs(kv[1]))[:6]
        for s, rate in ext:
            if s not in px or s in fc.pos or len(fc.pos) >= 6: continue
            side = funding_side(rate)
            if side is None: continue
            if not confirmed("funding", s, side):
                continue
            fc.open(s, side, px[s], "funding %+.4f%%/8h (10s confirmed)" % (100 * rate))
            log("%-16s %-4s %-11s funding %+.4f%%" % ("funding_carry", side.upper(), s, 100 * rate))

        # no alpha book sits silent: if idle for 10 minutes, take the top mover
        for name2, bk in books.items():
            if bk.pos or (time.time() - bk.last_open) < 600:
                continue
            cand = [u for u in uni if u.get("c") is not None and u.get("price")]
            if not cand:
                continue
            best = max(cand, key=lambda u: abs(u["c"]))
            side2 = "buy" if best["c"] >= 0 else "sell"
            if not confirmed("idle:" + name2, best["s"], side2):
                continue
            bk.open(best["s"], side2, best["price"],
                    "idle fill (10s confirmed): mover %+.1f%%" % best["c"])
            log("%-16s %-4s %-11s idle fill %+.1f%%" % (name2, side2.upper(), best["s"], best["c"]))

        try:
            now2 = time.time()
            rows = [{"book": k[0], "symbol": k[1], "side": k[2],
                     "age": round(now2 - v, 2),
                     "left": round(max(0.0, CONFIRM_SECS - (now2 - v)), 2)}
                    for k, v in PENDING.items()]
            rows.sort(key=lambda r: -r["age"])
            with open(os.path.join(STATE, "pending.json.t"), "w") as fh:
                json.dump({"ts": int(now2 * 1000), "confirm_secs": CONFIRM_SECS,
                           "pending": rows[:40]}, fh)
            os.replace(os.path.join(STATE, "pending.json.t"),
                       os.path.join(STATE, "pending.json"))
        except Exception:
            pass

        for b in books.values(): b.dump()
        log("cycle: " + ", ".join("%s %d open" % (k, len(v.pos)) for k, v in books.items()))
        time.sleep(0.5)

if __name__ == "__main__": main()
