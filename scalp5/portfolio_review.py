#!/usr/bin/env python3
"""portfolio_review - every 10 seconds, find the models that are not delivering.

Reads the live books, scores each on realised PnL, and writes a kill list that
the engines obey. A book is stopped when it has had enough trades to judge and
is still losing, or when its recent streak is negative.

    python3 portfolio_review.py --every 10
"""
import argparse, json, os, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"

MIN_TRADES = 5          # enough evidence to judge
MAX_LOSS_PCT = -2.0     # of its starting equity
RECENT_N = 5            # look at the last N closes


def jget(u, t=8.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "review/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--every", type=float, default=10.0)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    print("PORTFOLIO REVIEW — every %.0fs, stop any book that is not delivering" % a.every, flush=True)
    prev = set()
    while True:
        try:
            d = jget(MASTER + "/api/all", 12)
        except Exception as exc:
            print("%s  cannot read books: %r" % (time.strftime("%H:%M:%S"), exc), flush=True)
            time.sleep(a.every); continue

        stop, keep, rows = [], [], []
        for e in d.get("engines") or []:
            if e.get("equity") is None:
                continue
            eq = e.get("equity") or 0
            start = e.get("start_equity") or 0
            trades = e.get("trades") or []
            n = len(trades)
            ret = 100 * (eq / start - 1) if start else 0.0
            recent = [t.get("net_pct") or 0 for t in trades[-RECENT_N:]]
            streak = sum(recent)
            rows.append((e["key"], n, ret, streak))
            if n >= MIN_TRADES and (ret <= MAX_LOSS_PCT or (len(recent) >= RECENT_N and streak < 0)):
                stop.append(e["key"])
            else:
                keep.append(e["key"])

        payload = {"ts": int(time.time() * 1000), "every_s": a.every,
                   "stopped": stop, "running": keep,
                   "books": [{"engine": k, "trades": n, "return_pct": round(r, 3),
                              "recent_sum": round(s, 3)} for k, n, r, s in rows]}
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "review.json.t"), "w") as fh:
            json.dump(payload, fh)
        os.replace(os.path.join(STATE, "review.json.t"), os.path.join(STATE, "review.json"))
        with open(os.path.join(STATE, "disabled.json.t"), "w") as fh:
            json.dump({"books": stop, "ts": payload["ts"]}, fh)
        os.replace(os.path.join(STATE, "disabled.json.t"), os.path.join(STATE, "disabled.json"))

        newly = set(stop) - prev
        if newly:
            for k in sorted(newly):
                print("%s  STOP %-14s %s" % (time.strftime("%H:%M:%S"), k,
                      [r for r in payload["books"] if r["engine"] == k][0]), flush=True)
        prev = set(stop)
        if not a.dry:
            pass
        time.sleep(a.every)


if __name__ == "__main__":
    main()
