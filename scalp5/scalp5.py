#!/usr/bin/env python3
"""5-second round-trip scalper -- PAPER ONLY, fee-aware, no keys, no orders.

Signal source: the user's own running pumpdump backend (`/api/flow`), the same
1-second flow engine behind the dashboard. Prices for fills come from Binance
public REST `/api/v3/ticker/price`, because /api/flow only carries the top ~25
rows and a symbol drops out of it constantly.

NO REJECTIONS (by request): every cycle takes a trade. Candidate selection only
*ranks*; it never vetoes. Spot cannot short, so a row drawn to signal a dump is
still entered long -- that is a real limitation of the venue, not a filter being
sneaky about it.

Every trade books:
    gross_bps  = raw 5s mid move
    fees_bps   = 2 x taker fee (in and out)
    spread_bps = spread crossed at entry + exit
    net_bps    = gross - fees - spread          <-- the only number that matters

Usage:
    python3 scalp5.py --seconds 120 --hold 5
"""

import argparse
import json
import os
import statistics as stats
import time
import urllib.request
from datetime import datetime, timezone

FLOW_URL = "http://127.0.0.1:8787/api/flow"
PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol="


def log(msg):
    print("%s  %s" % (datetime.now(timezone.utc).strftime("%H:%M:%S"), msg), flush=True)


def fetch_rows(timeout=3.0):
    with urllib.request.urlopen(FLOW_URL, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")).get("rows") or []


def live_price(symbol, timeout=3.0):
    """Authoritative last price for the fill, independent of the flow table."""
    with urllib.request.urlopen(PRICE_URL + symbol, timeout=timeout) as resp:
        return float(json.loads(resp.read().decode("utf-8"))["price"])


def pick(rows):
    """Rank only. Never vetoes: an empty-ish table still yields the top row.

    Long-only reality: rows with direction +1 are preferred; if every row is
    signalling a dump, the most liquid row is taken long anyway so the cycle
    still trades.
    """
    if not rows:
        return None, 0.0
    longs = [r for r in rows if (r.get("direction") or 0) == 1]
    pool = longs if longs else rows
    best, best_score = None, None
    for r in pool:
        chg = abs(r.get("change_5s") or 0.0)
        score = (r.get("burst_multiple") or 1.0) * (1.0 + 200.0 * chg)
        if best_score is None or score > best_score:
            best, best_score = r, score
    return best, (best_score or 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--hold", type=float, default=5.0)
    ap.add_argument("--fee", type=float, default=0.001, help="taker fee per side")
    ap.add_argument("--ledger", default="ledger/scalp5.jsonl")
    args = ap.parse_args()

    log("scalp5 -- PAPER ONLY (no keys, no orders). hold=%ss fee=%s/side, NO REJECTIONS"
        % (args.hold, args.fee))

    trades = []
    started = time.time()
    next_entry = started

    while time.time() - started < args.seconds:
        now = time.time()
        if now < next_entry:
            time.sleep(min(1.0, next_entry - now))
            continue

        try:
            rows = fetch_rows()
        except Exception as exc:
            log("flow fetch failed: %r" % (exc,))
            time.sleep(1)
            continue

        row, score = pick(rows)
        if row is None:
            next_entry = now + args.hold
            continue

        sym = row["symbol"]
        spread = row.get("spread_pct") or 0.0
        t0 = time.time()
        try:
            entry = live_price(sym)
        except Exception as exc:
            log("entry price failed for %s: %r" % (sym, exc))
            next_entry = t0 + args.hold
            continue

        time.sleep(max(0.0, args.hold - (time.time() - t0)))
        try:
            exit_px = live_price(sym)
        except Exception as exc:
            log("exit price failed for %s: %r" % (sym, exc))
            next_entry = t0 + args.hold
            continue

        gross_bps = (exit_px / entry - 1.0) * 1e4
        fees_bps = 2.0 * args.fee * 1e4
        spread_bps = spread * 1e4
        net_bps = gross_bps - fees_bps - spread_bps

        rec = {
            "ts": int(t0 * 1000), "symbol": sym,
            "entry": entry, "exit": exit_px, "hold_s": args.hold,
            "spread_pct": spread,
            "gross_bps": round(gross_bps, 3),
            "fees_bps": round(fees_bps, 3),
            "spread_bps": round(spread_bps, 3),
            "net_bps": round(net_bps, 3),
            "burst_multiple": round(row.get("burst_multiple") or 0, 2),
            "direction": row.get("direction"),
            "score": round(score, 3),
        }
        trades.append(rec)
        log("%-11s %+7.2f gross  -%5.2f fees  -%5.2f spread  = %+7.2f net bps   (burst %.1fx dir %+d)"
            % (sym, gross_bps, fees_bps, spread_bps, net_bps,
               rec["burst_multiple"], rec["direction"] or 0))
        next_entry = t0 + args.hold

    if not trades:
        log("no trades recorded")
        return

    gross = [t["gross_bps"] for t in trades]
    net = [t["net_bps"] for t in trades]
    wins = sum(1 for n in net if n > 0)

    log("")
    log("=" * 80)
    log("trades %d   elapsed %.0fs   hold %ss   fee %.3f%%/side"
        % (len(trades), time.time() - started, args.hold, args.fee * 100))
    log("gross/trade : mean %+.2f bps   median %+.2f bps   best %+.2f   worst %+.2f"
        % (stats.mean(gross), stats.median(gross), max(gross), min(gross)))
    log("net  /trade : mean %+.2f bps   median %+.2f bps"
        % (stats.mean(net), stats.median(net)))
    log("wins %d/%d (%.0f%%)  -- a win means the 5s move BEAT the 20bps fee"
        % (wins, len(trades), 100.0 * wins / len(trades)))
    log("cumulative net: %+.1f bps = %+.2f%% of equity"
        % (sum(net), sum(net) / 100.0))
    if sum(gross):
        log("fees+spread ate %.0f%% of the gross move captured"
            % (100.0 * (1 - sum(net) / sum(gross))))
    log("=" * 80)

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    with open(args.ledger, "a") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    main()
