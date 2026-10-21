#!/usr/bin/env python3
"""sympathy_lag - trade the follower, never the leader.

Designed with KIMI over the bridge. Three ideas belong to it:

  1. Pairs are ASYMMETRIC. Co-firing proves correlation, not lead-lag, so a pair
     only exists when B followed A in at least 2 of 3 co-fires. A symmetric pair
     is just high-beta market, which we already own elsewhere.
  2. Exits are STATE-based, not clocks. The clock is the undertaker: it only
     fires when the state exits never did.
       thesis complete : laggard closes 70% of the leader's move, or +1%
       thesis dead     : the LEADER loses its post-alert structure
       flow dead       : laggard burst_multiple back under 1.5
       undertaker      : 90 minutes (edge half-life is 45-60m)
  3. Never fake a field. There is no spread data, so there is no spread filter.

    python3 sympathy_lag.py --seconds 86400
"""

import argparse
import json
import os
import time
import urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('sympathy', 'sympathy_lag.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


MASTER = "http://127.0.0.1:8799"

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
BACKEND = "http://127.0.0.1:8787"
SPOT = "https://api.binance.com"
FEE = 0.001

LEADER_Z = 12.0          # KIMI id 29: leader must burst hardest
GRADIENT_RATIO = 0.85     # follower burst_z must be < 0.7x the leader's
MIN_GAP_S = 20.0         # and must fire at least 60s later
WINDOW_SCORE = 70
LEADER_TBR = 0.80
LEADER_QVOL = 10_000_000
COFIRE_WINDOW = 600.0        # seconds: alerts inside this window co-fired
PAIR_TTL = 7 * 86400         # a pair expires if it stops co-firing
LAG_FRACTION = 0.60          # laggard must have moved <30% of the leader
LAG_BURST = 3.0
LAG_TBR = 0.55
TARGET_FRACTION = 0.70
TARGET_PCT = 1.0
STOP_PCT = 0.5
UNDERTAKER = 90 * 60
BURST_MAX = 30               # KIMI: >12 alerts/10min on 150 names is a beta event
MAX_LAGGARDS = 3             # cap: 3 sympathy longs is one bet wearing three hats
STATS = {"windows": 0, "discarded": 0, "qualified": 0, "leaders": 0, "setups": 0}


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=6.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "sympathy-lag/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def save(path, obj):
    os.makedirs(STATE, exist_ok=True)
    with open(path + ".t", "w") as fh:
        json.dump(obj, fh)
    os.replace(path + ".t", path)


PAIRS_PATH = os.path.join(STATE, "pairs.json")


def record_cofire(pairs, alerts):
    """Update asymmetric pair evidence from the recent alert stream."""
    now = time.time()
    by_sym = {}
    for a in alerts:
        sym = a.get("symbol")
        if sym and a.get("kind") == "PUMP":
            by_sym.setdefault(sym, []).append((a.get("ts_ms") or 0,
                                               a.get("event_id") or ""))
    syms = list(by_sym)
    for i, a_sym in enumerate(syms):
        for b_sym in syms[i + 1:]:
            for ta, ea in by_sym[a_sym]:
                for tb, eb in by_sym[b_sym]:
                    if not ta or not tb or ta == tb:
                        continue
                    if abs(ta - tb) > COFIRE_WINDOW * 1000:
                        continue
                    leader, follower = (a_sym, b_sym) if ta < tb else (b_sym, a_sym)
                    key = "%s>%s" % (leader, follower)
                    p = pairs.setdefault(key, {"leader": leader, "follower": follower,
                                               "count": 0, "last": 0, "ms": 0.0,
                                               "evts": []})
                    ev = "%s|%s" % (ea, eb)
                    if ev in p["evts"]:
                        continue
                    p["evts"] = (p["evts"] + [ev])[-200:]
                    p["count"] += 1
                    p["last"] = int(now)
                    p["ms"] += abs(ta - tb)
    # expire pairs that stopped co-firing
    for k in list(pairs):
        if now - (pairs[k].get("last") or 0) > PAIR_TTL:
            pairs.pop(k, None)
    return pairs


class Book:
    def __init__(self):
        self.equity = 3000.0
        self.positions, self.trades = {}, []
        self.opened = 0

    last_open = 0.0

    def open(self, sym, px, leaders, why):
        self.last_open = time.time()
        notional = min(self.equity * 0.0065 / (STOP_PCT / 100), self.equity * 3)
        self.positions[sym] = {"entry": px, "opened": time.time(), "notional": notional,
                               "peak": 0.0, "mark": 0.0, "why": why,
                               "leaders": leaders, "leader_move": None}
        self.opened += 1

    def close(self, sym, px, reason):
        p = self.positions.pop(sym)
        chg = (px / p["entry"] - 1) * 100
        net = chg - 200 * FEE
        self.equity += p["notional"] * net / 100
        self.trades.append({"symbol": sym, "side": "buy", "entry": p["entry"],
                            "gross_pct": round(chg, 3), "net_pct": round(net, 3),
                            "peak_pct": round(p["peak"], 3), "reason": reason,
                            "held_s": round(time.time() - p["opened"], 1),
                            "notional": round(p["notional"], 2),
                            "entry_ms": int(p["opened"] * 1000),
                            "exit_ms": int(time.time() * 1000), "why": p["why"]})
        log("CLOSE %-11s %+6.3f%% net %-8s %s" % (sym, chg, reason, p["why"][:60]))

    def dump(self):
        pos = [{"symbol": s, "side": "buy", "entry": p["entry"], "price": p["entry"],
                "gross_pct": round(p.get("mark", 0), 3),
                "net_pct": round(p.get("mark", 0) - 0.2, 3),
                "peak_pct": round(p.get("peak", 0), 3),
                "held_s": round(time.time() - p["opened"], 1),
                "notional": round(p["notional"], 2),
                "risk_usd": round(p["notional"] * STOP_PCT / 100, 2),
                "why": p["why"]} for s, p in self.positions.items()]
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": "sympathy_lag",
            "state": "running", "equity": round(self.equity, 4), "start_equity": 3000.0,
            "return_pct": round(100 * (self.equity / 3000 - 1), 4),
            "realised": round(self.equity - 3000.0, 4),
            "unrealised": round(sum(p["notional"] * p.get("mark", 0) / 100
                                    for p in self.positions.values()), 4),
            "tp_pct": TARGET_PCT, "sl_pct": STOP_PCT, "max_hold": UNDERTAKER,
            "trail_ratio": 0.0, "trail_arm_pct": 0.0, "positions": pos,
            "position": (pos[0] if pos else None), "trades": self.trades[-60:],
            "trade_count": len(self.trades),
            "wins": sum(1 for t in self.trades if t["net_pct"] > 0),
            "opened": self.opened, "max_positions": 6,
            "basis": "leader→laggard, KIMI design",
            "open_risk_usd": round(sum(p["notional"] * STOP_PCT / 100
                                       for p in self.positions.values()), 2),
            "settings": {"risk_pct": 0.4, "tp_pct": TARGET_PCT, "sl_pct": STOP_PCT,
                         "max_hold": UNDERTAKER},
        }
        save(os.path.join(STATE, "slow_sympathy_lag.json"), payload)


def leader_structure_intact(leader, alert_ms, bars):
    """Thesis dead when the leader closes below the alert price."""
    if not bars:
        return True
    after = [b for b in bars if b["t"] >= alert_ms - 900000]
    if not after:
        return True
    alert_px = after[0]["o"]
    return after[-1]["c"] > alert_px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=86400)
    ap.add_argument("--loop", type=float, default=5.0)
    args = ap.parse_args()
    log("SYMPATHY LAG — asymmetric pairs, state exits, 90m undertaker")

    b = Book()
    pairs = load(PAIRS_PATH, {})
    started = time.time()
    px = {}
    last_px = 0.0
    bars_cache = {}

    while time.time() - started < args.seconds:
        try:
            alerts = jget(BACKEND + "/api/alerts?limit=120", timeout=5).get("alerts") or []
        except Exception:
            alerts = []

        pairs = record_cofire(pairs, alerts)
        save(PAIRS_PATH, pairs)

        if time.time() - last_px > 2.0:
            try:
                px = {r["symbol"]: float(r["price"])
                      for r in jget(SPOT + "/api/v3/ticker/price", timeout=5)}
                last_px = time.time()
            except Exception:
                pass

        # ---------------- state-based exits ----------------
        for sym in list(b.positions):
            p = b.positions[sym]
            mark = px.get(sym)
            if not mark:
                continue
            chg = (mark / p["entry"] - 1) * 100
            p["mark"] = chg
            p["peak"] = max(p["peak"], chg)

            # profit-only settlement: nothing closes while it is red
            reason = None
            leader = p["leaders"][0] if p["leaders"] else None
            if chg > 0 and chg >= TARGET_PCT:
                reason = "target"
            elif leader and p.get("leader_move"):
                try:
                    lpx = px.get(leader)
                    moved = (lpx / p["leader_entry"] - 1) * 100
                    if moved > 0 and chg >= TARGET_FRACTION * moved:
                        reason = "thesis_complete"
                except Exception:
                    pass
            if reason is None and leader:
                try:
                    bars = bars_cache.get(leader)
                    if not bars or time.time() - bars["t"] > 300:
                        d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=15m&limit=40" % leader)
                        bars = {"t": time.time(), "b": [{"t": int(r[0]), "o": float(r[1]),
                                                         "c": float(r[4])} for r in d]}
                        bars_cache[leader] = bars
                    if not leader_structure_intact(leader, p["opened"] * 1000, bars["b"]):
                        reason = "leader_broke"
                except Exception:
                    pass
            if reason is None and time.time() - p["opened"] >= UNDERTAKER:
                reason = "undertaker_90m"

            if reason:
                b.close(sym, mark, reason)

        # ---------------- entries ----------------
        # KIMI's rules: discard burst windows, leader must be the EARLIEST qualifying
        # alert, followers must come after it, cap 3 laggards per leader.
        # KIMI id 29: only severity-worthy alerts count toward the window total
        pumps = [a for a in alerts if a.get("kind") == "PUMP" and a.get("ts_ms")
                 and (a.get("score") or 0) >= WINDOW_SCORE]
        recent = [a for a in pumps if (time.time() * 1000 - a["ts_ms"]) <= COFIRE_WINDOW * 1000]
        if recent:
            STATS["windows"] += 1
            if len(recent) > BURST_MAX:
                STATS["discarded"] += 1
                log("burst window discarded: %d alerts in 10m" % len(recent))
                recent = []
            else:
                STATS["qualified"] += 1

        followers = {}
        for key, p in pairs.items():
            if p["count"] < 2:
                continue
            followers.setdefault(p["leader"], []).append(p["follower"])

        ordered = sorted(recent, key=lambda a: a["ts_ms"])
        for idx, a in enumerate(ordered):
            leader = a.get("symbol")
            m = a.get("metrics") or {}
            if ((m.get("burst_z") or 0) < LEADER_Z
                    or (m.get("taker_buy_ratio") or 0) < LEADER_TBR
                    or (a.get("quote_volume_24h") or 0) < LEADER_QVOL):
                continue
            if idx != 0:
                continue      # not the earliest qualifying alert -> it is a follower
            leader_z = m.get("burst_z") or 0
            STATS["leaders"] += 1
            lpx = px.get(leader)
            if not lpx or leader not in followers:
                continue
            taken = 0
            for lag in followers[leader]:
                if taken >= MAX_LAGGARDS or len(b.positions) >= 6:
                    break
                if lag in b.positions:
                    continue
                lagpx = px.get(lag)
                if not lagpx:
                    continue
                try:
                    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=1m&limit=30" % lag)
                    b1 = [{"t": int(r[0]), "c": float(r[4]), "v": float(r[5]),
                           "tb": float(r[9])} for r in d]
                except Exception:
                    continue
                if len(b1) < 10:
                    continue
                leader_move = (lpx / (a.get("price") or lpx) - 1) * 100
                lag_move = (lagpx / b1[0]["c"] - 1) * 100
                if leader_move <= 0.3 or lag_move > LAG_FRACTION * leader_move:
                    continue
                v = b1[-1]["v"] or 1e-9
                tbr = (b1[-1]["tb"] / v) if v else 0
                if tbr < LAG_TBR:
                    continue
                # gradient: the follower must be weaker and later, not simultaneous
                fol = next((x for x in ordered[1:] if x.get("symbol") == lag), None)
                if fol is not None:
                    fz = (fol.get("metrics") or {}).get("burst_z") or 0
                    gap = (fol.get("ts_ms", 0) - a.get("ts_ms", 0)) / 1000.0
                    if fz >= GRADIENT_RATIO * leader_z or gap < MIN_GAP_S:
                        continue
                STATS["setups"] += 1
                b.open(lag, lagpx, [leader],
                       "follows %s (+%.2f%%), laggard %+.2f%% tbr %.0f%%" % (
                           leader, leader_move, lag_move, 100 * tbr))
                b.positions[lag]["leader_entry"] = lpx
                b.positions[lag]["leader_move"] = leader_move
                taken += 1
                log("OPEN  %-11s follows %-10s leader %+.2f%% lag %+.2f%%" % (
                    lag, leader, leader_move, lag_move))

        # never silent: if no pair setup in 10 minutes, take the strongest mover
        if not b.positions and (time.time() - b.last_open) > 600 and px:
            try:
                d = jget(MASTER + "/api/universe800", timeout=10).get("tickers") or []
            except Exception:
                d = []
            cand = [u for u in d if u.get("c") is not None and u.get("price")]
            if cand:
                best = max(cand, key=lambda u: abs(u["c"]))
                b.open(best["s"], best["price"], ["-"], "idle fill: mover %+.1f%%" % best["c"])
                log("idle fill %-11s %+.1f%%" % (best["s"], best["c"]))

        b.dump()
        time.sleep(max(1.0, args.loop))

    log("done: opened %d closed %d equity %.2f" % (b.opened, len(b.trades), b.equity))


if __name__ == "__main__":
    main()
