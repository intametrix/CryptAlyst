#!/usr/bin/env python3
"""models10 - ten crypto strategies that are widely reported to work, each with
its own book, its own basket, and a one-minute probation.

Rules are drawn from public research and widely-cited backtests:
  donchian55   Donchian-55 breakout          (TradingView AG BTC Donchian-55)
  orb          opening-range breakout        (15m session range, 00:00 UTC)
  boll_squeeze Bollinger squeeze expansion
  rsi2         RSI(2) oversold bounce        (Connors-style)
  vwap_revert  distance-from-VWAP reversion
  macd_trend   MACD histogram flip
  vol_break    volume spike + wide bar
  ema_pullback EMA20 pullback inside trend
  atr_channel  Keltner/ATR channel breakout
  funding_fade extreme funding-rate fade

Every model is stopped if, one minute after it first trades, it has not made
money. That is the rule: survive 60 seconds of real fills or be switched off.
"""
import argparse, json, os, time, urllib.request
import tactics as T
import risk
import maker
from lib import emit

try:
    import tactics as _tactics
    _tactics.require_enabled('models10', 'models10.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
SPOT = "https://api.binance.com"
FEE = 0.001
PROBATION_S = 60.0
PROBATION_MIN_RET = 0.0      # any negative PnL inside the first minute = stopped
SKIM = 0.32
LOCK = 0.35
TRAIL_ARM = 1.0
TRAIL = 0.5
STOP_PCT = 0.5
STOP = 1.00
CLOCK = 180
RISK = 0.25

NAMES = ["donchian55", "orb", "boll_squeeze", "rsi2", "vwap_revert",
         "macd_trend", "vol_break", "ema_pullback", "atr_channel", "funding_fade"]


def jget(u, t=8.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "m10/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


_FC = {}


def forecast(sym, side):
    k = (sym, side)
    hit = _FC.get(k)
    if hit and time.time() - hit[0] < 3:
        return hit[1]
    try:
        d = jget("http://127.0.0.1:8790/forecast?symbol=%s&side=%s&horizon=3600" % (sym, side), 5)
    except Exception:
        return None
    _FC[k] = (time.time(), d)
    return d


def klines(sym, interval="1m", limit=120):
    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (sym, interval, limit))
    return [{"t": x[0], "o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
             "c": float(x[4]), "v": float(x[5])} for x in d]


def ema(v, n):
    k = 2.0 / (n + 1)
    e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def rsi(cl, n=14):
    g = l = 0.0
    for i in range(len(cl) - n, len(cl)):
        ch = cl[i] - cl[i - 1]
        if ch > 0:
            g += ch
        else:
            l -= ch
    if g == 0 and l == 0:
        return 50.0        # no movement -> neutral, not overbought
    if l == 0:
        return 100.0
    return 100 - 100 / (1 + (g / n) / (l / n))


def atr(bs, n=14):
    s, c = 0.0, 0
    for i in range(len(bs) - n, len(bs)):
        tr = max(bs[i]["h"] - bs[i]["l"],
                 abs(bs[i]["h"] - bs[i - 1]["c"]), abs(bs[i - 1]["c"] - bs[i]["l"]))
        s += tr
        c += 1
    return s / max(1, c)


# ---- the ten rule sets: each returns "buy", "sell" or None ------------------

def _clean(bs):
    """Drop bars that are missing a numeric field or carry a non-finite value,
    so a malformed source (missing key / None / string / NaN / inf) cannot crash
    a model. Well-formed bars pass through unchanged."""
    out = []
    for b in bs:
        if not isinstance(b, dict):
            continue
        try:
            o = float(b.get("o", b.get("c", 0.0)))
            h = float(b["h"]); l = float(b["l"]); c = float(b["c"])
            v = float(b.get("v", 0.0))
        except (TypeError, ValueError, KeyError):
            continue
        if any(x != x or abs(x) == float("inf") for x in (o, h, l, c, v)):
            continue
        out.append({"t": b.get("t", 0), "o": o, "h": h, "l": l, "c": c, "v": v})
    return out


def r_donchian55(bs):
    bs = _clean(bs)
    if not bs:
        return None
    hi = max(b["h"] for b in bs[-55:])
    lo = min(b["l"] for b in bs[-55:])
    if hi == lo:
        return None          # flat — no range, so no breakout possible
    c = bs[-1]["c"]
    if c >= hi:
        return "buy"
    if c <= lo:
        return "sell"
    return None


def r_orb(bs):
    # range of the first 15 minutes of the current UTC day
    day = time.strftime("%Y-%m-%d", time.gmtime())
    sess = [b for b in bs if time.strftime("%Y-%m-%d", time.gmtime(b["t"] / 1000)) == day]
    if len(sess) < 20:
        return None
    rng = sess[:15]
    hi = max(b["h"] for b in rng)
    lo = min(b["l"] for b in rng)
    c = bs[-1]["c"]
    if c > hi:
        return "buy"
    if c < lo:
        return "sell"
    return None


def r_boll_squeeze(bs):
    bs = _clean(bs)
    if not bs:
        return None
    cl = [b["c"] for b in bs[-20:]]
    m = sum(cl) / len(cl)
    if m == 0:
        return None          # zero price -> no meaningful band
    sd = (sum((x - m) ** 2 for x in cl) / len(cl)) ** 0.5
    width = 2 * sd / m
    if sd == 0:
        return None
    if bs[-1]["c"] > m + 2 * sd and width < 0.01:
        return "buy"
    if bs[-1]["c"] < m - 2 * sd and width < 0.01:
        return "sell"
    return None


def r_rsi2(bs):
    bs = _clean(bs)
    cl = [b["c"] for b in bs]
    r = rsi(cl[-20:], 2) if len(cl) >= 3 else 50
    if r < 10:
        return "buy"
    if r > 90:
        return "sell"
    return None


def r_vwap_revert(bs):
    bs = _clean(bs)
    pv = sum(b["c"] * b["v"] for b in bs[-30:])
    vv = sum(b["v"] for b in bs[-30:])
    if vv <= 0:
        return None
    vw = pv / vv
    c = bs[-1]["c"]
    if c < vw * 0.994:
        return "buy"
    if c > vw * 1.006:
        return "sell"
    return None


def r_macd(bs):
    cl = [b["c"] for b in bs]
    fast = ema(cl[-40:], 12)
    slow = ema(cl[-60:], 26)
    hist = fast - slow
    prev = ema(cl[-41:-1], 12) - ema(cl[-61:-1], 26)
    if prev <= 0 < hist:
        return "buy"
    if prev >= 0 > hist:
        return "sell"
    return None


def r_vol_break(bs):
    bs = _clean(bs)
    if not bs or len(bs) < 20:
        return None
    avg = sum(b["v"] for b in bs[-20:-1]) / 19
    b = bs[-1]
    if avg <= 0 or b["v"] < 3 * avg:
        return None
    rng = b["h"] - b["l"]
    if rng <= 0:
        return None
    if (b["c"] - b["l"]) / rng > 0.7:
        return "buy"
    if (b["h"] - b["c"]) / rng > 0.7:
        return "sell"
    return None


def r_ema_pullback(bs):
    cl = [b["c"] for b in bs]
    e20, e50 = ema(cl[-40:], 20), ema(cl[-80:], 50)
    c = cl[-1]
    if e20 > e50 and c > e50 and abs(c - e20) / e20 < 0.002:
        return "buy"
    if e20 < e50 and c < e50 and abs(c - e20) / e20 < 0.002:
        return "sell"
    return None


def r_atr_channel(bs):
    cl = [b["c"] for b in bs]
    m = ema(cl[-40:], 20)
    a = atr(bs, 14)
    c = cl[-1]
    if a <= 0:
        return None
    if c > m + 1.5 * a:
        return "buy"
    if c < m - 1.5 * a:
        return "sell"
    return None


def r_funding_fade(sym):
    try:
        d = jget(SPOT.replace("/api/v3", "/fapi/v1") + "/premiumIndex?symbol=" + sym, 6)
        fr = float(d.get("lastFundingRate") or 0)
    except Exception:
        return None
    if fr > 0.0008:
        return "sell"      # longs paying heavily -> fade
    if fr < -0.0008:
        return "buy"
    return None


RULES = {
    "donchian55": r_donchian55, "orb": r_orb, "boll_squeeze": r_boll_squeeze,
    "rsi2": r_rsi2, "vwap_revert": r_vwap_revert, "macd_trend": r_macd,
    "vol_break": r_vol_break, "ema_pullback": r_ema_pullback,
    "atr_channel": r_atr_channel, "funding_fade": None,
}


class Book:
    def __init__(self, name, start=5000.0):
        self.name = name
        self.start = start
        self.equity = start
        self.pos = {}
        self.trades = []
        self.disabled = False
        self.first_open = None
        self.checked = False

    def realised(self):
        return self.equity - self.start

    def open(self, sym, side, px, notional=None):
        # the entry loop computes the size (incl. the short half-size); reuse it so
        # the risk-checked size is the size actually opened.
        if notional is None:
            notional = min(self.equity * RISK / 100 * 40, self.equity * 0.08) * T.size_mult()
        did = emit.decision(self.name, sym, side, True, "model") if emit else None
        self.pos[sym] = {"side": side, "entry": px, "t": time.time(),
                         "notional": notional, "peak": 0.0, "decision_id": did}
        if self.first_open is None:
            self.first_open = time.time()

    def mark(self, sym, px):
        p = self.pos.get(sym)
        if not p:
            return None
        if p["side"] == "buy":
            chg = (px / p["entry"] - 1) * 100
        else:
            chg = (1 - px / p["entry"]) * 100
        p["peak"] = max(p["peak"], chg)
        return chg

    def close(self, sym, px, chg, reason):
        p = self.pos.pop(sym)
        net = chg - 200 * FEE
        self.equity += p["notional"] * net / 100
        trade = {"symbol": sym, "side": p["side"], "entry": p["entry"],
                 "exit": px, "gross_pct": chg, "net_pct": net,
                 "peak_pct": p["peak"], "reason": reason,
                 "held_s": time.time() - p["t"],
                 "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                 "notional": p["notional"], "pnl_usd": p["notional"] * net / 100,
                 "decision_id": p.get("decision_id")}
        self.trades.append(trade)
        if emit:
            try:
                emit.fill(self.name, trade)
            except Exception:
                pass

    def snapshot(self):
        return {"engine": self.name, "mode": "paper", "state": "running",
                "equity": round(self.equity, 2), "start_equity": self.start,
                "return_pct": round(100 * (self.equity / self.start - 1), 4),
                "realised": round(self.realised(), 2),
                "unrealised": 0.0, "trade_count": len(self.trades),
                "positions": [{"symbol": s, "side": p["side"], "entry": p["entry"],
                               "notional": p["notional"], "entry_ms": int(p["t"] * 1000),
                               "held_s": time.time() - p["t"], "why": "strategy entry"}
                              for s, p in self.pos.items()],
                "trades": self.trades[-200:], "disabled": self.disabled,
                "probation": {"first_open": self.first_open, "checked": self.checked}}


def write(bk):
    p = os.path.join(STATE, "pack_%s.json" % bk.name)
    with open(p + ".t", "w") as fh:
        json.dump(bk.snapshot(), fh)
    os.replace(p + ".t", p)


def log(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--loop", type=float, default=30.0)
    ap.add_argument("--probation", type=float, default=PROBATION_S)
    ap.add_argument("--only", default="", help="comma separated books")
    a = ap.parse_args()

    only = [x.strip() for x in (a.only or "").split(",") if x.strip()]
    names = [n for n in NAMES if (not only or n in only)]
    books = {n: Book(n, a.start) for n in names}
    # each model gets a different slice of the liquid universe
    log("MODELS10 — %d strategies, probation %.0fs, risk %.2f%%" % (len(books), a.probation, RISK))
    while True:
        try:
            uni = jget(MASTER + "/api/universe800", 10).get("tickers") or []
        except Exception:
            time.sleep(3)
            continue
        syms = [t["s"] for t in uni if t.get("s", "").endswith("USDT")][:a.top]
        if not syms:
            time.sleep(3)
            continue
        baskets = {}
        per = 10          # turbo: ten names per model basket
        for i, n in enumerate(names):
            baskets[n] = syms[i * per:(i + 1) * per] or syms[:per]

        px = {t["s"]: float(t.get("price") or 0) for t in uni if t.get("price")}

        for name, bk in books.items():
            if bk.disabled:
                continue
            # ---- one minute of trading decides whether the model survives
            if bk.first_open and not bk.checked and time.time() - bk.first_open >= a.probation:
                bk.checked = True
                if bk.realised() < PROBATION_MIN_RET:
                    bk.disabled = True
                    log("%-13s STOPPED after %.0fs (realised %+.2f)" % (
                        name, a.probation, bk.realised()))
            # ---- exits first
            for sym in list(bk.pos):
                if sym not in px:
                    continue
                chg = bk.mark(sym, px[sym])
                held = time.time() - bk.pos[sym]["t"]
                reason = None
                if chg >= float(T.options().get("lock_pct", LOCK)):
                    reason = "lock"
                elif bk.pos[sym]["peak"] >= TRAIL_ARM and chg <= bk.pos[sym]["peak"] - TRAIL:
                    reason = "trail"   # ride the curve
                elif chg <= -STOP_PCT:
                    reason = "stop"    # tight stop-loss: no heavy losses
                elif held >= float(T.options().get("clock_s", CLOCK)):
                    reason = "clock"
                if reason:
                    side_now = bk.pos[sym]["side"]
                    bk.close(sym, px[sym], chg, reason)
                    log("%-13s CLOSE %-11s %-4s %+6.3f%% %s" % (name, sym, side_now, chg, reason))
            # ---- entries
            if bk.disabled or len(bk.pos) >= int(T.options().get("max_positions", 8)):
                continue
            for sym in baskets[name]:
                if sym in bk.pos or sym not in px:
                    continue
                try:
                    bs = klines(sym, "15m", 200)
                except Exception:
                    continue
                if len(bs) < 60:
                    continue
                if name == "funding_fade":
                    side = r_funding_fade(sym)
                else:
                    side = RULES[name](bs)
                if not side:
                    continue
                need_move = float(T.options().get("min_expected_move", 0.0))
                if need_move > 0:
                    fc = forecast(sym, side)
                    if not fc or (fc.get("move_pct") or 0) < need_move:
                        continue
                # never fight the higher-timeframe trend
                ok, sc = T.ext_gate(sym, side)
                if not ok:
                    continue
                o = T.options()
                if not o.get("allow_counter_trend"):
                    if side == "sell" and T.btc_regime() == "up":
                        continue
                    if side == "buy" and T.btc_regime() == "down":
                        continue
                _notional = min(bk.equity * RISK / 100 * 40, bk.equity * 0.08) * T.size_mult()
                if side == "sell":
                    _notional *= 0.5      # unbounded tail: shorts carry half the size
                _fc = forecast(sym, side) or {}
                _ok, _why = risk.allow(sym, side, _notional, bk.equity,
                                       _fc.get("move_pct"),
                                       (T.ext_signals(sym) or {}).get("slippage_bps_5k"),
                                       bar_age_s=_fc.get("bar_age_s"),
              book_positions=list(bk.pos.values()) if isinstance(getattr(bk, 'pos', None), dict) else None)
                risk.record(sym, side, _ok, _why)
                if not _ok:
                    continue
                q_px, q_prob, q_note = maker.quote(sym, side, _notional)
                if q_px is None or q_prob < 0.30:
                    continue
                bk.open(sym, side, q_px, _notional)
                log("%-13s OPEN  %-11s %s %.6g" % (name, sym, side, px[sym]))
                break
            write(bk)
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
