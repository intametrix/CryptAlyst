#!/usr/bin/env python3
"""executor - trades exactly what the Advisor flags, and locks profit fast.

No confirmation window, no waiting for a bar to close. Every second it reads the
market agent's board and acts:

  side    PUMP -> long, DUMP -> short (else follow the 24h sign)
  entry   immediate, at the live tick
  exits   lock  +0.25%   (exit the moment it is there)
          trail once peak >= 0.45%, lock at peak - 0.15%
          stop  -0.45%
          clock 120s

Both directions, many names, small holds. The 0.25% lock is above the 0.2% round
trip, which is the point: it books the spread rather than donating it.

    python3 executor.py --risk 2.5 --lock 0.25
"""
import argparse, json, os, time, urllib.request
import tactics as T
import risk

try:
    import tactics as _tactics
    _tactics.require_enabled('executor', 'executor.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"; SPOT = "https://api.binance.com"; FEE = 0.001
FC_API = "http://127.0.0.1:8790"
BUS = "http://127.0.0.1:8793"
_bus = {"cur": 0}


def bus_alerts():
    try:
        d = jget(BUS + "/latest?since=%d" % _bus["cur"], 2)
    except Exception:
        return []
    _bus["cur"] = d.get("cursor") or _bus["cur"]
    return d.get("signals") or []
FC_CACHE = {}


def forecast(sym, side):
    """The Go supervisor's blended view: probability, expected move, action."""
    key = (sym, side)
    hit = FC_CACHE.get(key)
    if hit and time.time() - hit[0] < 3:
        return hit[1]
    try:
        d = jget(FC_API + "/forecast?symbol=%s&side=%s&horizon=3600" % (sym, side), 5)
    except Exception:
        return None
    FC_CACHE[key] = (time.time(), d)
    return d
TA_CACHE = {}          # symbol -> (ts, bars)
TA_TTL = 12.0


def bars_1m(sym):
    hit = TA_CACHE.get(sym)
    if hit and time.time() - hit[0] < TA_TTL:
        return hit[1]
    d = jget(SPOT + "/api/v3/klines?symbol=%s&interval=1m&limit=60" % sym, 6)
    b = [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
          "c": float(r[4]), "v": float(r[5])} for r in d]
    TA_CACHE[sym] = (time.time(), b)
    return b


def ema(v, n):
    k = 2.0 / (n + 1); e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def rsi(bs, n=14):
    g = l = 0.0
    for i in range(1, len(bs)):
        ch = bs[i]["c"] - bs[i-1]["c"]
        g += max(ch, 0.0); l += max(-ch, 0.0)
    if l == 0: return 100.0
    rs = (g / n) / (l / n)
    return 100 - 100 / (1 + rs)


def ta_check(sym, side):
    """How many independent technical confirmations support this direction."""
    try:
        bs = bars_1m(sym)
    except Exception:
        return 0, "no data"
    if len(bs) < 30:
        return 0, "thin data"
    cl = [b["c"] for b in bs]
    e9, e21 = ema(cl[-30:], 9), ema(cl[-30:], 21)
    r = rsi(bs[-30:])
    px = cl[-1]
    vwap = sum(b["c"] * b["v"] for b in bs[-30:]) / (sum(b["v"] for b in bs[-30:]) or 1)
    volmed = sorted(b["v"] for b in bs[-21:-1])[10] or 1e-9
    volx = bs[-1]["v"] / volmed
    conf, why = 0, []
    if side == "buy":
        if e9 > e21: conf += 1; why.append("ema up")
        if r < 68: conf += 1; why.append("rsi %.0f" % r)
        if px > vwap: conf += 1; why.append("above vwap")
        if volx > 1.2: conf += 1; why.append("vol %.1fx" % volx)
    else:
        if e9 < e21: conf += 1; why.append("ema down")
        if r > 32: conf += 1; why.append("rsi %.0f" % r)
        if px < vwap: conf += 1; why.append("below vwap")
        if volx > 1.2: conf += 1; why.append("vol %.1fx" % volx)
    return conf, ", ".join(why)


def jget(u, t=4.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "executor/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=2.5)
    ap.add_argument("--lock", type=float, default=0.25)
    ap.add_argument("--trail-arm", type=float, default=0.45)
    ap.add_argument("--trail-gap", type=float, default=0.15)
    ap.add_argument("--stop", type=float, default=0.45)
    ap.add_argument("--clock", type=float, default=120)
    ap.add_argument("--max-pos", type=int, default=20)
    ap.add_argument("--min-score", type=float, default=0.20)
    ap.add_argument("--min-alert", type=float, default=60.0,
                    help="only PUMP/DUMP alerts at or above this confidence")
    ap.add_argument("--start", type=float, default=5000.0)
    a = ap.parse_args()
    print("EXECUTOR — advisor-driven, lock %.2f%%, stop %.2f%%, clock %.0fs, risk %.1f%%" %
          (a.lock, a.stop, a.clock, a.risk), flush=True)

    equity = a.start
    pos, trades, opened = {}, [], 0
    while True:
        now = time.time()
        board = {}
        try:
            d = jget(MASTER + "/api/opportunities", 4)
            for r in d.get("opportunities") or []:
                board[r["symbol"]] = r
        except Exception:
            pass
        px = {}
        if pos or board:
            syms = ",".join(list(pos.keys()) + list(board.keys())[:30])
            try:
                px = jget(MASTER + "/api/tick?syms=" + syms, 4).get("p") or {}
            except Exception:
                px = {}

        # ---- exits: lock, trail, stop, clock
        for sym in list(pos):
            p = pos[sym]; mark = px.get(sym)
            if not mark:
                continue
            chg = (mark / p["entry"] - 1) * 100
            if p["side"] == "sell":
                chg = -chg
            p["peak"] = max(p["peak"], chg)
            held = now - p["t"]
            # winners: skim fast. losers: give room, exit on signal flip or hard stop.
            flip = False
            for other in pos.values():
                pass
            reason = None
            if chg >= max(a.lock, 0.45):
                reason = "lock"
            elif chg >= 0.32 and held >= 2.5:
                reason = "skim"
            elif p["peak"] >= a.trail_arm and chg <= p["peak"] - a.trail_gap:
                reason = "trail"

            elif held >= max(a.clock, 900):
                reason = "clock"
            if reason and T.hold_loser(held, chg):
                reason = None      # hold losers for a day, by directive
            if reason is None and held > 20 and chg < 0:
                # a loser is only closed when the agents agree it is dead
                fc = forecast(sym, p["side"])
                dead, n = T.consensus_dead(fc)
                if dead:
                    reason = "consensus_exit"
            if reason is None and held > 5 and chg > 0:
                fc = forecast(sym, p["side"])
                if fc and (fc.get("prob") or 0) < 0.40:
                    reason = "forecast_flip"
            if reason:
                net = chg - 200 * FEE
                equity += p["notional"] * net / 100
                trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                               "exit": mark, "gross_pct": round(chg, 4),
                               "net_pct": round(net, 4), "peak_pct": round(p["peak"], 4),
                               "reason": reason, "held_s": round(now - p["t"], 2),
                               "notional": round(p["notional"], 2),
                               "entry_ms": int(p["t"] * 1000), "exit_ms": int(now * 1000),
                               "why": p["why"]})
                T.grade(p.get("views"), net > 0)
                pos.pop(sym, None)
                print("%s  CLOSE %-11s %-4s %+6.3f%% net %+6.3f%% %s" %
                      (time.strftime("%H:%M:%S"), sym, p["side"], chg, net, reason), flush=True)

        try:
            kill = set(json.load(open(os.path.join(STATE, "disabled.json"))).get("books") or [])
        except Exception:
            kill = set()
        if "executor" in kill:
            px = px or {}
        # ---- entries straight off the advisor board
        for sym, r in sorted(board.items(), key=lambda kv: -kv[1]["score"]):
            if len(pos) >= a.max_pos:
                break
            if "executor" in kill:
                break
            if sym in pos or r["score"] < a.min_score or not px.get(sym):
                continue
            chg = r.get("chg") or 0
            why = (r.get("why") or "").upper()
            import re as _re
            mscore = _re.search(r"(?:PUMP|DUMP)\s*(\d+)", why)
            if not mscore or float(mscore.group(1)) < a.min_alert:
                continue                      # not a high-confidence pump/dump
            if "PUMP" in why: side = "buy"
            else: side = "sell"
            _book_eq = pos[0]["notional"] * 0 if False else 5000.0
            _notional = min(_book_eq * a.risk / 100 * 40, _book_eq * 0.08)
            if side == "sell":
                _notional *= 0.5          # unbounded tail: shorts carry half the size
            _ok, _why = risk.allow(sym, side, _notional, 5000,
                                   (fc or {}).get("move_pct") if 'fc' in dir() else None,
                                   (T.ext_signals(sym) or {}).get("slippage_bps_5k"),
              book_positions=list(pos.values()) if isinstance(pos, dict) else None)
            risk.record(sym, side, _ok, _why)
            if not _ok:
                continue
            conf, tawhy = ta_check(sym, side)
            tf, ta_against = T.ta_vote(sym, side)
            if tf: conf += 1; tawhy += ", ta+%d" % tf
            if ta_against >= 3: continue
            if conf < T.min_confirmations(side):
                continue          # fighting the BTC drift needs more evidence
            fc = forecast(sym, side)
            if fc:
                ok, sc = T.ext_gate(sym, side)
                if not ok:
                    continue
                o = T.options()
                if fc and o.get("forecast_gate"):
                    if (fc.get("prob") or 0) < float(o.get("forecast_min", 0.45)):
                        continue
                    if (fc.get("move_pct") or 0) < float(o.get("min_expected_move", 0.0)):
                        continue          # the supervisor does not like this direction
                pass
            risk_here = a.risk * (0.6 + 0.2 * min(conf, 4))
            notional = min(equity * (risk_here / 100) / (a.stop / 100), equity * 3)
            if side == "sell":
                notional *= 0.5
            pos[sym] = {"side": side, "entry": px[sym], "t": now, "notional": notional,
                        "peak": 0.0, "conf": conf, "risk": round(risk_here, 2),
                        "views": (fc or {}).get("views") or [],
                        "why": "TA%d[%s] fcst %.2f · %s" % (
                            conf, tawhy, (fc or {}).get("prob") or 0, r.get("why") or "")}
            opened += 1
            print("%s  OPEN  %-11s %-4s %.6g  TA%d risk %.1f%%  %s" %
                  (time.strftime("%H:%M:%S"), sym, side, px[sym], conf, risk_here, tawhy), flush=True)

        out = {"ts": int(time.time() * 1000), "mode": "paper", "engine": "executor",
               "state": "running", "equity": round(equity, 4), "start_equity": a.start,
               "return_pct": round(100 * (equity / a.start - 1), 4),
               "realised": round(equity - a.start, 4), "unrealised": 0.0,
               "tp_pct": a.lock, "sl_pct": a.stop, "max_hold": a.clock,
               "trail_ratio": a.trail_gap, "trail_arm_pct": a.trail_arm,
               "positions": [{"symbol": s, "side": p["side"], "entry": p["entry"],
                              "price": p["entry"], "gross_pct": round(p["peak"], 3),
                              "net_pct": round(p["peak"] - 0.2, 3), "peak_pct": round(p["peak"], 3),
                              "held_s": round(time.time() - p["t"], 1),
                              "notional": round(p["notional"], 2),
                              "risk_usd": round(p["notional"] * a.stop / 100, 2),
                              "why": p["why"]} for s, p in pos.items()],
               "position": None, "trades": trades[-100:], "trade_count": len(trades),
               "wins": sum(1 for t in trades if t["net_pct"] > 0), "opened": opened,
               "max_positions": a.max_pos, "basis": "advisor executor · lock-fast",
               "open_risk_usd": round(sum(p["notional"] * a.stop / 100 for p in pos.values()), 2),
               "settings": {"risk_pct": a.risk, "lock_pct": a.lock, "stop_pct": a.stop,
                            "clock_s": a.clock}}
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "slow_executor.json.t"), "w") as fh:
            json.dump(out, fh)
        os.replace(os.path.join(STATE, "slow_executor.json.t"), os.path.join(STATE, "slow_executor.json"))
        time.sleep(1.0)


if __name__ == "__main__":
    main()
