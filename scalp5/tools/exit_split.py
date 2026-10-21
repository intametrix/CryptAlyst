#!/usr/bin/env python3
"""exit_split - how each engine actually closes: model, clock or session end.

    python3 tools/exit_split.py            # ledgers + packs
"""
import collections, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tactics


def reasons():
    for f in glob.glob("ledger/*.jsonl") + glob.glob("state/pack_*.json"):
        try:
            if f.endswith(".jsonl"):
                for line in open(f):
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(d, dict) and d.get("reason"):
                        yield (d.get("engine") or f.split("/")[-1][:-6]), d["reason"]
            else:
                d = json.load(open(f))
                eng = d.get("engine") or f.split("pack_")[-1][:-5]
                for t in (d.get("trades") or []):
                    if t.get("reason"):
                        yield eng, t["reason"]
        except Exception:
            continue


per = collections.defaultdict(lambda: collections.Counter())
for eng, reason in reasons():
    per[eng][tactics.classify_reason(reason)] += 1

print("%-20s %7s %7s %7s %8s" % ("engine", "model", "clock", "end", "clock%"))
tot = collections.Counter()
for eng, c in sorted(per.items(), key=lambda kv: -(kv[1]["clock"] / max(1, sum(kv[1].values())))):
    n = sum(c.values())
    tot.update(c)
    print("%-20s %7d %7d %7d %7.0f%%" % (eng, c["model"], c["clock"], c["session_end"],
                                         100 * c["clock"] / max(1, n)))
n = sum(tot.values())
print("%-20s %7d %7d %7d %7.0f%%" % ("ALL", tot["model"], tot["clock"], tot["session_end"],
                                     100 * tot["clock"] / max(1, n)))
