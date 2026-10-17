#!/usr/bin/env python3
"""tape - a live fill tape for the dashboard. Reads every engine's log and
normalises fills into one stream, newest first.

Writes state/tape.json every second. Only reads files; never trades.
"""
import json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
STATE = os.path.join(HERE, "state")
SRC = [("swing", "swing.log"), ("ignition", "ignition.log"), ("alpha", "alpha.log"),
       ("strategies", "strategies.log"), ("slow", "slow.log"), ("edges", "edges.log"),
       ("multimodels", "multimodels.log"), ("statsmodels", "statsmodels.log"),
       ("sympathy", "sympathy.log")]
SEEN = set()
FILLS = []

def parse(line):
    m = re.match(r"^(\d\d:\d\d:\d\d)\s+", line)
    if not m:
        return None
    ts = m.group(1)
    rest = line[len(ts):].strip()
    r = re.search(r"(?:>>>\s*OPEN|OPEN)\s+(\S+)\s+(BUY|SELL)\s+(.*)$", rest)
    if r:
        return {"t": ts, "sym": r.group(1), "side": r.group(2), "why": r.group(3)[:90], "kind": "open"}
    r = re.search(r"(?:<<<\s*CLOSE|CLOSE)\s+(\S+)\s+(BUY|SELL)?\s*([+-]?[\d.]+)%?\s*net\s*([+-]?[\d.]+)%.*?(?:pnl\s*([+-]?[\d.]+))?", rest)
    if r:
        return {"t": ts, "sym": r.group(1), "side": r.group(2) or "", "why": "net %s%%" % (r.group(4)), "kind": "close"}
    r = re.search(r"(BUY|SELL)\s+(\S+)\s+(.*)$", rest)
    if r:
        return {"t": ts, "sym": r.group(2), "side": r.group(1), "why": r.group(3)[:90], "kind": "open"}
    return None

def main():
    print("tape: watching %d logs" % len(SRC), flush=True)
    offs = {f: 0 for _, f in SRC}
    while True:
        for name, f in SRC:
            p = os.path.join(LOGS, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz < offs[f]:
                offs[f] = 0
            if sz == offs[f]:
                continue
            with open(p, "r", errors="ignore") as fh:
                fh.seek(offs[f])
                for line in fh:
                    ev = parse(line)
                    if not ev:
                        continue
                    ev.update({"src": name, "id": "%s|%s|%s|%s" % (f, ev["t"], ev["sym"], ev["kind"])})
                    if ev["id"] in SEEN:
                        continue
                    SEEN.add(ev["id"])
                    FILLS.append(ev)
                offs[f] = fh.tell()
        FILLS.sort(key=lambda x: x["t"])
        FILLS[:] = FILLS[-400:]
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "tape.json.t"), "w") as fh:
            json.dump({"ts": int(time.time() * 1000), "fills": FILLS[-200:]}, fh)
        os.replace(os.path.join(STATE, "tape.json.t"), os.path.join(STATE, "tape.json"))
        time.sleep(1.0)

if __name__ == "__main__":
    main()
