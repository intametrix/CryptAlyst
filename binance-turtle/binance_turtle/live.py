#!/usr/bin/env python3
"""Binance Turtle detector -- the runnable entry point.

PAPER / DETECTION ONLY.  Public endpoints, no API keys, no orders.  It scans
the liquid USDT universe, applies the plan's gates, and writes every setup to
a JSONL ledger so the signals can be compared against a backtest later
(plan s5 "paper phase": run live detection for 4 weeks, log every signal).

    python3 -m binance_turtle.live --once --top 30
    python3 -m binance_turtle.live --interval 900 --top 60
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from .binance_api import BinancePublic
from .config import Config, CONFLICTS
from .strategy import (BTCRegime, SymbolState, correlation_clusters,
                       evaluate_entry, size_unit)

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("%s  %s" % (stamp, msg), flush=True)


def pct(x, digits=2):
    return "n/a" if x is None else ("%." + str(digits) + "f%%") % (x * 100.0)


def load_universe(api, cfg, top, include_stables=False):
    rows = api.universe(cfg, include_stables=include_stables)
    return rows[:top]


def prepare(api, cfg, symbol, daily_bars=400, trigger_bars=1500):
    """Daily (primary) + 4h (trigger) klines for one symbol."""
    now = int(time.time() * 1000)
    start_d = now - daily_bars * DAY_MS
    start_t = now - trigger_bars * 4 * HOUR_MS
    daily = api.klines_range(symbol, cfg.primary_interval, start_d, now)
    trigger = api.klines_range(symbol, cfg.trigger_interval, start_t, now)
    # the live bar is incomplete -- only completed bars may signal
    if daily:
        daily = [b for b in daily if b["close_time"] < now]
    if trigger:
        trigger = [b for b in trigger if b["close_time"] < now]
    return daily, trigger


def scan(api, cfg, top, ledger_path, include_stables=False, quiet=False,
         explain=False):
    now = int(time.time() * 1000)
    result = {
        "time": now,
        "universe": [],
        "setups": [],
        "btc": {},
        "errors": [],
        "weight": None,
    }

    universe = load_universe(api, cfg, top, include_stables=include_stables)
    log("universe: %d symbols pass the $%.0fM / 24h screen"
        % (len(universe), cfg.min_quote_volume_24h / 1e6))

    # ---- BTC regime gate (answers s4) --------------------------------
    btc = None
    try:
        btc_daily, _ = prepare(api, cfg, cfg.btc_symbol, daily_bars=400, trigger_bars=200)
        btc = SymbolState(cfg.btc_symbol, btc_daily, None, cfg)
        regime = BTCRegime(cfg)
        risk_off = regime.update(btc_daily)
        de_risk, rv = regime.derisk(btc_daily)
        result["btc"] = {
            "risk_off": risk_off,
            "realized_vol_20d": rv,
            "de_risk": de_risk,
            "last_close": btc_daily[-1]["close"] if btc_daily else None,
        }
        log("BTC regime: %s | 20d realized vol %s | de-risk %s"
            % ("RISK-OFF (no new alt longs)" if risk_off else "risk-on",
               pct(rv, 0), "ON" if de_risk else "off"))
    except Exception as exc:
        result["errors"].append({"symbol": cfg.btc_symbol, "error": str(exc)})
        log("BTC regime unavailable: %s" % exc)
        risk_off, de_risk = False, False

    # ---- per-symbol indicator prep -----------------------------------
    states = {}
    for row in universe:
        sym = row["symbol"]
        try:
            daily, trigger = prepare(api, cfg, sym)
            st = SymbolState(sym, daily, trigger, cfg)
            if st.ok:
                states[sym] = st
                st.universe_row = row
        except Exception as exc:
            result["errors"].append({"symbol": sym, "error": str(exc)})
    log("prepared %d/%d symbols with full indicator history"
        % (len(states), len(universe)))

    clusters, cluster_list = correlation_clusters(states, cfg)
    log("correlation clusters: %d" % len(cluster_list))

    # ---- evaluate both layers ----------------------------------------
    setups = []
    rejected = 0
    diagnostics = []
    for sym, st in states.items():
        for system in ("primary", "trigger"):
            try:
                setup, rejects = evaluate_entry(
                    st, system, cfg,
                    btc_risk_off=risk_off,
                    onset_veto=False,       # flow.py onset layer not present
                    funding_veto=False,     # fapi context fetched separately
                )
            except Exception as exc:
                result["errors"].append({"symbol": sym, "error": str(exc)})
                continue
            if explain:
                level = (st.last(st.tc_up) if system == "trigger"
                         else st.last(st.dc_up))
                close = (st.last(st.t_close) if system == "trigger"
                         else st.last(st.d_close))
                diagnostics.append(
                    "%-12s %-8s close=%-12.8g level=%-12.8g atr%%=%-7s %s"
                    % (sym, system, close or 0, level or 0,
                       pct(st.atr_pct_now()), "; ".join(rejects) or "SETUP"))
            if setup:
                row = st.universe_row
                setup.order_qty_hint, notes = size_unit(
                    10_000.0, setup.atr, setup.price,
                    api.exchange_info(use_cache=True)["symbols"]
                       .get(sym, {}).get("lot_step") or 0.0,
                    st.median_hourly_quote_volume(), cfg,
                    risk_per_unit=(cfg.derisk_risk_per_unit if de_risk
                                   else cfg.risk_per_unit))
                setup.notional = notes.get("notional")
                d = setup.as_dict()
                d["cluster"] = clusters.get(sym)
                d["quote_volume_24h"] = row["quote_volume_24h"]
                d["size_notes"] = notes
                setups.append(d)
            else:
                rejected += 1

    setups.sort(key=lambda s: -s["score"])
    result["setups"] = setups
    result["universe"] = [{"symbol": r["symbol"],
                           "quote_volume_24h": r["quote_volume_24h"]} for r in universe]
    result["weight"] = api.budget.used()
    result["rejected_evaluations"] = rejected

    if ledger_path:
        os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
        with open(ledger_path, "a") as fh:
            fh.write(json.dumps({
                "ts": now,
                "iso": datetime.now(timezone.utc).isoformat(),
                "btc": result["btc"],
                "setups": setups,
                "universe_size": len(universe),
            }) + "\n")

    if not quiet:
        print("")
        if explain:
            print("GATE DIAGNOSTICS (%d symbol/system evaluations)" % len(diagnostics))
            print("-" * 96)
            for line in diagnostics:
                print(line)
            print("-" * 96)
        print("%-12s %-8s %-6s %-10s %-9s %-8s %-9s" %
              ("SYMBOL", "SYSTEM", "SCORE", "PRICE", "ATR%", "VOLx", "STOP"))
        print("-" * 76)
        for s in setups:
            print("%-12s %-8s %-6.3f %-10.8g %-9s %-8s %-9.8g" % (
                s["symbol"], s["system"], s["score"], s["price"],
                pct(s["atr_pct"]), s["vol_mult"], s["stop"]))
        print("-" * 76)
        print("setups: %d   rejected evaluations: %d   request weight used: %d/min"
              % (len(setups), rejected, result["weight"]))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Binance Turtle detector (paper)")
    ap.add_argument("--top", type=int, default=40,
                    help="how many top-volume symbols to scan")
    ap.add_argument("--once", action="store_true", help="single scan then exit")
    ap.add_argument("--interval", type=int, default=900,
                    help="seconds between scans in loop mode")
    ap.add_argument("--cache", default="cache", help="klines cache directory")
    ap.add_argument("--ledger", default="ledger/signals.jsonl",
                    help="JSONL ledger of every scan")
    ap.add_argument("--state", default="state/detector_state.json")
    ap.add_argument("--include-stables", action="store_true")
    ap.add_argument("--explain", action="store_true",
                    help="print per-symbol gate diagnostics (why a setup was rejected)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    cfg = Config()
    if not args.quiet:
        log("Binance Turtle detector v0.1 -- PAPER ONLY (no keys, no orders)")
        for k, v in CONFLICTS.items():
            log("  plan/answers conflict resolved: %s -- %s" % (k, v))

    api = BinancePublic(cache_dir=args.cache, verbose=not args.quiet)
    try:
        api.ping()
    except Exception as exc:
        log("FATAL: cannot reach Binance public API: %s" % exc)
        return 2

    scan_count = 0
    while True:
        scan_count += 1
        try:
            scan(api, cfg, args.top, args.ledger, args.include_stables, args.quiet,
                 explain=args.explain)
        except KeyboardInterrupt:
            log("stopped by user")
            return 0
        except Exception as exc:
            log("scan failed: %r" % (exc,))
        state = {"scans": scan_count, "updated": time.time(),
                 "weight_used": api.budget.used(), "stats": api.stats}
        os.makedirs(os.path.dirname(os.path.abspath(args.state)), exist_ok=True)
        with open(args.state, "w") as fh:
            json.dump(state, fh, indent=2)
        if args.once:
            return 0
        log("sleeping %ds until the next scan" % args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
