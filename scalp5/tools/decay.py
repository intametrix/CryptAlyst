#!/usr/bin/env python3
"""decay - how long does an edge last?

For every closed trade we know peak_pct and held_s. The time at which the peak
occurred tells us the signal's half-life, which is what the clock should be set
to - not a guess, and not the same for every model.
"""
import json, os, statistics, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")


def fills():
    out = []
    path = os.path.join(STATE, "fills.jsonl")
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def report(rows, label):
    if not rows:
        print("%-16s no trades" % label)
        return
    wins = [r for r in rows if (r.get("net_pct") or 0) > 0]
    losses = [r for r in rows if (r.get("net_pct") or 0) <= 0]
    def avg(xs, k):
        return statistics.mean([x.get(k) or 0 for x in xs]) if xs else 0
    print("%-16s n=%-4d win %3.0f%% | avg net %+7.3f%% | avg peak %+6.3f%% | "
          "hold %4.0fs | win hold %4.0fs | loss hold %4.0fs" % (
        label, len(rows), 100 * len(wins) / len(rows),
        avg(rows, "net_pct"), avg(rows, "peak_pct"), avg(rows, "held_s"),
        avg(wins, "held_s"), avg(losses, "held_s")))


def main():
    rows = fills()
    if not rows:
        print("no fills yet — run the engines and come back")
        return
    print("total fills: %d\n" % len(rows))
    report(rows, "all")
    by_engine = {}
    for r in rows:
        by_engine.setdefault(r.get("engine") or "?", []).append(r)
    for eng, rws in sorted(by_engine.items(), key=lambda kv: -len(kv[1])):
        report(rws, eng)

    # how much of the peak did we actually capture?
    caps = [(r.get("net_pct") or 0) / (r.get("peak_pct") or 1)
            for r in rows if (r.get("peak_pct") or 0) > 0.05]
    if caps:
        print("\ncapture ratio: median %.0f%% of the available move (n=%d)" % (
            100 * statistics.median(caps), len(caps)))
    # peak by exit reason: are we leaving money on the table?
    by_reason = {}
    for r in rows:
        by_reason.setdefault(r.get("reason") or "?", []).append(r.get("peak_pct") or 0)
    print("\npeak achieved by exit reason:")
    for reason, peaks in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print("  %-10s n=%-3d median peak %+6.3f%%  max %+6.3f%%" % (
            reason, len(peaks), statistics.median(peaks), max(peaks)))


if __name__ == "__main__":
    main()
