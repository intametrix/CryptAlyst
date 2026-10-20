#!/usr/bin/env python3
"""session_monitor - a line every few minutes on how the session is actually doing.

    python3 tools/session_monitor.py --every 300
"""
import argparse, glob, json, os, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")
OUT = os.path.join(HERE, "logs", "session_monitor.log")


def books():
    rows, eq, start, pos, closed, wins, losses = [], 0.0, 0.0, 0, 0, 0, 0
    for f in glob.glob(os.path.join(STATE, "pack_*.json")):
        try:
            b = json.load(open(f))
        except Exception:
            continue
        e = float(b.get("equity") or 0)
        s = float(b.get("start_equity") or 0)
        eq += e
        start += s
        pos += len(b.get("positions") or [])
        trades = b.get("trades") or []
        closed += len(trades)
        for t in trades:
            if (t.get("net_pct") or 0) >= 0:
                wins += 1
            else:
                losses += 1
        rows.append(b.get("engine"))
    return eq, start, pos, closed, wins, losses, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=300)
    a = ap.parse_args()
    while True:
        eq, start, pos, closed, wins, losses, n = books()
        pnl = eq - start
        line = "%s  books %d | equity $%.2f | pnl %+.2f (%+.3f%%) | open %d | closed %d (w %d / l %d)" % (
            time.strftime("%H:%M:%S"), n, eq, pnl, (100 * pnl / start if start else 0),
            pos, closed, wins, losses)
        print(line, flush=True)
        with open(OUT, "a") as fh:
            fh.write(line + "\n")
        time.sleep(a.every)


if __name__ == "__main__":
    main()
