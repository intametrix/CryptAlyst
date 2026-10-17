#!/usr/bin/env python3
"""Event-driven backtest for the crypto Turtle system (plan section 5).

Rules implemented (KIMI_TRADING_PLAN.md / KIMI_ANSWERS2.md):
  entry  : close > prior 55-day high, volume confirmed (>=1.5x 20-bar median,
           >=$150k absolute), ATR%(20,1d) inside 1.5-25%, no blow-off
           (RSI>85 and price>1.6x SMA200), BTC risk-on
  exits  : 2N stop (never widened), 20-bar channel close-through, 10-bar time
           stop when flat, ratchet = max(stop, channel) never retreating
  sizing : 0.5% equity risk per unit, N = ATR(20,1d)
  costs  : 0.1% taker per side, 0.1% entry slippage, 0.15% stop slippage

No lookahead: every decision uses bars up to and including i; the fill happens
at the OPEN of i+1. Indicators are computed on closed bars only.

Usage:
    python3 backtest.py --symbols 40 --start 2019-01-01
"""

import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from binance_turtle import indicators as ta
from binance_turtle.binance_api import BinancePublic
from binance_turtle.config import Config

DAY = 86_400_000


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def load_universe(api, cfg, count, start_ms, explicit=None, min_bars=0):
    if explicit:
        syms = [x.strip().upper() for x in explicit if x.strip()]
    else:
        rows = api.universe(cfg)
        syms = [r["symbol"] for r in rows[:count]]
    if cfg.btc_symbol not in syms:
        syms.append(cfg.btc_symbol)
    data = {}
    for i, s in enumerate(syms):
        try:
            bars = api.klines_range(s, "1d", start_ms, int(time.time() * 1000))
            bars = [b for b in bars if b["close_time"] < int(time.time() * 1000)]
            if len(bars) >= max(200, min_bars):
                data[s] = bars
            elif min_bars:
                print("  - %s skipped: %d bars < %d required" % (s, len(bars), min_bars))
        except Exception as exc:
            print("  ! %s: %r" % (s, exc))
        if (i + 1) % 10 == 0:
            print("  fetched %d/%d" % (i + 1, len(syms)), flush=True)
    return data


def prepare(bars, cfg):
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]
    v = [b["quote_volume"] for b in bars]
    atr = ta.atr_wilder(h, l, c, cfg.atr_period)
    atr_pct = [(atr[i] / c[i]) if atr[i] and c[i] else None for i in range(len(c))]
    up55, _ = ta.donchian_prior(h, l, cfg.primary_entry_bars)
    _, exit_low = ta.donchian_prior(h, l, cfg.primary_exit_bars)
    rsi = ta.rsi_wilder(c, cfg.rsi_period)
    sma200 = ta.sma(c, cfg.ma_long_period)
    return {"bars": bars, "h": h, "l": l, "c": c, "v": v, "atr": atr,
            "atr_pct": atr_pct, "up55": up55, "exit_low": exit_low,
            "rsi": rsi, "sma200": sma200}


def btc_gate(btc, cfg):
    """Latched risk-off: below 55d low blocks new longs until above 55d high."""
    up, dn = ta.donchian_prior(btc["h"], btc["l"], cfg.btc_donchian_bars)
    risk_off = False
    flags = []
    for i in range(len(btc["c"])):
        if up[i] is None or dn[i] is None:
            flags.append(False)
            continue
        if not risk_off and btc["c"][i] < dn[i]:
            risk_off = True
        elif risk_off and btc["c"][i] > up[i]:
            risk_off = False
        flags.append(risk_off)
    return flags


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------
def run_backtest(data, cfg, start_equity=10_000.0, bracket=None):
    prepped = {}
    for s, b in data.items():
        p = prepare(b, cfg)
        if p["atr"][-1] is not None:
            prepped[s] = p

    btc = prepped.get(cfg.btc_symbol)
    gate = btc_gate(btc, cfg) if btc else []
    btc_times = [b["open_time"] for b in btc["bars"]] if btc else []
    btc_index = {t: i for i, t in enumerate(btc_times)}

    events = []
    for s, p in prepped.items():
        for i, b in enumerate(p["bars"]):
            events.append((b["open_time"], s, i))
    events.sort()

    equity = start_equity
    peak = equity
    max_dd = 0.0
    curve = []
    positions = {}
    trades = []
    pending = {}

    def close_pos(sym, px, ts, reason, i):
        nonlocal equity, peak, max_dd
        p = positions.pop(sym)
        # a stop that has ratcheted above the entry is a trailing stop, not a
        # loss -- reporting both as "stop" hides which half of the edge works
        if reason == "stop" and px > p["entry"]:
            reason = "trail_stop"
        gross = (px - p["entry"]) * p["qty"]
        cost = (p["entry"] + px) * p["qty"] * cfg.taker_fee
        pnl = gross - cost
        equity += pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak else 0.0
        max_dd = max(max_dd, dd)
        if bracket:
            r = (px / p["entry"] - 1.0) / bracket[1]
        else:
            r = (px - p["entry"]) / (2.0 * p["n"]) if p["n"] else 0.0
        trades.append({
            "symbol": sym, "entry_ts": p["ts"], "exit_ts": ts,
            "entry": p["entry"], "exit": px, "qty": p["qty"],
            "bars": i - p["i"], "reason": reason,
            "r": round(r - 2.0 * cfg.taker_fee / (2.0 * p["n"] / p["entry"]), 3)
                 if p["n"] else 0.0,
            "pnl": round(pnl, 2),
        })
        curve.append((ts, equity))

    for ts, sym, i in events:
        p = prepped[sym]
        bar = p["bars"][i]

        # --- pending entry fills at this bar's open -----------------------
        if sym in pending:
            e = pending.pop(sym)
            entry = bar["open"] * (1.0 + cfg.entry_slippage)
            positions[sym] = {"entry": entry, "qty": e["qty"], "n": e["n"],
                              "stop": entry - cfg.stop_atr_mult * e["n"],
                              "ts": ts, "i": i,
                              "channel": e["channel"]}
            # bracket mode: the entry bar must obey the SAME -sl% rule, not the
            # 2N stop -- mixing them booked a wide 2N gap as -6R on a 2% bracket
            if bracket:
                stop_px = entry * (1.0 - bracket[1])
                if bar["low"] <= stop_px:
                    px = min(stop_px, bar["open"])
                    close_pos(sym, px, ts, "bracket_stop_entry_bar", i)
                continue
            # stop may already be hit inside the entry bar
            if bar["low"] <= positions[sym]["stop"]:
                px = min(positions[sym]["stop"], bar["open"]) * (1.0 - cfg.stop_slippage)
                close_pos(sym, px, ts, "stop_same_bar", i)
                pending_skip = True
            else:
                pending_skip = False
            if not pending_skip:
                continue
            else:
                continue

        pos = positions.get(sym)
        if pos is not None and bracket:
            tp, sl = bracket
            hit_sl = bar["low"] <= pos["entry"] * (1.0 - sl)
            hit_tp = bar["high"] >= pos["entry"] * (1.0 + tp)
            if hit_sl:
                close_pos(sym, pos["entry"] * (1.0 - sl), ts, "bracket_stop", i)
                continue
            if hit_tp:
                close_pos(sym, pos["entry"] * (1.0 + tp), ts, "bracket_target", i)
                continue
            continue

        if pos is not None:
            # 1. intra-bar stop against the stop set at the previous close
            if bar["low"] <= pos["stop"]:
                px = min(pos["stop"], bar["open"]) * (1.0 - cfg.stop_slippage)
                close_pos(sym, px, ts, "stop", i)
                continue
            # 2. channel exit on the close of this bar -> exit next open
            ch = p["exit_low"][i]
            if ch is not None and bar["close"] < ch:
                nxt = p["bars"][i + 1]["open"] if i + 1 < len(p["bars"]) else bar["close"]
                close_pos(sym, nxt * (1.0 - cfg.entry_slippage), ts, "channel", i)
                continue
            # 3. time stop: flat (+/-0.5N) after 10 bars
            if (i - pos["i"]) >= cfg.time_stop_bars:
                move = (bar["close"] - pos["entry"]) / pos["n"] if pos["n"] else 0
                if abs(move) <= cfg.time_stop_band_atr:
                    nxt = p["bars"][i + 1]["open"] if i + 1 < len(p["bars"]) else bar["close"]
                    close_pos(sym, nxt * (1.0 - cfg.entry_slippage), ts, "time_stop", i)
                    continue
            # 4. ratchet
            if ch is not None and ch > pos["stop"]:
                pos["stop"] = ch
            continue

        # --- flat: look for an entry signal on this closed bar ------------
        if len(positions) >= cfg.max_units_total:
            continue
        if p["up55"][i] is None or p["atr"][i] is None:
            continue
        if bar["close"] <= p["up55"][i]:
            continue
        ok, _ = ta.volume_confirmed(p["v"], i, cfg)
        if not ok:
            continue
        ap = p["atr_pct"][i]
        if ap is None or ap < cfg.atr_pct_min or ap > cfg.atr_pct_max:
            continue
        if p["rsi"][i] and p["sma200"][i]:
            if p["rsi"][i] > cfg.blowoff_rsi and bar["close"] > cfg.blowoff_ma_mult * p["sma200"][i]:
                continue
        if btc and btc_index:
            j = btc_index.get(ts)
            if j is None:
                continue
            if j < len(gate) and gate[j]:
                continue
        n = p["atr"][i]
        risk_amt = equity * cfg.risk_per_unit
        qty = risk_amt / n
        # notional sanity: never more than equity
        qty = min(qty, equity / bar["close"])
        if qty * bar["close"] < 10:
            continue
        pending[sym] = {"qty": qty, "n": n, "channel": p["exit_low"][i]}

    all_ts = [b["open_time"] for p in prepped.values() for b in p["bars"]]
    span = (min(all_ts), max(all_ts)) if all_ts else None
    return {
        "equity": equity, "start_equity": start_equity,
        "max_dd": max_dd, "trades": trades, "curve": curve, "span": span,
    }


def metrics(res, cfg):
    tr = res["trades"]
    if not tr:
        return {"trades": 0}
    rs = [t["r"] for t in tr]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    total_r = sum(rs)
    exp_r = total_r / len(rs)
    win_rate = len(wins) / len(rs)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    start = res["start_equity"]
    end = res["equity"]
    days = 1
    if res.get("span"):
        days = max(1.0, (res["span"][1] - res["span"][0]) / DAY)
    cagr = (end / start) ** (365.0 / max(days, 1)) - 1.0 if end > 0 else -1.0
    return {
        "trades": len(tr),
        "win_rate": round(100.0 * win_rate, 1),
        "expectancy_r": round(exp_r, 3),
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "total_r": round(total_r, 1),
        "max_dd_pct": round(100.0 * res["max_dd"], 1),
        "equity": round(end, 2),
        "return_pct": round(100.0 * (end / start - 1.0), 1),
        "cagr_pct": round(100.0 * cagr, 1),
        "mar": round(cagr / res["max_dd"], 2) if res["max_dd"] else None,
        "days": int(days),
    }


def cfg_start_ts(res):
    return min(t["entry_ts"] for t in res["trades"]) if res["trades"] else int(time.time() * 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=40)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--out", default="backtest_result.json")
    ap.add_argument("--list", default="", help="explicit comma-separated symbols")
    ap.add_argument("--bracket", default="", help="tp,sl percentages, e.g. 6,2")
    ap.add_argument("--min-bars", type=int, default=0,
                    help="drop symbols with less history than this (kills new listings)")
    args = ap.parse_args()

    cfg = Config()
    api = BinancePublic(cache_dir=args.cache, verbose=False)

    start_ms = int(time.mktime(time.strptime(args.start, "%Y-%m-%d")) * 1000)
    print("Turtle crypto backtest || universe %d USDT pairs >= $%.0fM | from %s"
          % (args.symbols, cfg.min_quote_volume_24h / 1e6, args.start))
    print("rules: 55d breakout + volume confirm + ATR band + BTC gate | 2N stop | 20d channel exit")
    print("costs: 0.1%% taker x2 + entry/stop slippage\n")

    data = load_universe(api, cfg, args.symbols, start_ms,
                         explicit=[x for x in args.list.split(",") if x.strip()],
                         min_bars=args.min_bars)
    print("\nloaded %d symbols with history" % len(data))

    t0 = time.time()
    bracket = None
    if args.bracket:
        a, b = args.bracket.split(",")
        bracket = (float(a) / 100.0, float(b) / 100.0)
        print("EXIT MODE: fixed bracket +%.1f%% / -%.1f%%" % (float(a), float(b)))
    res = run_backtest(data, cfg, start_equity=args.equity, bracket=bracket)
    m = metrics(res, cfg)
    print("sim finished in %.1fs" % (time.time() - t0))
    print()
    print("=" * 72)
    for k in ("trades", "win_rate", "expectancy_r", "total_r", "max_dd_pct",
              "equity", "return_pct", "cagr_pct", "mar", "days"):
        if k in m:
            print("  %-14s %s" % (k, m[k]))
    print("=" * 72)
    print()
    by_reason = {}
    for t in res["trades"]:
        by_reason.setdefault(t["reason"], []).append(t["r"])
    print("by exit:")
    for reason, rs in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print("  %-14s n=%-4d avg R %+.3f  total R %+.1f"
              % (reason, len(rs), sum(rs) / len(rs), sum(rs)))

    # regime breakdown: the plan demands it, because an edge that lives in one
    # period is not an edge
    by_year = {}
    for t in res["trades"]:
        y = time.strftime("%Y", time.gmtime(t["entry_ts"] / 1000))
        by_year.setdefault(y, []).append(t["r"])
    print()
    print("by year:")
    for y in sorted(by_year):
        rs = by_year[y]
        print("  %s  n=%-3d  avg R %+.3f  total R %+.1f  win %.0f%%"
              % (y, len(rs), sum(rs) / len(rs), sum(rs),
                 100.0 * sum(1 for r in rs if r > 0) / len(rs)))

    with open(args.out, "w") as fh:
        json.dump({"metrics": m, "trades": res["trades"][-400:]}, fh, indent=2)
    print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
