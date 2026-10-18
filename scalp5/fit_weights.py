#!/usr/bin/env python3
"""fit_weights - learn the composite score from realised outcomes.

Ridge regression of net PnL on the entry-time feature vector. Until there are
enough trades with stored features it keeps an equal-weight prior, so the model
never runs on weights that are noise.
"""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")
OUT = os.path.join(STATE, "weights.json")
MIN_TRADES = 25


def load_trades():
    rows = []
    # preferred source: the durable fills log written by lib/emit
    fills = os.path.join(STATE, "fills.jsonl")
    if os.path.exists(fills):
        with open(fills) as fh:
            for line in fh:
                try:
                    t = json.loads(line)
                except Exception:
                    continue
                f = t.get("features") or {}
                if f and t.get("net_pct") is not None:
                    rows.append((f, float(t["net_pct"]), t.get("engine")))
    if rows:
        return rows
    for fn in os.listdir(STATE):
        if not (fn.startswith("pack_") and fn.endswith(".json")):
            continue
        try:
            with open(os.path.join(STATE, fn)) as fh:
                book = json.load(fh)
        except Exception:
            continue
        for t in book.get("trades") or []:
            f = t.get("features") or {}
            if f and t.get("net_pct") is not None:
                rows.append((f, float(t["net_pct"]), t.get("engine")))
    return rows


def fit(rows):
    try:
        import numpy as np
    except Exception:
        return None
    fields = sorted({k for f, _, _ in rows for k in f})
    if not fields:
        return None
    X = np.array([[f.get(k, 0.0) for k in fields] for f, _, _ in rows], dtype=float)
    y = np.array([v for _, v, _ in rows], dtype=float)
    lam = 1.0                                  # ridge, keeps weights sane on small n
    A = X.T @ X + lam * np.eye(len(fields))
    b = X.T @ y
    try:
        w = np.linalg.solve(A, b)
    except Exception:
        w = np.linalg.lstsq(A, b, rcond=None)[0]
    scale = float(np.abs(w).sum() or 1.0)
    return {k: round(float(v) / scale, 6) for k, v in zip(fields, w)}


def main():
    rows = load_trades()
    print("trades with features: %d (need %d to fit)" % (len(rows), MIN_TRADES), flush=True)
    if len(rows) < MIN_TRADES:
        print("keeping the equal-weight prior — not enough evidence yet", flush=True)
        json.dump({"weights": {}, "n": len(rows), "ts": int(time.time() * 1000),
                   "note": "insufficient trades"}, open(OUT, "w"))
        return
    w = fit(rows)
    if not w:
        print("fit failed", flush=True)
        return
    json.dump({"weights": w, "n": len(rows), "ts": int(time.time() * 1000)}, open(OUT, "w"))
    print("fitted on %d trades:" % len(rows), flush=True)
    for k, v in sorted(w.items(), key=lambda kv: -abs(kv[1])):
        print("  %-22s %+.4f" % (k, v), flush=True)


if __name__ == "__main__":
    main()
