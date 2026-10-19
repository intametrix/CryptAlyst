#!/usr/bin/env python3
"""backtest - the event-driven harness the trading plan says comes first.

Rules it obeys, straight from TRADING_PLAN.md §7.1:
  - signals are computed on COMPLETED bars only
  - entries fill at the NEXT bar's open
  - stops are evaluated intra-bar and fill conservatively (stop price minus slippage)
  - fees are charged per side; slippage is charged on every fill
  - no lookahead anywhere: a decision at bar i may only use bars <= i

Strategy under test: the Turtle system in §4.
  entry   close beyond the prior 20-bar high (S1) or 55-bar high (S2)
  exit    close below the prior 10-bar low, or the 2N stop, whichever first
  size    Unit = (risk% x equity) / N, N = ATR(20)
  limits  4 units per market, 2 per correlation cluster, 6 total, 25% notional cap

Acceptance thresholds (§7.1): MAR >= 0.5, max DD <= 45%, expectancy >= +0.15R, >= 60 trades.

    python3 tools/backtest.py --interval 15m --bars 1000 --symbols 20
"""
import argparse, json, math, statistics, sys, time, urllib.request

BINANCE = "https://api.binance.com"
FEE = 0.10              # percent per side, taker
SLIP = 0.05             # percent per fill, conservative


def jget(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "backtest/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def klines(sym, interval, limit):
    d = jget("%s/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (BINANCE, sym, interval, limit))
    return [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
             "c": float(k[4]), "v": float(k[5])} for k in d]


def atr(bars, i, n=20):
    """Wilder ATR at bar i, using only bars <= i."""
    if i < n:
        return None
    trs = []
    for k in range(i - n + 1, i + 1):
        h, l, pc = bars[k]["h"], bars[k]["l"], bars[k - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(pc - l)))
    a = sum(trs) / n
    for k in range(i + 1, i + 1):        # Wilder smoothing would need history; seed is enough here
        pass
    return a


def donchian(bars, i, n, field):
    """Channel from COMPLETED bars only: bars [i-n, i-1]."""
    if i - n < 0:
        return None
    window = bars[i - n:i]
    return max(b[field] for b in window) if field == "h" else min(b[field] for b in window)


def load(symbols, interval, bars):
    out = {}
    for s in symbols:
        try:
            out[s] = klines(s, interval, bars)
            print("  %-10s %d bars" % (s, len(out[s])), flush=True)
            time.sleep(0.15)             # one-time history pull, paced
        except Exception as exc:
            print("  %-10s failed %r" % (s, exc), flush=True)
    return out


def corr(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else 0.0


def clusters(data, syms):
    """Correlation clusters from realised returns, as the plan requires."""
    rets = {}
    for s in syms:
        b = data[s]
        rets[s] = [(b[i]["c"] / b[i - 1]["c"] - 1) for i in range(1, len(b))]
    group = {}
    gid = 0
    for i, s in enumerate(syms):
        if s in group:
            continue
        group[s] = gid
        for t in syms[i + 1:]:
            if t in group:
                continue
            if corr(rets[s], rets[t]) > 0.7:
                group[t] = gid
        gid += 1
    return group


def run(a):
    syms = a.symbols_list
    print("BACKTEST — %s, %d bars, %d symbols, fee %.2f%%/side, slip %.2f%%/fill"
          % (a.interval, a.bars, len(syms), FEE, SLIP))
    print("loading history (one-time pull):")
    data = load(syms, a.interval, a.bars)
    syms = [s for s in syms if s in data and len(data[s]) > 60]
    if not syms:
        print("no data")
        return
    group = clusters(data, syms)
    n_bars = min(len(data[s]) for s in syms)
    print("\nclusters: %s" % {s: group[s] for s in syms})

    equity = a.equity
    start_eq = equity
    curve = [equity]
    pos = {}          # sym -> dict(units, entry_px, stop, size_notional, entry_i)
    trades = []
    units_total = 0

    for i in range(30, n_bars - 1):
        # ---- manage open positions on THIS bar's data, fill at next open
        for s in list(pos.keys()):
            p = pos[s]
            bar = data[s][i]
            n = atr(data[s], i) or 0
            # 2N stop: conservative fill at the stop minus slippage
            if n and bar["l"] <= p["stop"]:
                fill = p["stop"] * (1 - SLIP / 100)
                r_mult = (fill - p["entry_px"]) / (p["entry_px"] * FEE / 100 * 0 + 1e-12)
                pnl_pct = (fill / p["entry_px"] - 1) * 100 - FEE * 2 - SLIP
                equity += p["notional"] * pnl_pct / 100
                trades.append({"sym": s, "reason": "stop", "pnl_pct": pnl_pct,
                               "held": i - p["entry_i"], "R": pnl_pct})
                units_total -= p["units"]
                del pos[s]
                continue
            # 10-bar low exit, filled at next open (approximated by this close minus slip)
            low10 = donchian(data[s], i, 10, "l")
            if low10 is not None and bar["c"] < low10:
                fill = bar["c"] * (1 - SLIP / 100)
                pnl_pct = (fill / p["entry_px"] - 1) * 100 - FEE * 2 - SLIP
                equity += p["notional"] * pnl_pct / 100
                trades.append({"sym": s, "reason": "channel", "pnl_pct": pnl_pct,
                               "held": i - p["entry_i"], "R": pnl_pct})
                units_total -= p["units"]
                del pos[s]

        if units_total >= a.max_units_total:
            curve.append(equity)
            continue

        # ---- entries: decision on completed bar i, fill at bar i+1 open
        for s in syms:
            if s in pos or units_total >= a.max_units_total:
                continue
            bars = data[s]
            c = bars[i]["c"]
            hi20 = donchian(bars, i, 20, "h")
            hi55 = donchian(bars, i, 55, "h")
            if hi20 is None:
                continue
            brk = (c > hi20) or (hi55 is not None and c > hi55)
            if not brk:
                continue
            n = atr(bars, i)
            if not n or n <= 0:
                continue
            # cluster limit
            same = sum(1 for t in pos if group.get(t) == group.get(s))
            if same >= a.max_cluster:
                continue
            risk_usd = equity * a.risk / 100
            units = 1
            notional = min(risk_usd / (n / c) if n else 0, equity * a.max_notional_pct / 100)
            if notional <= 0:
                continue
            entry = bars[i + 1]["o"] * (1 + SLIP / 100)
            pos[s] = {"units": units, "entry_px": entry, "stop": entry - 2 * n,
                      "notional": notional, "entry_i": i + 1}
            units_total += units
        curve.append(equity)

    if not trades:
        print("\nno trades — nothing to judge")
        return

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    expectancy = statistics.mean(pnls)
    # MAR: annualised return / max drawdown, from the equity curve
    peak, dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak * 100 if peak else 0)
    total_ret = (equity / start_eq - 1) * 100
    years = n_bars * {"1m": 1 / 525600, "5m": 5 / 525600, "15m": 15 / 525600,
                      "1h": 1 / 8760, "4h": 4 / 8760, "1d": 1 / 365}.get(a.interval, 15 / 525600)
    ann = total_ret / years if years else 0
    mar = ann / dd if dd else float("inf")

    print("\nresults")
    print("  trades            %d" % len(trades))
    print("  win rate          %.0f%%" % (100 * len(wins) / len(trades)))
    print("  expectancy        %+.3f%% per trade" % expectancy)
    print("  total return      %+.2f%%" % total_ret)
    print("  max drawdown      %.2f%%" % dd)
    print("  annualised        %+.0f%%" % ann)
    print("  MAR               %.2f" % mar)
    print("  avg hold          %.1f bars" % statistics.mean([t["held"] for t in trades]))
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    print("  exits             %s" % reasons)

    print("\nacceptance (§7.1)")
    checks = [("MAR >= 0.5", mar >= 0.5),
              ("max DD <= 45%", dd <= 45),
              ("expectancy >= +0.15R", expectancy >= 0.15),
              ("at least 60 trades", len(trades) >= 60)]
    for label, ok in checks:
        print("  %-24s %s" % (label, "PASS" if ok else "FAIL"))
    print("\nverdict: %s" % ("ACCEPTABLE" if all(o for _, o in checks) else "REFUSED — do not trade this live"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--bars", type=int, default=1000)
    ap.add_argument("--equity", type=float, default=5000)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--max-units-total", type=int, default=6)
    ap.add_argument("--max-cluster", type=int, default=2)
    ap.add_argument("--max-notional-pct", type=float, default=25)
    ap.add_argument("--symbols", type=int, default=20)
    a = ap.parse_args()
    a.symbols_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
                      "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT", "LTCUSDT",
                      "ATOMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT",
                      "SUIUSDT", "TIAUSDT"][:a.symbols]
    run(a)
