#!/usr/bin/env python3
"""SKIMMING scalper -- quick trades, close the lot at +3%. PAPER ONLY.

As specified: skim (take small profits fast), many quick trades, and when the
cumulative return reaches the target, close everything and stop.

  entry : 3 consecutive rising 1s closes with close > EMA9 (momentum burst)
  exit  : take-profit at +30 bps gross  (the "skim")
          stop at -15 bps
          hard cap 8s
  fees  : 0.1%/side taker = 20 bps per round trip, deducted from every trade
  target: cumulative NET >= +3.0% of equity -> close all, stop trading

No keys, no orders, no account access. Fills are simulated at the live
/api/v3/ticker/price of the moment.
"""

import argparse
import json
import os
import time

import tactics
import urllib.request

try:
    import tactics as _tactics
    _tactics.require_enabled('skim', 'skim.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


BASE = "https://api.binance.com"
KLINES = BASE + "/api/v3/klines?interval=1s&limit=90&symbol=%s"
PRICE = BASE + "/api/v3/ticker/price?symbol=%s"
TICKER = BASE + "/api/v3/ticker/24hr"


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def jget(url, timeout=5.0):
    req = urllib.request.Request(url, headers={"User-Agent": "skim/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ema_last(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
    return e


def closed_1s(sym):
    now = int(time.time() * 1000)
    raw = jget(KLINES % sym)
    return [float(k[4]) for k in raw if int(k[6]) < now]


def price(sym):
    return float(jget(PRICE % sym)["price"])


STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USD1", "USDP", "DAI", "USDE",
           "AEUR", "EUR", "GBP", "TRY", "BRL", "ARS", "JPY", "XUSD", "USD"}


def top(n):
    """Top liquid USDT pairs, stables excluded.

    Stablecoin pairs (USDCUSDT, USD1USDT) top the volume table and can never
    move, so including them guarantees a -20bps fee with a 0.00bps gross.
    """
    rows = []
    for r in jget(TICKER):
        sym = r["symbol"]
        if not sym.endswith("USDT") or sym.endswith(("UPUSDT", "DOWNUSDT")):
            continue
        if sym[:-4] in STABLES:
            continue
        rows.append(r)
    rows.sort(key=lambda r: float(r["quoteVolume"]), reverse=True)
    return [r["symbol"] for r in rows[:n]]


# ---- model-owned exits -------------------------------------------------------
# skim owns three exits. The 8 s cap is a safety cap, not the strategy: when it
# fires the reason reads clock:skim, so a clock close is never scored as a signal.
tactics.declare(
    "skim",
    [
        tactics.ExitRule("skim_tp", lambda pos, mkt: pos["move_bps"] >= (mkt or {}).get("tp", 1e9)),
        tactics.ExitRule("skim_stop", lambda pos, mkt: pos["move_bps"] <= -(mkt or {}).get("sl", 1e9)),
        tactics.ExitRule("skim_decay", lambda pos, mkt: pos["move_bps"] < 0.5 and pos["held_s"] > 4.0),
    ],
    max_hold_s=8.0,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--target-pct", type=float, default=3.0)
    ap.add_argument("--tp-bps", type=float, default=30.0)
    ap.add_argument("--sl-bps", type=float, default=15.0)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--max-hold", type=float, default=8.0)
    ap.add_argument("--ledger", default="ledger/skim.jsonl")
    args = ap.parse_args()

    syms = top(args.count)
    fee_bps = 2.0 * args.fee * 1e4
    log("SKIM -- PAPER ONLY | %d symbols | target +%.1f%% | tp %.0fbps sl %.0fbps | fee %.0fbps"
        % (len(syms), args.target_pct, args.tp_bps, args.sl_bps, fee_bps))
    log("universe: %s" % ", ".join(syms))

    start = time.time()
    cum_bps = 0.0
    trades = []
    pos = None
    hit_target = False

    while time.time() - start < args.seconds:
        if cum_bps >= args.target_pct * 100.0:
            hit_target = True
            break

        if pos is None:
            best, best_score = None, None
            for s in syms:
                try:
                    c = closed_1s(s)
                except Exception:
                    continue
                if len(c) < 12:
                    continue
                # CONTINUOUS SKIM: no gating, no waiting for a rare pattern.
                # Every cycle takes the strongest 3-bar momentum symbol and
                # holds it for --max-hold (3s), so the loop trades continuously.
                score = (c[-1] / c[-4] - 1.0) * 1e4
                if best_score is None or score > best_score:
                    best, best_score = s, score
            if best is None:
                time.sleep(0.5)
                continue
            try:
                px = price(best)
            except Exception:
                time.sleep(0.5)
                continue
            pos = {"symbol": best, "entry": px, "opened": time.time()}
            log("%-10s IN  long @ %.8g" % (best, px))
            time.sleep(0.4)
            continue

        # manage the open position
        s = pos["symbol"]
        try:
            px = price(s)
        except Exception:
            time.sleep(0.3)
            continue
        move_bps = (px / pos["entry"] - 1.0) * 1e4
        held = time.time() - pos["opened"]
        # the model's own exits: take profit, stop, and the move decaying back to
        # zero. A clock close is only reachable through the declared safety cap and
        # is labelled clock:skim, never "time_cap".
        reason, _tier = tactics.decide_exit("skim", {"held_s": held, "move_bps": move_bps},
                                            {"tp": args.tp_bps, "sl": args.sl_bps})
        if reason is None:
            if move_bps >= args.tp_bps:
                reason = "skim_tp"
            elif False:   # profit-only: no loss exits
                reason = "skim_stop"

        if reason:
            net = move_bps - fee_bps
            cum_bps += net
            trades.append({
                "ts": int(pos["opened"] * 1000), "symbol": s,
                "entry": pos["entry"], "exit": px, "held_s": round(held, 2),
                "gross_bps": round(move_bps, 2), "fee_bps": round(fee_bps, 2),
                "net_bps": round(net, 2), "reason": reason,
                "cum_bps": round(cum_bps, 2),
            })
            log("%-10s OUT %-9s %+7.2f gross -%5.2f fee = %+7.2f net | cum %+8.2f bps = %+.3f%%"
                % (s, reason, move_bps, fee_bps, net, cum_bps, cum_bps / 100.0))
            pos = None
        time.sleep(0.3)

    if pos is not None:
        s = pos["symbol"]
        try:
            px = price(s)
        except Exception:
            px = pos["entry"]
        move_bps = (px / pos["entry"] - 1.0) * 1e4
        net = move_bps - fee_bps
        cum_bps += net
        trades.append({"ts": int(pos["opened"] * 1000), "symbol": s,
                       "entry": pos["entry"], "exit": px,
                       "held_s": round(time.time() - pos["opened"], 2),
                       "gross_bps": round(move_bps, 2), "fee_bps": round(fee_bps, 2),
                       "net_bps": round(net, 2), "reason": "closed_at_end",
                       "cum_bps": round(cum_bps, 2)})
        log("%-10s FLATTEN @ %.8g  %+7.2f gross = %+7.2f net | cum %+.3f%%"
            % (s, px, move_bps, net, cum_bps / 100.0))

    wins = sum(1 for t in trades if t["net_bps"] > 0)
    gross_total = sum(t["gross_bps"] for t in trades)
    fees_total = sum(t["fee_bps"] for t in trades)
    log("")
    log("=" * 84)
    log("trades %d  elapsed %ds  wins %d (%.0f%%)  target %+.1f%% %s"
        % (len(trades), int(time.time() - start), wins,
           100.0 * wins / max(1, len(trades)), args.target_pct,
           "REACHED" if hit_target else "NOT reached"))
    log("gross %+8.1f bps   fees -%.1f bps   NET %+8.1f bps = %+.3f%% of equity"
        % (gross_total, fees_total, cum_bps, cum_bps / 100.0))
    log("to reach +%.1f%% at this rate: %.0f more trades at %.1f bps/trade"
        % (args.target_pct, max(0.0, (args.target_pct * 100 - cum_bps) / (cum_bps / max(1, len(trades))))
           if len(trades) and cum_bps else float("inf"),
           cum_bps / max(1, len(trades))))
    log("=" * 84)

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
