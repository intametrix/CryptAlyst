#!/usr/bin/env python3
"""status - one screen on how the fleet is doing. Safe to run any time."""
import json, os, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")


def load(fn, default):
    try:
        with open(os.path.join(STATE, fn)) as fh:
            return json.load(fh)
    except Exception:
        return default


def main():
    sup = load("supervisor.json", {})
    dis = set(load("disabled.json", {}).get("books") or [])
    books = []
    for fn in sorted(os.listdir(STATE)):
        if fn.startswith("pack_") and fn.endswith(".json"):
            try:
                with open(os.path.join(STATE, fn)) as fh:
                    books.append(json.load(fh))
            except Exception:
                pass
    equity = sum(b.get("equity") or 0 for b in books)
    start = sum(b.get("start_equity") or 0 for b in books) or 1
    opened = sum(len(b.get("positions") or []) for b in books)
    closed = sum(b.get("trade_count") or 0 for b in books)
    wins = 0
    total = 0
    for b in books:
        for t in b.get("trades") or []:
            total += 1
            if (t.get("net_pct") or 0) > 0:
                wins += 1

    print("CRYPTALYST — %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 68)
    print("equity      $%s   (%+.2f%% from $%s)" % (
        format(round(equity), ","), 100 * (equity / start - 1), format(round(start), ",")))
    print("books       %d   open %d   closed %d   win rate %.0f%%" % (
        len(books), opened, closed, 100 * wins / total if total else 0))
    print("disabled    %s" % (", ".join(sorted(dis)) if dis else "none"))

    ranked = []
    for b in books:
        st = b.get("start_equity") or 5000
        eq = b.get("equity") or st
        ranked.append((b.get("engine"), 100 * (eq / st - 1), len(b.get("positions") or []),
                       b.get("trade_count") or 0, eq))
    ranked.sort(key=lambda r: -r[1])
    print("\nbest")
    for r in ranked[:6]:
        print("  %-16s %+7.2f%%  open %-2d closed %-3d $%.0f" % r)
    print("worst")
    for r in ranked[-6:]:
        print("  %-16s %+7.2f%%  open %-2d closed %-3d $%.0f" % r)

    h = (sup.get("history") or [])
    if h:
        first, last = h[0], h[-1]
        print("\nsupervisor  %d cycles   equity $%s -> $%s (%+.2f%%)" % (
            len(h), format(round(first["equity"]), ","), format(round(last["equity"]), ","),
            100 * (last["equity"] / first["equity"] - 1) if first["equity"] else 0))
        for e in h[-5:]:
            acts = "; ".join(e.get("actions") or [])
            print("  %s  eq $%-9s open %-3s closed %-3s %s" % (
                time.strftime("%H:%M", time.localtime(e["ts"] / 1000)),
                format(round(e["equity"]), ","), e["open"], e["closed"], acts[:60]))


if __name__ == "__main__":
    main()
