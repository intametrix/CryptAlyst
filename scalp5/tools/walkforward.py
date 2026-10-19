#!/usr/bin/env python3
"""walkforward - can the edge survive data it was not fitted on?

Fits feature weights on one window, scores the next, and rolls forward. If the
out-of-sample numbers collapse against in-sample, the model is curve-fitted and
no amount of live trading will fix it.

    python3 tools/walkforward.py --windows 4 --min-train 40
"""
import argparse, glob, json, math, os, statistics, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")


def load_trades():
    """Every closed trade we still have: live fills, archived books, live books."""
    rows = []
    seen = set()
    for pattern in (os.path.join(STATE, "fills.jsonl"),
                    os.path.join(STATE, "archive_*", "fills.jsonl")):
        for path in glob.glob(pattern):
            try:
                with open(path) as fh:
                    for line in fh:
                        try:
                            t = json.loads(line)
                        except Exception:
                            continue
                        f = t.get("features") or {}
                        if not f or t.get("net_pct") is None:
                            continue
                        key = (t.get("entry_ms"), t.get("symbol"), t.get("side"), t.get("net_pct"))
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append({"ts": t.get("exit_ms") or t.get("entry_ms") or 0,
                                     "feat": f, "net": float(t["net_pct"]),
                                     "engine": t.get("engine"), "symbol": t.get("symbol")})
            except Exception:
                pass
    for path in glob.glob(os.path.join(STATE, "archive_*", "pack_*.json")) + \
                glob.glob(os.path.join(STATE, "pack_*.json")):
        try:
            with open(path) as fh:
                b = json.load(fh)
        except Exception:
            continue
        for t in (b.get("trades") or []):
            f = t.get("features") or {}
            if not f or t.get("net_pct") is None:
                continue
            key = (t.get("entry_ms"), t.get("symbol"), t.get("side"), t.get("net_pct"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"ts": t.get("exit_ms") or t.get("entry_ms") or 0,
                         "feat": f, "net": float(t["net_pct"]),
                         "engine": b.get("engine"), "symbol": t.get("symbol")})
    rows.sort(key=lambda r: r["ts"])
    return rows


def keys_of(rows):
    ks = set()
    for r in rows:
        for k, v in r["feat"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                ks.add(k)
    return sorted(ks)


def matrix(rows, keys):
    X, y = [], []
    for r in rows:
        X.append([float(r["feat"].get(k) or 0.0) for k in keys])
        y.append(r["net"])
    return X, y


def ridge_fit(X, y, lam=1.0):
    """Closed-form ridge without numpy: solve (X'X + lam I) w = X'y."""
    n = len(X[0]) if X else 0
    A = [[0.0] * n for _ in range(n)]
    b = [0.0] * n
    for row, target in zip(X, y):
        for i in range(n):
            b[i] += row[i] * target
            for j in range(n):
                A[i][j] += row[i] * row[j]
    for i in range(n):
        A[i][i] += lam
    # gaussian elimination with partial pivoting
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-12:
            continue
        A[col], A[piv] = A[piv], A[col]
        b[col], b[piv] = b[piv], b[col]
        pv = A[col][col]
        for j in range(col, n):
            A[col][j] /= pv
        b[col] /= pv
        for r in range(n):
            if r == col:
                continue
            f = A[r][col]
            if f == 0:
                continue
            for j in range(col, n):
                A[r][j] -= f * A[col][j]
            b[r] -= f * b[col]
    return b


def score(X, w):
    return [sum(wi * xi for wi, xi in zip(w, row)) for row in X]


def stats(vals, label, width=0):
    if not vals:
        return "%-14s no trades" % label
    wins = [v for v in vals if v > 0]
    return ("%-14s n=%-4d mean %+7.4f%%  median %+7.4f%%  win %3.0f%%  sum %+8.2f%%"
            % (label, len(vals), statistics.mean(vals), statistics.median(vals),
               100 * len(wins) / len(vals), sum(vals)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=4)
    ap.add_argument("--min-train", type=int, default=40)
    ap.add_argument("--lam", type=float, default=1.0)
    a = ap.parse_args()

    rows = load_trades()
    print("closed trades with features: %d" % len(rows))
    if len(rows) < a.min_train:
        print("need at least %d to say anything; %d short" % (a.min_train, a.min_train - len(rows)))
        return
    keys = keys_of(rows)
    print("features: %d  (%s)" % (len(keys), ", ".join(keys[:10])))

    # equal weight prior, as the baseline every fit must beat
    base_all = [r["net"] for r in rows]
    print("\nbaseline (all trades, no model)")
    print("  " + stats(base_all, "buy-and-hold"))

    n = len(rows)
    size = max(a.min_train, n // (a.windows + 1))
    print("\nwalk-forward, %d windows of ~%d trades" % (a.windows, size))
    oos_all, is_all = [], []
    for w in range(a.windows):
        train_end = size + w * size
        test_end = min(n, train_end + size)
        if train_end >= n or test_end <= train_end:
            break
        train, test = rows[:train_end], rows[train_end:test_end]
        Xtr, ytr = matrix(train, keys)
        Xte, yte = matrix(test, keys)
        wts = ridge_fit(Xtr, ytr, a.lam)
        ispred = score(Xtr, wts)
        oospred = score(Xte, wts)
        is_mean = statistics.mean(ypred for ypred in ispred)
        # we only trade when the model says the trade is worth taking
        taken = [r["net"] for r, p in zip(test, oospred) if p > 0]
        print("\nwindow %d  train %d  test %d" % (w + 1, len(train), len(test)))
        print("  " + stats([r["net"] for r in train], "in-sample"))
        print("  " + stats([r["net"] for r in test], "out-of-sample"))
        print("  " + stats(taken, "taken (p>0)"))
        is_all.append(is_mean)
        oos_all.extend(taken)

    print("\nsummary")
    if oos_all:
        print("  " + stats(oos_all, "all OOS taken"))
    if is_all:
        print("  in-sample mean predicted net: %+.4f%%" % statistics.mean(is_all))
    print("\nrule of thumb: an edge that only exists in-sample is a fitted line, not alpha.")


if __name__ == "__main__":
    main()
