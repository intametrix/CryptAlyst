#!/usr/bin/env python3
"""Alert-driven trader v2: batched prices, multiple positions, trailing exits.

What changed from v1 and why:

  * PRICES ARE BATCHED.  /api/v3/ticker/price returns EVERY symbol in one
    request (weight 4).  v1 did one request per symbol per tick, so five open
    positions meant five round-trips at ~300ms each and the loop crawled.
    Now the tick is a single call and positions are checked every second.
  * MANY POSITIONS.  v1 held one at a time, so with an 8%/2% bracket nothing
    ever filled while you watched.  Now up to --max-positions open at once, each
    with its own peak/ratchet.
  * FILLS ARE VISIBLE.  Entries are logged the instant they open, closures the
    instant they trigger.

Rules (unchanged): alert direction (PUMP->long, DUMP->short), confidence floor,
peak ratchets every tick, close at +cap, at trail% below peak, or at -stop%.

PAPER ONLY -- no keys, no orders.
"""

import argparse
import json
import os
import threading
import time
import urllib.request

BASE = "https://api.binance.com"
ALL_PRICES = BASE + "/api/v3/ticker/price"
ALERTS = "http://127.0.0.1:8787/api/alerts"
MODEL_AGG = "http://127.0.0.1:8792/api/aggregate"


EDITABLE = ("risk_pct", "max_open_risk_pct", "max_positions", "tp_pct", "sl_pct",
            "trail_ratio", "trail_arm_pct", "lock_trade_pct", "lock_profit_pct",
            "lock_usd", "max_hold", "min_agree", "min_confidence_pct",
            "min_liquidity",
            "scan_every", "conviction_cap", "vol_mult", "tolerance", "min_stop_frac")


def settings_snapshot(args):
    return {k: getattr(args, k) for k in EDITABLE if hasattr(args, k)}


def apply_settings(args, c, extra=None):
    """Generic: any key that exists on args is coerced to its type and set."""
    changed = []
    for k, v in c.items():
        if k == "ts":
            continue
        if k == "hold_sec" and extra is not None:
            extra(float(v)); changed.append("hold_sec=" + str(v)); continue
        if not hasattr(args, k):
            continue
        cur = getattr(args, k)
        try:
            setattr(args, k, type(cur)(v))
            changed.append("%s=%s" % (k, getattr(args, k)))
        except Exception:
            pass
    return changed

def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=6.0):
    req = urllib.request.Request(url, headers={"User-Agent": "swing3v2/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def all_prices():
    """One request, every symbol. The whole point of the batching."""
    rows = jget(ALL_PRICES)
    return {r["symbol"]: float(r["price"]) for r in rows}


TICKER24 = BASE + "/api/v3/ticker/24hr"
CAL_PATH = "/tmp/pumpdump-run/calibration.json"
COMPONENTS = {"aggression": "component_aggression", "flow": "component_flow",
              "quality": "component_quality", "speed": "component_speed"}


def load_calibration(path=CAL_PATH):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _feature(alert, name):
    m = alert.get("metrics") or {}
    comp = alert.get("components") or {}
    if name in m:
        try:
            return float(m[name])
        except Exception:
            return None
    for short, full in COMPONENTS.items():
        if name == full and short in comp:
            try:
                return float(comp[short])
            except Exception:
                return None
    v = alert.get(name)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def calibrated_probability(alert, cal):
    """P(this alert is CORRECT), from the backend's fitted logistic model.

    The raw score is NOT a probability: a 94 score maps to ~28% here, and the
    median alert to ~3%. Missing features are imputed at the fitted MEAN, which
    standardises to 0 and is neutral -- imputing 0.0 instead pushes every
    prediction to 0.99 and is silently wrong.
    """
    if not cal:
        return None
    import math
    z = cal["intercept"]
    for i, f in enumerate(cal["features"]):
        v = _feature(alert, f)
        sd = cal["std"][i] or 1.0
        mu = cal["mean"][i]
        if v is None:
            v = mu
        z += cal["weights"][i] * ((v - mu) / sd)
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USD1", "USDP", "DAI", "USDE",
           "AEUR", "EUR", "GBP", "TRY", "BRL", "ARS", "JPY", "XUSD", "USD"}


def seed_watchlist(min_volume=1_000_000.0, cap=400):
    """Diversify from the start: every liquid USDT pair, not just alerted ones."""
    rows = jget(TICKER24)
    out = []
    for r in rows:
        sym = r["symbol"]
        if not sym.endswith("USDT") or sym.endswith(("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        if sym[:-4] in STABLES:
            continue
        try:
            qv = float(r["quoteVolume"])
        except Exception:
            continue
        if qv < min_volume:
            continue
        out.append((qv, sym))
    out.sort(reverse=True)
    return set(sym for _, sym in out[:cap])


def fetch_alerts():
    return jget(ALERTS).get("alerts") or []


# --- async alert feed --------------------------------------------------
# The scanner runs on its own thread so a slow HTTP fetch can never stall
# position management, and a fast position tick can never delay discovery.
_LATEST = {"alerts": [], "at": 0.0}
_LOCK = threading.Lock()


# --- async model aggregate --------------------------------------------
_AGG = {"by_symbol": {}, "at": 0.0}


def start_model_poller(interval=1.0):
    def loop():
        while True:
            try:
                d = jget(MODEL_AGG, timeout=5.0)
                by = {}
                for r in d.get("rows") or []:
                    by[r["symbol"]] = {"net": r.get("net") or 0.0,
                                       "decision": r.get("decision"),
                                       "agree": r.get("agreement") or 0}
                with _LOCK:
                    _AGG["by_symbol"] = by
                    _AGG["at"] = time.time()
            except Exception:
                pass
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True).start()


def model_view(symbol):
    with _LOCK:
        return _AGG["by_symbol"].get(symbol)


def start_alert_poller(interval=0.5):
    def loop():
        while True:
            try:
                a = fetch_alerts()
                with _LOCK:
                    _LATEST["alerts"] = a
                    _LATEST["at"] = time.time()
            except Exception:
                pass
            time.sleep(interval)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def latest_alerts(max_age=3.0):
    with _LOCK:
        if time.time() - _LATEST["at"] > max_age:
            return []
        return list(_LATEST["alerts"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=28800)
    ap.add_argument("--tick", type=float, default=0.5, help="price/manage cadence (s)")
    ap.add_argument("--scan-every", type=float, default=0.5, help="alert scan cadence (s)")
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--min-confidence", type=float, default=0.75)
    ap.add_argument("--cap-pct", type=float, default=0.0,
                    help="0 disables the cap so winners can run")
    ap.add_argument("--trail-pct", type=float, default=2.0)
    ap.add_argument("--trail-ratio", type=float, default=0.20,
                    help="80/20 rule: close when PnL gives back this fraction of its peak")
    ap.add_argument("--watch-min-volume", type=float, default=1_000_000.0)
    ap.add_argument("--watch-max", type=int, default=400)
    ap.add_argument("--min-liquidity", type=float, default=50_000_000.0,
                    help="only trade symbols with at least this 24h quote volume")
    ap.add_argument("--model-gate", choices=("off", "veto", "require"), default="veto",
                    help="veto=skip when the model aggregate opposes the trade; "
                         "require=only trade when it agrees")
    ap.add_argument("--tech-confirm", action="store_true", default=True,
                    help="require the alert's own metrics to agree with the direction")
    ap.add_argument("--calibration", default=CAL_PATH)
    ap.add_argument("--basis", choices=("confidence", "calibrated"), default="confidence",
                    help="which number gates and sizes: the alert's own score, or P(correct)")
    ap.add_argument("--long-only", action="store_true",
                    help="the ledger shows DUMP->short nets -0.297%% vs PUMP->long -0.190%%")
    ap.add_argument("--min-calibrated", type=float, default=0.10,
                    help="trade only alerts whose CALIBRATED P(correct) clears this")
    ap.add_argument("--bad-tick-pct", type=float, default=-0.25,
                    help="a tick only counts as bad if PnL is at or below this %% "
                         "(flat noise is not a signal to sell)")
    ap.add_argument("--bad-ticks", type=int, default=4,
                    help="close after this many consecutive negative ticks")
    ap.add_argument("--max-hold", type=float, default=0.0,
                    help="force-close after this many seconds (0 = hold indefinitely)")
    ap.add_argument("--trail-arm-pct", type=float, default=0.5,
                    help="peak PnL must reach this before the 80/20 trail arms; "
                         "otherwise a 0.01%% peak closes the trade for a 0.2%% fee")
    ap.add_argument("--stop-pct", type=float, default=2.0)
    ap.add_argument("--alert-max-age", type=float, default=90.0)
    ap.add_argument("--cooldown", type=float, default=120.0)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--equity", type=float, default=300.0)
    ap.add_argument("--risk-pct", type=float, default=0.5,
                    help="percent of equity lost if this trade's stop is hit")
    ap.add_argument("--max-per-side", type=int, default=12,
                    help="max positions on one side; 20 correlated longs is one bet")
    ap.add_argument("--rebalance", action="store_true", default=True,
                    help="when the risk budget is full, evict the weakest position to fund a better one")
    ap.add_argument("--evict-margin", type=float, default=0.05,
                    help="new alert must beat the victim's confidence by this much")
    ap.add_argument("--max-open-risk-pct", type=float, default=30.0,
                    help="hard ceiling on TOTAL open risk as %% of equity")
    ap.add_argument("--cycle-seconds", type=float, default=0.0,
                    help="close EVERY position this often and rescan (0 = off)")
    ap.add_argument("--lock-trade-pct", type=float, default=0.0,
                    help="close a single trade once its PnL reaches this %% (0 = off)")
    ap.add_argument("--lock-profit-pct", type=float, default=1.0,
                    help="when total unrealised >= this %% of equity, bank every winner")
    ap.add_argument("--lock-cooldown", type=float, default=60.0)
    ap.add_argument("--stale-after", type=float, default=600.0,
                    help="seconds before a position is judged dead weight")
    ap.add_argument("--stale-pct", type=float, default=-0.3,
                    help="close if still below this after --stale-after")
    ap.add_argument("--conviction-cap", type=float, default=2.0,
                    help="max multiple of base risk a high-confidence alert can take")
    ap.add_argument("--state", default="state/swing_state.json")
    ap.add_argument("--control", default="state/control.json",
                    help="live risk overrides written by the panel's controls")
    ap.add_argument("--ledger", default="ledger/swing3.jsonl")
    args = ap.parse_args()

    log("SWING v2 -- PAPER ONLY | %d positions | %s | trail %d%% of peak | stop -%.0f%%"
        % (args.max_positions,
           ("cap +%.0f%%" % args.cap_pct) if args.cap_pct > 0 else "NO CAP (winners run)",
           int(100 * args.trail_ratio), args.stop_pct))
    log("risk ceiling: total open risk capped at %.0f%% of equity" % args.max_open_risk_pct)
    log("rebalance: %s" % ("ON - weakest holding rotates out for a better alert"
                           if args.rebalance else "off"))
    log("downside: close after %d consecutive negative ticks, then rescan" % args.bad_ticks)
    log("cycle: close EVERY position every %.0fs then rescan" % args.cycle_seconds
        if args.cycle_seconds > 0 else "cycle close: off")
    log("per-trade lock: close each trade at +%.2f%% (fee is 0.20%% round trip)"
        % args.lock_trade_pct)
    log("profit lock: bank all winners when unrealised >= %.1f%% of equity (cooldown %.0fs)"
        % (args.lock_profit_pct, args.lock_cooldown))
    log("dead weight: close if still < %.1f%% after %.0fs" % (args.stale_pct, args.stale_after))
    log("sizing: %.2f%% equity risk per trade at the %.0f%% stop, x conviction (cap %.1fx)"
        % (args.risk_pct, args.stop_pct, args.conviction_cap))
    start_alert_poller(0.5)
    start_model_poller(1.0)
    log("async alerts: scanner thread every 0.5s | position tick %.1fs" % args.tick)
    log("trail: give back %.0f%% of peak PnL once peak >= %.1f%% (80/20), cap +%.0f%%, stop -%.0f%%"
        % (100 * args.trail_ratio, args.trail_arm_pct, args.cap_pct, args.stop_pct))
    log("basis: %s | floor %.0f%% | alerts every %.1fs | max age %.0fs | cooldown %.0fs"
        % (args.basis.upper(),
           100 * (args.min_confidence if args.basis == "confidence" else args.min_calibrated),
           args.scan_every, args.alert_max_age, args.cooldown))

    watch = set()
    try:
        watch = seed_watchlist(args.watch_min_volume, args.watch_max)
        log("watchlist seeded: %d liquid USDT pairs (>= $%.0fM 24h volume)"
            % (len(watch), args.watch_min_volume / 1e6))
    except Exception as exc:
        log("watchlist seed failed: %r" % (exc,))

    cal = load_calibration(args.calibration)
    if cal:
        log("calibration loaded: %d features, n=%d, base rate %.3f (raw scores are NOT probabilities)"
            % (len(cal["features"]), cal["n"], cal["base_rate"]))
    else:
        log("WARNING: no calibration at %s -- falling back to raw scores" % args.calibration)

    equity = args.equity
    start_equity = equity
    positions = {}
    trades = []
    seen_alerts = set()
    cooldown = {}
    scans = 0
    opened_count = 0
    started = time.time()
    next_scan = 0.0
    prices = {}
    rank = {"CRITICAL": 3, "ALERT": 2, "WATCH": 1}
    last_lock = 0.0
    last_cycle = time.time()
    FUNNEL = {"alerts": 0, "dup": 0, "side_full": 0, "stale": 0, "liquidity": 0,
              "technical": 0, "model": 0, "confidence": 0, "opened": 0}
    evict = set()
    control_mtime = 0.0
    CTRL = {"path": os.path.abspath(args.control), "mtime": 0.0}

    def apply_control():
        try:
            mt = os.path.getmtime(CTRL["path"])
        except OSError:
            return
        if mt <= CTRL["mtime"]:
            return
        CTRL["mtime"] = mt
        try:
            with open(CTRL["path"]) as fh:
                c = json.load(fh)
        except Exception:
            return
        ch = apply_settings(args, c, None)
        if ch:
            log("SETTINGS APPLIED -> " + ", ".join(ch))

    def dump_state():
        plist = []
        for sym, p in positions.items():
            px = prices.get(sym, p["entry"])
            chg = (px / p["entry"] - 1.0) * 100.0
            if p["side"] == "sell":
                chg = -chg
            peak = p.get("peak", 0.0)
            plist.append({
                "symbol": sym, "side": p["side"], "entry": p["entry"], "price": px,
                "gross_pct": round(chg, 3), "net_pct": round(chg - 200 * args.fee, 3),
                "peak_pct": round(peak, 3),
                "trail_line": (round(peak * (1.0 - args.trail_ratio), 3)
                               if peak >= args.trail_arm_pct else round(-args.stop_pct, 3)),
                "trail_armed": peak >= args.trail_arm_pct,
                "to_cap": (round(args.cap_pct - chg, 3) if args.cap_pct > 0 else None),
                "to_trail": round(chg - (peak * (1.0 - args.trail_ratio)
                                         if peak >= args.trail_arm_pct else -args.stop_pct), 3),
                "held_s": round(time.time() - p["opened"], 1),
                "entry_ms": int(p["opened"] * 1000),
                "alert_kind": p.get("kind"), "conf": p.get("conf"),
                "why": p.get("why"), "decision": p.get("decision"),
                "notional": p.get("notional"), "risk_usd": p.get("risk_usd"),
                "pcal": p.get("pcal"),
            })
        payload = {
            "ts": int(time.time() * 1000),
            "mode": "paper",
            "equity": round(equity, 4),
            "start_equity": start_equity,
            "return_pct": round(100.0 * (equity / start_equity - 1.0), 4),
            "tp_pct": args.cap_pct, "sl_pct": args.stop_pct,
            "trail_pct": args.trail_pct,
            "trail_ratio": args.trail_ratio,
            "trail_arm_pct": args.trail_arm_pct,
            "scans": scans, "opened": opened_count,
            "positions": plist, "position": (plist[0] if plist else None),
            "trades": trades[-80:],
            "trade_count": len(trades),
            "wins": sum(1 for t in trades if t["net_pct"] > 0),
            "max_positions": args.max_positions,
            "risk_pct": args.risk_pct,
            "max_per_side": args.max_per_side,
            "max_open_risk_pct": args.max_open_risk_pct,
            "rebalance": bool(args.rebalance),
            "max_hold": args.max_hold,
            "min_calibrated": args.min_calibrated,
            "min_liquidity": args.min_liquidity,
            "tech_confirm": bool(args.tech_confirm),
            "model_gate": args.model_gate,
            "lock_profit_pct": args.lock_profit_pct,
            "lock_trade_pct": args.lock_trade_pct,
            "cycle_seconds": args.cycle_seconds,
            "bad_ticks": args.bad_ticks,
            "stale_after": args.stale_after,
            "settings": settings_snapshot(args),
            "funnel": dict(FUNNEL),
            "basis": args.basis,
            "open_risk_usd": round(sum(p.get("risk_usd") or 0 for p in positions.values()), 4),
            "watchlist": sorted(watch),
            "watchlist_size": len(watch),
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.state)) or ".", exist_ok=True)
        tmp = args.state + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, args.state)

    while time.time() - started < args.seconds:
        t0 = time.time()

        # ---- one batched price call ------------------------------------
        try:
            prices.update(all_prices())
        except Exception as exc:
            log("price batch failed: %r" % (exc,))

        # ---- CYCLE CLOSE: wipe the book and rescan ---------------------
        cycle_now = False
        if args.cycle_seconds > 0 and (time.time() - last_cycle) >= args.cycle_seconds:
            cycle_now = True
            last_cycle = time.time()

        # ---- PORTFOLIO LOCK -------------------------------------------
        # When the book as a whole is doing well, banking the winners is worth
        # more than the last few percent of any single one: realised P&L cannot
        # be given back, unrealised can.
        lock_now = False
        if args.lock_profit_pct > 0 and (time.time() - last_lock) > args.lock_cooldown and positions:
            total = 0.0
            for sym, p in positions.items():
                px = prices.get(sym)
                if not px:
                    continue
                raw = (px / p["entry"] - 1.0) * 100.0
                chg = raw if p["side"] == "buy" else -raw
                total += (p.get("notional") or 0) * chg / 100.0
            if total >= equity * args.lock_profit_pct / 100.0:
                lock_now = True

        # ---- manage every open position --------------------------------
        locked = 0
        for sym in list(positions):
            p = positions[sym]
            px = prices.get(sym)
            if not px:
                continue
            chg = (px / p["entry"] - 1.0) * 100.0
            if p["side"] == "sell":
                chg = -chg
            peak = max(p.get("peak", 0.0), chg)
            p["peak"] = peak
            # CONSECUTIVE-LOSING-TICK counter: a position that keeps printing
            # negative is telling you it was the wrong pick, so it is closed and
            # the slot goes back to the scanner for a better opportunity.
            if chg <= args.bad_tick_pct:
                p["neg_run"] = p.get("neg_run", 0) + 1
            else:
                p["neg_run"] = 0
            reason = None
            if cycle_now:
                reason = "cycle_%.0fs" % args.cycle_seconds
            elif sym in evict:
                reason = "rebalance"
            elif lock_now and chg > 0:
                reason = "lock_in"
            elif args.lock_trade_pct > 0 and chg >= args.lock_trade_pct:
                reason = "lock_trade_%.2f" % args.lock_trade_pct
            elif args.cap_pct > 0 and chg >= args.cap_pct:
                # PER-TRADE profit cap: this trade is done, its slot goes back
                # to the scanner. Not a book-level target.
                reason = "cap_%.1fpct" % args.cap_pct
            elif args.bad_ticks > 0 and p.get("neg_run", 0) >= args.bad_ticks:
                # PER-TRADE downside: N consecutive negative ticks means the
                # pick is wrong. Close it, find a better one.
                reason = "%d_bad_points" % args.bad_ticks
            elif peak >= args.trail_arm_pct and chg <= peak * (1.0 - args.trail_ratio):
                reason = "trail_%d_of_peak" % int(100 * args.trail_ratio)
            elif chg <= -args.stop_pct:
                reason = "stop_%.0f" % args.stop_pct
            elif args.max_hold > 0 and (time.time() - p["opened"]) >= args.max_hold:
                reason = "skim_timeout"
            elif (args.stale_after > 0 and (time.time() - p["opened"]) >= args.stale_after
                  and chg < args.stale_pct):
                reason = "stale_cut"
            if reason:
                net = chg - 200 * args.fee
                notional = p.get("notional", equity)
                pnl_usd = notional * net / 100.0
                equity += pnl_usd
                trades.append({
                    "symbol": sym, "side": p["side"], "entry": p["entry"], "exit": px,
                    "gross_pct": round(chg, 3), "net_pct": round(net, 3),
                    "peak_pct": round(peak, 3), "reason": reason,
                    "held_s": round(time.time() - p["opened"], 1),
                    "notional": round(notional, 2),
                    "pnl_usd": round(pnl_usd, 4),
                               "entry_ms": int(p["opened"] * 1000),
                               "exit_ms": int(time.time() * 1000),
                               "decision": p.get("decision"),
                    "equity_after": round(equity, 4),
                })
                if reason == "lock_in":
                    locked += 1
                log("<<< CLOSE %-11s %-4s %+6.2f%% net %+6.2f%%  %-9s pnl %+7.3f  eq %.2f"
                    % (sym, p["side"].upper(), chg, net, reason, pnl_usd, equity))
                cooldown[sym] = time.time()
                positions.pop(sym, None)
                evict.discard(sym)
        if locked:
            last_lock = time.time()
            log("=== LOCKED IN %d winners, %d still open, equity %.2f"
                % (locked, len(positions), equity))

        # ---- scan for new entries --------------------------------------
        _force = os.path.join(os.path.dirname(os.path.abspath(args.control)), "force_swing.json")
        if os.path.exists(_force):
            try:
                os.remove(_force)
            except Exception:
                pass
            next_scan = 0.0
            log("FORCED rescan")
        if time.time() >= next_scan:
            apply_control()
            scans += 1
            next_scan = time.time() + args.scan_every
            longs = sum(1 for p in positions.values() if p["side"] == "buy")
            shorts = len(positions) - longs
            # HARD RISK CEILING. Slots alone are not a risk rule: 50 positions at
            # 1% risk each is 50%+ of equity at risk at once. The budget decides
            # how many may actually open.
            open_risk = sum(p.get("risk_usd") or 0 for p in positions.values())
            budget = equity * args.max_open_risk_pct / 100.0
            free = args.max_positions - len(positions)
            if free > 0:
                alerts = latest_alerts()
                if not alerts:
                    try:
                        alerts = fetch_alerts()
                    except Exception as exc:
                        log("alerts fetch failed: %r" % (exc,))
                        alerts = []
                now_ms = int(time.time() * 1000)
                cand = []
                for a in alerts:
                    eid = a.get("event_id")
                    kind = a.get("kind")
                    if kind in ("PUMP", "DUMP") and a.get("symbol"):
                        watch.add(a["symbol"])      # watchlist grows, never blocked
                    if not eid or eid in seen_alerts or kind not in ("PUMP", "DUMP"):
                        FUNNEL["dup"] += 1
                        continue
                    FUNNEL["alerts"] += 1
                    if args.long_only and kind != "PUMP":
                        continue
                    sym = a.get("symbol")
                    if sym in positions:
                        continue
                    want_long = kind == "PUMP"
                    if want_long and longs >= args.max_per_side:
                        FUNNEL["side_full"] += 1; continue
                    if not want_long and shorts >= args.max_per_side:
                        FUNNEL["side_full"] += 1; continue
                    if time.time() - cooldown.get(sym, 0) < args.cooldown:
                        continue
                    if now_ms - (a.get("ts_ms") or 0) > args.alert_max_age * 1000:
                        FUNNEL["stale"] += 1
                        continue
                    conf = (a.get("score") or 0.0) / 100.0
                    # TECHNICAL + LIQUIDITY GATE, from the alert's own snapshot:
                    #   quote_volume_24h  -> must be a liquid market
                    #   range_position    -> long only in the upper half of the
                    #                        24h range, short only in the lower
                    #   taker_buy_ratio   -> the aggressive side must agree
                    m = a.get("metrics") or {}
                    try:
                        qv = float(m.get("quote_volume_24h") or 0.0)
                    except Exception:
                        qv = 0.0
                    if args.min_liquidity > 0 and qv < args.min_liquidity:
                        FUNNEL["liquidity"] += 1
                        continue
                    if args.tech_confirm:
                        rp = m.get("range_position")
                        tbr = m.get("taker_buy_ratio")
                        if kind == "PUMP":
                            if (rp is not None and float(rp) < 0.45) or \
                               (tbr is not None and float(tbr) < 0.55):
                                FUNNEL["technical"] += 1
                                continue
                        else:
                            if (rp is not None and float(rp) > 0.55) or \
                               (tbr is not None and float(tbr) > 0.45):
                                FUNNEL["technical"] += 1
                                continue
                    # MODEL AGGREGATE GATE: the six models produce a net score
                    # per symbol. 'veto' refuses to trade against it; 'require'
                    # demands agreement and a symbol present in the aggregate.
                    if args.model_gate != "off":
                        mv = model_view(sym)
                        if mv is None:
                            if args.model_gate == "require":
                                continue
                        else:
                            wants_long = (kind == "PUMP")
                            agrees = (mv["net"] > 0) if wants_long else (mv["net"] < 0)
                            opposes = (mv["net"] < 0) if wants_long else (mv["net"] > 0)
                            if opposes:
                                FUNNEL["model"] += 1
                                continue
                            if args.model_gate == "require" and not agrees:
                                FUNNEL["model"] += 1
                                continue
                    pcal = calibrated_probability(a, cal)
                    # BASIS: confidence (the alert's own score) or calibrated
                    # P(correct). Both are computed; only one decides.
                    if args.basis == "calibrated" and pcal is not None:
                        edge = pcal
                        if edge < args.min_calibrated:
                            FUNNEL["confidence"] += 1
                            continue
                    else:
                        edge = conf
                        if edge < args.min_confidence:
                            continue
                    cand.append((a, conf, pcal, edge))
                # rank by the ACTIVE basis (confidence or calibrated), then
                # severity, then freshness -- the best alerts get the slots
                cand.sort(key=lambda ac: (-(ac[3] or 0.0),
                                          -rank.get(ac[0].get("severity"), 0),
                                          -(ac[0].get("ts_ms") or 0)))
                for a, conf, pcal, edge in cand[:free]:
                    sym = a["symbol"]
                    px = prices.get(sym)
                    if not px:
                        continue
                    side = "buy" if a["kind"] == "PUMP" else "sell"
                    # SIZING BY RISK, NOT BY HOPE. The stop distance defines the
                    # position: notional such that a -stop% move costs exactly
                    # risk_pct of equity, scaled by conviction (bounded).
                    # conviction is measured against a FIXED reference, not the
                    # confidence floor: with the floor at 0 the old form gave
                    # every alert the full 2x multiplier and doubled total risk
                    # size on whichever edge is the basis, against a fixed
                    # reference so the multiplier cannot silently double
                    ref = 0.75 if args.basis == "confidence" else max(args.min_calibrated, 0.05)
                    conv_mult = min(args.conviction_cap, max(0.5, edge / ref))
                    risk_frac = (args.risk_pct / 100.0) * conv_mult
                    notional = equity * risk_frac / (args.stop_pct / 100.0)
                    risk_usd = notional * args.stop_pct / 100.0
                    if open_risk + risk_usd > budget:
                        # REBALANCE: if a materially better alert is waiting and
                        # the budget is spent, rotate out the weakest holding
                        # rather than refusing the opportunity outright.
                        victim = None
                        if args.rebalance and positions:
                            scored = []
                            for vsym, vp in positions.items():
                                if vsym in evict or vsym in positions and vsym == sym:
                                    continue
                                vpx = prices.get(vsym)
                                if not vpx:
                                    continue
                                vraw = (vpx / vp["entry"] - 1.0) * 100.0
                                vchg = vraw if vp["side"] == "buy" else -vraw
                                scored.append((vchg, -vp["opened"], vsym, vp.get("conf") or 0))
                            if scored:
                                scored.sort()
                                worst_chg, _, worst_sym, worst_conf = scored[0]
                                if conf >= worst_conf + args.evict_margin:
                                    victim = worst_sym
                        if victim:
                            evict.add(victim)
                            log("rebalance: evicting %-11s (worst open) to fund %-11s (conf %3.0f%%)"
                                % (victim, sym, 100 * conf))
                            break
                        log("risk ceiling: would need $%.2f more risk, budget $%.2f of $%.2f (%.0f%% cap)"
                            % (risk_usd, max(0.0, budget - open_risk), budget,
                               args.max_open_risk_pct))
                        break
                    open_risk += risk_usd
                    _why = "alert %s conf %.0f%%" % (a["kind"], 100 * conf)
                    if args.model_gate != "off":
                        _why += " · models %s" % ("agree" if mv else "absent")
                    if args.tech_confirm:
                        _why += " · tech ok"
                    positions[sym] = {"side": side, "entry": px, "opened": time.time(),
                                      "why": _why,
                                      "peak": 0.0, "kind": a["kind"],
                                      "conf": round(conf, 3),
                                      "pcal": round(pcal, 4) if pcal is not None else None,
                                      "notional": round(notional, 4),
                                      "risk_usd": round(risk_usd, 4),
                                      "decision": {
                                          "gates": {
                                              "alert": a["kind"],
                                              "conf": round(conf, 4),
                                              "min_conf": args.min_confidence,
                                              "model_gate": mv or "absent",
                                              "liquidity": a.get("metrics", {}).get("quote_volume_24h"),
                                              "liq_floor": args.min_liquidity,
                                              "tech": bool(args.tech_confirm),
                                          },
                                          "votes": {},
                                          "features": {
                                              "taker_buy_ratio": a.get("metrics", {}).get("taker_buy_ratio"),
                                              "range_position": a.get("metrics", {}).get("range_position"),
                                              "burst_multiple": a.get("metrics", {}).get("burst_multiple"),
                                              "burst_z": a.get("metrics", {}).get("burst_z"),
                                              "spread_pct": a.get("metrics", {}).get("spread_pct"),
                                              "score": a.get("score"),
                                          },
                                          "pcal": round(pcal, 4) if pcal is not None else None,
                                          "conviction": round(conv_mult, 3) if "conv_mult" in dir() else None,
                                          "size_usd": round(notional, 2),
                                          "risk_usd": round(risk_usd, 2),
                                      }}
                    seen_alerts.add(a.get("event_id"))
                    FUNNEL["opened"] += 1
                    opened_count += 1
                    log(">>> OPEN  %-11s %-4s %-6s conf %3.0f%% (pcal %5.1f%%) @ %-11.8g notional %7.2f risk %5.2f"
                        % (sym, side.upper(), a["kind"],
                           100 * conf, 100 * (pcal or 0), px, notional,
                           notional * args.stop_pct / 100.0))
                if not cand:
                    log("scan %d: no eligible alert (%d open)" % (scans, len(positions)))

        dump_state()
        slack = args.tick - (time.time() - t0)
        if slack > 0:
            time.sleep(slack)

    log("")
    log("=" * 80)
    log("opened %d | closed %d | wins %d | equity %.2f -> %.2f (%+.2f%%)"
        % (opened_count, len(trades), sum(1 for t in trades if t["net_pct"] > 0),
           start_equity, equity, 100.0 * (equity / start_equity - 1.0)))
    for t in trades[-20:]:
        log("  %-11s %-4s %+6.2f%% net  %-9s held %5.0fs"
            % (t["symbol"], t["side"], t["net_pct"], t["reason"], t["held_s"]))
    log("=" * 80)

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
