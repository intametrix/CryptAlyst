#!/usr/bin/env python3
"""crypto_strategies - five crypto-native strategies, each with its own book,
all fed by the platform's own signal stream (pump/dump alerts + model votes).

  flow_follow     aggressive taker flow + burst        -> trade with the flow
  range_fade      extreme range position + burst       -> fade the extension
  model_stack     >=3 models agree                     -> trade the consensus
  sweep_reversal  CRITICAL alert with a burst z spike  -> fade the stop hunt
  momentum_stack  alert AND model agreement AND range  -> triple confirmation

Every strategy reads:
    /api/alerts            pump/dump onset, score, metrics, severity
    /api/models/signals    the six model votes per symbol
    /api/all               fills/positions of the other engines (for divergence)
    /api/v3/ticker/price   batched marks for marking to market

Each writes state/strategy_<name>.json so the dashboard can list it like any
other engine. PAPER ONLY.

    python3 crypto_strategies.py --seconds 86400
"""

import argparse
import json
import os
import time
import urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('cryptostrat', 'crypto_strategies.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
SIGNALS = "http://127.0.0.1:8787"   # alerts + model votes live on the Go backend
BINANCE = "https://api.binance.com"


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=8.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "crypto-strategies/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def prices():
    d = jget(BINANCE + "/api/v3/ticker/price")
    return {r["symbol"]: float(r["price"]) for r in d}


# --------------------------------------------------------------------------
# strategies: (alert, votes, held_symbols) -> ("buy"|"sell"|None, why)
# --------------------------------------------------------------------------
def flow_follow(a, votes, held):
    m = a.get("metrics") or {}
    tbr = m.get("taker_buy_ratio")
    burst = m.get("burst_multiple") or 0
    if tbr is None or burst < 3:
        return None, ""
    if tbr >= 0.70 and a.get("kind") == "PUMP":
        return "buy", "taker buy %.0f%% on %.1fx flow" % (100 * tbr, burst)
    if tbr <= 0.30 and a.get("kind") == "DUMP":
        return "sell", "taker buy %.0f%% on %.1fx flow" % (100 * tbr, burst)
    return None, ""


def range_fade(a, votes, held):
    m = a.get("metrics") or {}
    rp = m.get("range_position")
    burst = m.get("burst_multiple") or 0
    if rp is None or burst < 5:
        return None, ""
    if rp >= 0.92 and a.get("kind") == "PUMP":
        return "sell", "fade top of range %.0f%% on %.1fx" % (100 * rp, burst)
    if rp <= 0.08 and a.get("kind") == "DUMP":
        return "buy", "fade bottom of range %.0f%% on %.1fx" % (100 * rp, burst)
    return None, ""


def model_stack(a, votes, held):
    # only two detectors currently emit votes, so |net| can never exceed 2
    net = sum(votes.values())
    if abs(net) >= 2:
        return ("buy" if net > 0 else "sell"), "%d model net %+d" % (len(votes), net)
    return None, ""


def sweep_reversal(a, votes, held):
    m = a.get("metrics") or {}
    z = m.get("burst_z") or 0
    if a.get("severity") != "CRITICAL" or z < 8:
        return None, ""
    # a violent single burst is usually a stop sweep: fade it
    if a.get("kind") == "PUMP":
        return "sell", "CRITICAL pump z=%.1f — fading the sweep" % z
    return "buy", "CRITICAL dump z=%.1f — fading the sweep" % z


def momentum_stack(a, votes, held):
    m = a.get("metrics") or {}
    rp, tbr = m.get("range_position"), m.get("taker_buy_ratio")
    net = sum(votes.values())
    if rp is None or tbr is None:
        return None, ""
    if a.get("kind") == "PUMP" and net >= 2 and rp >= 0.55 and tbr >= 0.55:
        return "buy", "alert+models(%+d)+range %.0f%%+flow %.0f%%" % (net, 100 * rp, 100 * tbr)
    if a.get("kind") == "DUMP" and net <= -2 and rp <= 0.45 and tbr <= 0.45:
        return "sell", "alert+models(%+d)+range %.0f%%+flow %.0f%%" % (net, 100 * rp, 100 * tbr)
    return None, ""


LINEAGE = os.path.join(STATE, "lineage.json")
BASE_FN = {}   # name -> callable

STRATEGIES = [
    ("flow_follow", flow_follow, {"tp": 1.2, "sl": 1.0, "hold": 900, "risk": 0.65}),
    ("range_fade", range_fade, {"tp": 0.8, "sl": 1.2, "hold": 600, "risk": 0.65}),
    ("model_stack", model_stack, {"tp": 1.5, "sl": 1.0, "hold": 1200, "risk": 0.65}),
    ("sweep_reversal", sweep_reversal, {"tp": 0.7, "sl": 1.0, "hold": 300, "risk": 0.65}),
    ("momentum_stack", momentum_stack, {"tp": 1.5, "sl": 1.0, "hold": 900, "risk": 0.65}),
]

FEE = 0.001


class Book:
    def __init__(self, name, cfg, equity=2000.0):
        self.name, self.cfg = name, cfg
        self.equity = self.start = equity
        self.positions, self.trades = {}, []
        self.opened = 0
        self.seen = set()

    def mark(self, sym, px):
        p = self.positions.get(sym)
        if not p:
            return None
        raw = (px / p["entry"] - 1.0) * 100.0
        return raw if p["side"] == "buy" else -raw

    def manage(self, px):
        for sym in list(self.positions):
            p = self.positions[sym]
            mark = px.get(sym)
            if not mark:
                continue
            chg = self.mark(sym, mark)
            p["peak"] = max(p.get("peak", 0.0), chg)
            reason = None
            if chg >= self.cfg["tp"]:
                reason = "target"
            elif chg <= -self.cfg["sl"]:
                reason = "stop"
            elif (time.time() - p["opened"]) >= self.cfg["hold"]:
                reason = "clock"
            if reason:
                net = chg - 200 * FEE
                pnl = p["notional"] * net / 100.0
                self.equity += pnl
                self.trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                                    "exit": mark, "gross_pct": round(chg, 3),
                                    "net_pct": round(net, 3),
                                    "peak_pct": round(p["peak"], 3),
                                    "reason": reason,
                                    "held_s": round(time.time() - p["opened"], 1),
                                    "notional": round(p["notional"], 2),
                                    "pnl_usd": round(pnl, 4),
                                    "entry_ms": int(p["opened"] * 1000),
                                    "exit_ms": int(time.time() * 1000),
                                    "why": p.get("why")})
                self.positions.pop(sym, None)

    def open(self, sym, side, px, why):
        stop_frac = self.cfg["sl"] / 100.0
        notional = min(self.equity * (self.cfg["risk"] / 100.0) / max(stop_frac, 0.002),
                       self.equity * 0.25)
        self.positions[sym] = {"side": side, "entry": px, "opened": time.time(),
                               "notional": notional, "peak": 0.0, "why": why}
        self.opened += 1

    def dump(self):
        plist = []
        for sym, p in self.positions.items():
            plist.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                          "price": p.get("last", p["entry"]),
                          "gross_pct": round(p.get("mark", 0.0), 3),
                          "net_pct": round(p.get("mark", 0.0) - 0.2, 3),
                          "peak_pct": round(p.get("peak", 0.0), 3),
                          "held_s": round(time.time() - p["opened"], 1),
                          "notional": round(p["notional"], 2),
                          "risk_usd": round(p["notional"] * self.cfg["sl"] / 100.0, 2),
                          "why": p.get("why")})
        payload = {
            "ts": int(time.time() * 1000), "mode": "paper", "engine": self.name,
            "state": "running", "equity": round(self.equity, 4),
            "start_equity": self.start,
            "return_pct": round(100 * (self.equity / self.start - 1), 4),
            "realised": round(self.equity - self.start, 4),
            "unrealised": round(sum((p["notional"] * p.get("mark", 0.0) / 100.0)
                                    for p in self.positions.values()), 4),
            "tp_pct": self.cfg["tp"], "sl_pct": self.cfg["sl"],
            "max_hold": self.cfg["hold"], "trail_ratio": 0.0, "trail_arm_pct": 0.0,
            "positions": plist, "position": (plist[0] if plist else None),
            "trades": self.trades[-60:], "trade_count": len(self.trades),
            "wins": sum(1 for t in self.trades if t["net_pct"] > 0),
            "opened": self.opened, "max_positions": 8, "watchlist_size": 0,
            "basis": "signal-derived crypto strategy",
            "open_risk_usd": round(sum(p["notional"] * self.cfg["sl"] / 100.0
                                       for p in self.positions.values()), 2),
            "settings": {"risk_pct": self.cfg["risk"], "tp_pct": self.cfg["tp"],
                         "sl_pct": self.cfg["sl"], "max_hold": self.cfg["hold"]},
        }
        os.makedirs(STATE, exist_ok=True)
        path = os.path.join(STATE, "strategy_%s.json" % self.name)
        with open(path + ".t", "w") as fh:
            json.dump(payload, fh)
        os.replace(path + ".t", path)


def vote_map(signals):
    """symbol -> {direction_sign: int} from the model votes."""
    out = {}
    for s in signals:
        sym = s.get("symbol")
        d = (s.get("direction") or "").upper()
        if not sym:
            continue
        sign = 1 if ("BUY" in d or "PUMP" in d) else -1
        out.setdefault(sym, {})
        out[sym][s.get("model") or "?"] = sign
    return {k: v for k, v in out.items()}


MODCACHE = {}


def load_module(path):
    """Import a generated model file (models/xxx.py) and return its signal()."""
    if path in MODCACHE:
        return MODCACHE[path]
    import importlib.util
    full = path if os.path.isabs(path) else os.path.join(HERE, path)
    spec = importlib.util.spec_from_file_location("gen_" + os.path.basename(path)[:-3], full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "signal", None)
    if not callable(fn):
        raise RuntimeError("no signal() in " + path)
    MODCACHE[path] = fn
    return fn


def load_lineage_strategies():
    """Prefer the lineage written by darwin; fall back to the built-ins."""
    builtin = {n: (fn, cfg) for n, fn, cfg in STRATEGIES}
    try:
        with open(LINEAGE) as fh:
            lin = json.load(fh)
    except Exception:
        return [(n, fn, cfg) for n, fn, cfg in STRATEGIES]
    out = []
    for s in lin.get("strategies") or []:
        if not s.get("alive", True):
            continue
        mod = s.get("module")
        if mod:
            try:
                raw = load_module(mod)
                # generated modules take (alert, votes); built-ins take (alert, votes, held)
                fn = (lambda r: (lambda a, v, held: r(a, v)))(raw)
            except Exception:
                continue
        else:
            base = s.get("base")
            fn = dict((k, v[0]) for k, v in builtin.items()).get(base)
            if not fn:
                continue
        pr = s.get("params") or {}
        cfg = {"tp": pr.get("tp", 1.0), "sl": pr.get("sl", 1.0),
               "hold": pr.get("hold", 900), "risk": pr.get("risk", 1.0)}
        out.append((s["name"], fn, cfg))
    return out or [(n, fn, cfg) for n, fn, cfg in STRATEGIES]


def seed_lineage():
    """First run: write the built-ins into a lineage file so darwin can edit it."""
    if os.path.exists(LINEAGE):
        return
    lin = {"generation": 1, "strategies": [
        {"name": n, "base": n, "params": dict(cfg), "generation": 1, "alive": True}
        for n, _, cfg in STRATEGIES]}
    os.makedirs(STATE, exist_ok=True)
    with open(LINEAGE, "w") as fh:
        json.dump(lin, fh, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=86400)
    ap.add_argument("--tick", type=float, default=0.5)
    ap.add_argument("--max-positions", type=int, default=6)
    args = ap.parse_args()

    seed_lineage()
    log("CRYPTO STRATEGIES — paper only, fed by the platform signal stream")
    for name, _, cfg in load_lineage_strategies():
        log("  %-16s tp %.1f sl %.1f hold %ds risk %.1f%%" % (
            name, cfg["tp"], cfg["sl"], cfg["hold"], cfg["risk"]))

    books = {name: Book(name, cfg) for name, _, cfg in load_lineage_strategies()}
    started = time.time()
    last = 0.0

    while time.time() - started < args.seconds:
        try:
            alerts = jget(SIGNALS + "/api/alerts?limit=200").get("alerts") or []
        except Exception:
            alerts = []
        try:
            sigs = jget(SIGNALS + "/api/models/signals?limit=400").get("signals") or []
        except Exception:
            sigs = []
        try:
            px = prices()
        except Exception:
            px = {}

        votes = vote_map(sigs)

        for name, fn, cfg in load_lineage_strategies():
            if _tactics.is_disabled(name):
                continue          # A6: a disabled model is never used
            b = books.get(name)
            if b is None:
                b = books[name] = Book(name, cfg)
                log("new lineage member: %-18s %s" % (name, cfg))
            b.manage(px)
            for sym, p in b.positions.items():
                if sym in px:
                    p["last"] = px[sym]
                    p["mark"] = b.mark(sym, px[sym]) or 0.0

            if len(b.positions) >= args.max_positions:
                continue
            for a in alerts:
                sym = a.get("symbol")
                if not sym or sym in b.positions:
                    continue
                eid = a.get("event_id")
                if eid and (name, eid) in b.seen:
                    continue
                side, why = fn(a, votes.get(sym, {}), b.positions)
                if side and sym in px:
                    b.seen.add((name, eid))
                    b.open(sym, side, px[sym], why)
                    log("%-16s %-4s %-12s %s" % (name, side.upper(), sym, why))
                if len(b.positions) >= args.max_positions:
                    break

        for b in books.values():
            b.dump()

        time.sleep(max(0.2, args.tick))

    log("")
    for name, b in books.items():
        log("%-16s equity %.2f (%+.2f%%) opened %d closed %d" % (
            name, b.equity, 100 * (b.equity / b.start - 1), b.opened, len(b.trades)))


if __name__ == "__main__":
    main()
