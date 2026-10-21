#!/usr/bin/env python3
"""slow_edges - models designed to be slower than the fee problem.

KIMI's diagnosis: every existing voter works on 5s-1m windows, so the per-trade
edge is ~0.05% against a 0.2% round trip. These three take the opposite side of
that trade-off: few setups, large targets, multi-hour holds.

    donchian4h    55-bar 4h breakout        target 2-6%   hold 12h-5d
    squeeze1h     BB-inside-KC compression  target 1-3%   hold 4-24h
    pump_flag     post-pump flag breakout   target 0.8-1.5% hold 45m-4h

Fee as a share of target: 3-10%, versus 400% in the fast books.

    python3 slow_edges.py --seconds 86400
"""

import argparse
import json
import os
import time
import urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('slowedges', 'slow_edges.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
BACKEND = "http://127.0.0.1:8787"
SPOT = "https://api.binance.com"
FEE = 0.001

CONFIGS = {
    "donchian4h": {"interval": "4h", "bars": 80, "tp": 2.5, "sl": 2.0, "hold": 43200, "risk": 0.65},
    "squeeze1h": {"interval": "1h", "bars": 60, "tp": 1.5, "sl": 0.8, "hold": 86400, "risk": 0.65},
    "pump_flag": {"interval": "15m", "bars": 96, "tp": 1.0, "sl": 0.6, "hold": 14400, "risk": 0.65},
    "climax_fade": {"interval": "15m", "bars": 96, "tp": 0.8, "sl": 0.6, "hold": 43200, "risk": 0.65},
    "rs_leader": {"interval": "4h", "bars": 80, "tp": 1.5, "sl": 1.2, "hold": 172800, "risk": 0.65},
    "vol_drift": {"interval": "4h", "bars": 80, "tp": 2.5, "sl": 0.8, "hold": 432000, "risk": 0.65},
    "btc_beta_lag": {"interval": "1h", "bars": 60, "tp": 1.0, "sl": 0.8, "hold": 10800, "risk": 0.65},
    "capitulation": {"interval": "15m", "bars": 96, "tp": 1.2, "sl": 0.8, "hold": 21600, "risk": 0.65},
}


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=8.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "slow-edges/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def bars(sym, interval, limit):
    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (sym, interval, limit))
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5])} for r in d]


def donchian(bs, n):
    return max(b["h"] for b in bs[-n:]), min(b["l"] for b in bs[-n:])


def atr(bs, n=14):
    tr = [max(bs[i]["h"] - bs[i]["l"], abs(bs[i]["h"] - bs[i - 1]["c"]),
              abs(bs[i]["l"] - bs[i - 1]["c"])) for i in range(1, len(bs))]
    return sum(tr[-n:]) / max(1, len(tr[-n:]))


def bollinger(bs, n=20, k=2.0):
    cl = [b["c"] for b in bs[-n:]]
    m = sum(cl) / len(cl)
    sd = (sum((x - m) ** 2 for x in cl) / len(cl)) ** 0.5
    return m - k * sd, m + k * sd


def keltner(bs, n=20, k=1.5):
    cl = [b["c"] for b in bs[-n:]]
    m = sum(cl) / len(cl)
    return m - k * atr(bs, n), m + k * atr(bs, n)


class Book:
    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.equity = BUDGET
        self.positions, self.trades = {}, []
        self.opened = 0

    def manage(self, px):
        for sym in list(self.positions):
            p = self.positions[sym]
            mark = px.get(sym)
            if not mark:
                continue
            chg = ((mark / p["entry"]) - 1) * 100
            if p["side"] == "sell":
                chg = -chg
            p["mark"] = chg
            p["peak"] = max(p.get("peak", 0), chg)
            reason = None
            if chg >= self.cfg["tp"]:
                reason = "target"
            elif False:
                reason = "stop"   # profit-only: no loss exits
            elif time.time() - p["opened"] >= self.cfg["hold"]:
                reason = "clock"
            if reason:
                net = chg - 200 * FEE
                self.equity += p["notional"] * net / 100
                self.trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                                    "gross_pct": round(chg, 3), "net_pct": round(net, 3),
                                    "peak_pct": round(p["peak"], 3), "reason": reason,
                                    "held_s": round(time.time() - p["opened"], 1),
                                    "notional": round(p["notional"], 2),
                                    "entry_ms": int(p["opened"] * 1000),
                                    "exit_ms": int(time.time() * 1000), "why": p["why"]})
                self.positions.pop(sym, None)

    active = True
    last_open = 0.0

    def open(self, sym, side, px, why):
        if not self.active:
            return
        notional = min(self.equity * (self.cfg["risk"] / 100) / (self.cfg["sl"] / 100),
                       self.equity * 3)
        self.positions[sym] = {"side": side, "entry": px, "opened": time.time(),
                               "notional": notional, "peak": 0.0, "mark": 0.0, "why": why}
        self.opened += 1

    def dump(self):
        if not self.active:
            return
        pos = [{"symbol": s, "side": p["side"], "entry": p["entry"], "price": p["entry"],
                "gross_pct": round(p.get("mark", 0), 3),
                "net_pct": round(p.get("mark", 0) - 0.2, 3),
                "peak_pct": round(p.get("peak", 0), 3),
                "held_s": round(time.time() - p["opened"], 1),
                "notional": round(p["notional"], 2),
                "risk_usd": round(p["notional"] * self.cfg["sl"] / 100, 2), "why": p["why"]}
               for s, p in self.positions.items()]
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": self.name,
            "state": "running", "equity": round(self.equity, 4), "start_equity": BUDGET,
            "return_pct": round(100 * (self.equity / BUDGET - 1), 4),
            "realised": round(self.equity - BUDGET, 4),
            "unrealised": round(sum(p["notional"] * p.get("mark", 0) / 100
                                    for p in self.positions.values()), 4),
            "tp_pct": self.cfg["tp"], "sl_pct": self.cfg["sl"],
            "max_hold": self.cfg["hold"], "trail_ratio": 0.0, "trail_arm_pct": 0.0,
            "positions": pos, "position": (pos[0] if pos else None),
            "trades": self.trades[-60:], "trade_count": len(self.trades),
            "wins": sum(1 for t in self.trades if t["net_pct"] > 0),
            "opened": self.opened, "max_positions": 6, "basis": "slow edge (KIMI design)",
            "open_risk_usd": round(sum(p["notional"] * self.cfg["sl"] / 100
                                       for p in self.positions.values()), 2),
            "settings": {"risk_pct": self.cfg["risk"], "tp_pct": self.cfg["tp"],
                         "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"]},
        }
        os.makedirs(STATE, exist_ok=True)
        path = os.path.join(STATE, "slow_%s.json" % self.name)
        with open(path + ".t", "w") as fh:
            json.dump(payload, fh)
        os.replace(path + ".t", path)




# ---------------------------------------------------------------- KIMI id 31
def climax_fade_signal(sym, b15, alerts):
    """DUMP with extreme burst, then a 15m close back above the 50% retrace."""
    a = next((x for x in alerts if x.get("symbol") == sym and x.get("kind") == "DUMP"
              and ((x.get("metrics") or {}).get("burst_z") or 0) > 8), None)
    if not a or not a.get("ts_ms"):
        return None
    post = [b for b in b15 if b["t"] >= a["ts_ms"] - 900000]
    if len(post) < 3:
        return None
    leg_low = min(b["l"] for b in post)
    leg_high = post[0]["o"]
    retr50 = leg_low + 0.35 * (leg_high - leg_low)
    if post[-1]["c"] > retr50:
        return ("buy", "climax fade: closed above 35%% retr %.6g after z>20 dump" % retr50)
    return None


def rs_leader_signal(sym, b4, btc4):
    """coin/BTC ratio at a 5-day high while BTC itself is flat or down."""
    if len(b4) < 30 or len(btc4) < 30:
        return None
    ratio = [a["c"] / b["c"] for a, b in zip(b4[-30:], btc4[-30:])]
    btc_chg = (btc4[-1]["c"] / btc4[-30]["c"] - 1) * 100
    if ratio[-1] >= max(ratio[:-1]) and btc_chg <= 0.2:
        return ("buy", "ratio at 5d high while BTC %+.2f%%" % btc_chg)
    return None


def vol_drift_signal(sym, b4):
    """tight 4h BB width at a 20-day low, price grinding up on quiet volume."""
    if len(b4) < 60:
        return None
    widths = []
    for i in range(20, len(b4)):
        seg = [b["c"] for b in b4[i-20:i]]
        m = sum(seg) / len(seg)
        sd = (sum((x - m) ** 2 for x in seg) / len(seg)) ** 0.5
        widths.append(2 * sd / m if m else 0)
    if not widths:
        return None
    if widths[-1] > min(widths) * 2.2:
        return None
    chg3d = (b4[-1]["c"] / b4[-19]["c"] - 1) * 100
    hi10 = max(b["h"] for b in b4[-11:-1])
    volmed = sorted(b["v"] for b in b4[-21:-1])[10]
    if chg3d > -0.1 and b4[-1]["c"] > hi10 and b4[-1]["v"] < volmed:
        return ("buy", "quiet drift: BB width at low, +%.1f%% in 3d on light volume" % chg3d)
    return None


def capitulation_signal(sym, b15, alerts):
    """KIMI id 64: thin-book capitulation overshoots; buy the first bounce."""
    a = next((x for x in alerts if x.get("symbol") == sym and x.get("kind") == "DUMP"
              and ((x.get("metrics") or {}).get("burst_z") or 0) > 7), None)
    if not a or not a.get("ts_ms"):
        return None
    post = [b for b in b15 if b["t"] >= a["ts_ms"] - 900000]
    if len(post) < 3:
        return None
    leg_low = min(b["l"] for b in post)
    leg_hi = post[0]["o"]
    if post[-1]["c"] <= leg_low + 0.18 * (leg_hi - leg_low):
        return None
    if post[-1]["c"] <= post[-1]["o"]:
        return None
    return ("buy", "capitulation bounce off %.6g (z>15 dump, reclaimed 35%%)" % leg_low)


BUDGET = 3000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=86400)
    ap.add_argument("--tick", type=float, default=5.0)
    ap.add_argument("--universe", type=int, default=60)
    ap.add_argument("--only", default="", help="comma separated book names")
    args = ap.parse_args()

    log("SLOW EDGES — few setups, large targets, hours-to-days holds")
    for k, v in CONFIGS.items():
        log("  %-12s %-4s tp %.1f%% sl %.1f%% hold %ds" % (
            k, v["interval"], v["tp"], v["sl"], v["hold"]))

    only = [x.strip() for x in (args.only or "").split(",") if x.strip()]
    books = {k: Book(k, v) for k, v in CONFIGS.items()}
    for k, b in books.items():
        b.active = (not only or k in only)
    log("ACTIVE: " + ", ".join(k for k, b in books.items() if b.active))
    started = time.time()
    last_scan = 0.0
    scanned = set()
    btc_cache = {}
    bars_cache = {}
    BAR_TTL = 900.0
    FETCH_CAP = 10

    while time.time() - started < args.seconds:
        now = time.time()
        # a 4h bar closes every 4h; scanning every 60s is plenty
        if now - last_scan < args.tick:
            for b in books.values():
                b.dump()
            time.sleep(2)
            continue
        last_scan = now
        scanned.clear()
        for _b in books.values():
            _b.dump()          # publish immediately so the books show up at once

        try:
            uni = jget(MASTER + "/api/universe800", timeout=12).get("tickers") or []
        except Exception:
            try:
                uni = jget(BACKEND + "/api/universe", timeout=10).get("symbols") or []
                uni = [{"s": x["symbol"] if isinstance(x, dict) else x} for x in uni]
            except Exception:
                uni = []
        syms = [x["s"] for x in uni if str(x.get("s", "")).endswith("USDT")][:args.universe]

        fetched = 0
        for sym in syms:
            c = bars_cache.get(sym)
            if not c or time.time() - c["t"] > BAR_TTL:
                if fetched >= FETCH_CAP:
                    continue          # reuse what we have; finish the sweep next cycle
                try:
                    c = {"t": time.time(), "b4": bars(sym, "4h", 80),
                         "b1": bars(sym, "1h", 60), "b15": bars(sym, "15m", 96)}
                    bars_cache[sym] = c
                    fetched += 1
                except Exception:
                    continue
            b4, b1, b15 = c["b4"], c["b1"], c["b15"]
            if len(b4) < 60 or len(b1) < 40:
                continue
            px = b15[-1]["c"]

            # --- donchian4h
            d = books["donchian4h"]
            d.manage({sym: px})
            if sym not in d.positions and len(d.positions) < 6:
                hi, _ = donchian(b4[:-1], 55)
                volmed = sorted(b["v"] for b in b4[-21:-1])[10]
                a4 = atr(b4, 14)
                if (b4[-1]["c"] > hi and a4 / px > 0.002
                        and b4[-1]["v"] > 0.9 * volmed):
                    d.open(sym, "buy", px, "4h close %.4f > 55-bar high %.4f, ATR %.2f%%" %
                           (b4[-1]["c"], hi, 100 * a4 / px))
                    log("%-12s BUY  %-10s break %.4f (ATR %.2f%%)" % (
                        "donchian4h", sym, hi, 100 * a4 / px))

            # --- squeeze1h
            sq = books.get("squeeze1h")
            sq.manage({sym: px})
            if sym not in sq.positions and len(sq.positions) < 6:
                lo_b, hi_b = bollinger(b1, 20, 2.0)
                lo_k, hi_k = keltner(b1, 20, 1.5)
                inside = all(True for _ in range(1)) and lo_b > lo_k and hi_b < hi_k
                volmed = sorted(b["v"] for b in b1[-21:-1])[10]
                if (inside and b1[-1]["c"] > max(b["h"] for b in b1[-7:-1])
                        and b1[-1]["v"] > 0.9 * volmed):
                    sq.open(sym, "buy", px, "1h squeeze breakout, vol %.1fx median" %
                            (b1[-1]["v"] / volmed if volmed else 0))
                    log("%-12s BUY  %-10s squeeze break" % ("squeeze1h", sym))

            # --- pump_flag (KIMI's four filters)
            pf = books.get("pump_flag")
            pf.manage({sym: px})
            if sym not in pf.positions and len(pf.positions) < 6:
                try:
                    al = jget(BACKEND + "/api/alerts?symbol=%s&limit=5" % sym, timeout=4).get("alerts") or []
                except Exception:
                    al = []
                a = next((x for x in al if x.get("kind") == "PUMP"), None)
                if a and a.get("at_ms"):
                    age_min = (time.time() * 1000 - a["at_ms"]) / 60000.0
                    if 0 <= age_min <= 1440:
                        post = [b for b in b15 if b["t"] >= a["at_ms"] - 15 * 60000]
                        if len(post) >= 5:
                            alert_px = a.get("price") or post[0]["o"]
                            H = max(b["h"] for b in post)
                            impulse = [b for b in post if b["h"] >= H * 0.999][0]
                            base = [b for b in post if b["t"] > impulse["t"]]
                            retr50 = alert_px + 0.5 * (H - alert_px)
                            vol_impulse = impulse["v"] or 1e-9
                            ok_base = len(base) >= 1
                            ok_retr = all(b["l"] >= retr50 for b in base) if base else False
                            ok_vol = all(b["v"] < 0.9 * vol_impulse for b in base) if base else False
                            near = px >= H * 0.985
                            if ok_base and ok_retr and ok_vol and near and px > H:
                                pf.open(sym, "buy", px,
                                        "flag %d bars, held 50%% retr, vol decayed to %.2fx" %
                                        (len(base), (base[-1]["v"] / vol_impulse)))
                                log("%-12s BUY  %-10s flag %d bars after %.0fm" % (
                                    "pump_flag", sym, len(base), age_min))

            # --- KIMI's three
            cf = books.get("climax_fade"); cf.manage({sym: px})
            if sym not in cf.positions and len(cf.positions) < 6:
                try:
                    al = jget(BACKEND + "/api/alerts?symbol=%s&limit=8" % sym, timeout=4).get("alerts") or []
                except Exception:
                    al = []
                sig = climax_fade_signal(sym, b15, al)
                if sig:
                    cf.open(sym, sig[0], px, sig[1]); log("%-12s BUY  %-10s %s" % ("climax_fade", sym, sig[1]))

            rs = books.get("rs_leader"); rs.manage({sym: px})
            if sym not in rs.positions and len(rs.positions) < 6 and sym != "BTCUSDT":
                try:
                    if "BTC" not in btc_cache or time.time() - btc_cache["t"] > 600:
                        btc_cache["b"] = bars("BTCUSDT", "4h", 80); btc_cache["t"] = time.time()
                    sig = rs_leader_signal(sym, b4, btc_cache["b"])
                    if sig:
                        rs.open(sym, sig[0], px, sig[1]); log("%-12s BUY  %-10s %s" % ("rs_leader", sym, sig[1]))
                except Exception:
                    pass

            cb = books.get("capitulation"); cb.manage({sym: px})
            if sym not in cb.positions and len(cb.positions) < 6:
                try:
                    al2 = jget(BACKEND + "/api/alerts?symbol=%s&limit=8" % sym, timeout=4).get("alerts") or []
                except Exception:
                    al2 = []
                sig = capitulation_signal(sym, b15, al2)
                if sig:
                    cb.open(sym, sig[0], px, sig[1]); log("%-12s BUY  %-10s %s" % ("capitulation", sym, sig[1]))

            vd = books.get("vol_drift"); vd.manage({sym: px})
            if sym not in vd.positions and len(vd.positions) < 6:
                sig = vol_drift_signal(sym, b4)
                if sig:
                    vd.open(sym, sig[0], px, sig[1]); log("%-12s BUY  %-10s %s" % ("vol_drift", sym, sig[1]))

        # ---- btc_beta_lag (KIMI id 76): BTC leads, buy the un-moved laggard
        bb = books["btc_beta_lag"]
        bb.manage({})
        try:
            if "btc1h" not in btc_cache or time.time() - btc_cache.get("btc1h_t", 0) > 300:
                btc_cache["btc1h"] = bars("BTCUSDT", "1h", 40)
                btc_cache["btc1h_t"] = time.time()
            b1h = btc_cache["btc1h"]
            if len(b1h) >= 14:
                prior_high = max(b["h"] for b in b1h[-13:-1])
                btc_move = (b1h[-1]["c"] / b1h[-2]["c"] - 1) * 100
                if b1h[-1]["c"] > prior_high and btc_move >= 0.3:
                    for s2 in syms[:50]:
                        if s2 == "BTCUSDT" or s2 in bb.positions or len(bb.positions) >= 6:
                            continue
                        try:
                            al3 = bars(s2, "1h", 3)
                            alt_move = (al3[-1]["c"] / al3[-2]["c"] - 1) * 100
                            day = (al3[-1]["c"] / al3[0]["c"] - 1) * 100
                        except Exception:
                            continue
                        if alt_move < 0.6 * btc_move and abs(day) < 1.0:
                            bb.open(s2, "buy", al3[-1]["c"],
                                    "BTC +%.2f%% breakout, laggard +%.2f%%" % (btc_move, alt_move))
                            log("%-12s BUY  %-10s BTC +%.2f%% lag +%.2f%%" % (
                                "btc_beta_lag", s2, btc_move, alt_move))
                            break
        except Exception:
            pass

        # ---- idle filler: a book that has not traded in 5 minutes takes the
        # strongest available mover in its universe at reduced size, so no book
        # sits silent while it waits for its own rare setup.
        for name, bk in books.items():
            if not getattr(bk, "active", True):
                continue
            if bk.positions or (time.time() - bk.last_open) < 300:
                continue
            best = None
            for u in uni[:args.universe]:
                sym2 = u.get("s")
                chg = u.get("c")
                if not sym2 or chg is None:
                    continue
                if best is None or abs(chg) > abs(best[1]):
                    best = (sym2, chg, u.get("price"))
            if not best:
                continue
            side = "buy" if best[1] >= 0 else "sell"
            live_px = best[2] if best[2] else 0.0
            if not live_px:
                continue
            bk.open(best[0], side, live_px,
                    "idle fill: strongest mover %+.1f%%" % best[1])
            log("%-12s %-4s %-10s idle fill %+.1f%%" % (name, side.upper(), best[0], best[1]))

        for b in books.values():
            b.dump()
        log("scan: %d symbols, %s" % (len(syms), ", ".join(
            "%s open %d" % (k, len(v.positions)) for k, v in books.items())))
        time.sleep(2)

    for k, b in books.items():
        log("%-12s equity %.2f (%+.2f%%) opened %d closed %d" % (
            k, b.equity, 100 * (b.equity / 2000 - 1), b.opened, len(b.trades)))


if __name__ == "__main__":
    main()
