#!/usr/bin/env python3
"""Train a model on the historian's dataset.

The point is not the model — a logistic regression — it is the provenance: the
features come from the same envelopes the explorer shows, the label is a forward
return computed without look-ahead, and the split is by time so the model is
never evaluated on its own past.

    python3 tools/train.py --csv datasets/features.csv --horizon 3
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd


def load(path):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["bar_ms"], unit="ms", utc=True)
    df = df.sort_values(["symbol", "bar_ms"]).reset_index(drop=True)
    return df


def add_features(df, horizon):
    g = df.groupby("symbol", group_keys=False)
    # forward return is shifted backwards from the future: the label never sees
    # anything the model could not have known at decision time
    df["fwd_ret"] = g["close"].apply(lambda s: s.shift(-horizon) / s - 1.0)
    df["label"] = (df["fwd_ret"] > 0).astype(float)
    df["ret_1"] = g["close"].apply(lambda s: s.pct_change())
    df["ret_3"] = g["close"].apply(lambda s: s.pct_change(3))
    df["vol_10"] = g["ret_1"].apply(lambda s: s.rolling(10, min_periods=3).std())
    df["range_pct"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    df["tick_z"] = g["ticks"].apply(lambda s: (s - s.rolling(20, min_periods=5).mean()) / s.rolling(20, min_periods=5).std())
    df["flow_z"] = g["flow_conf"].apply(lambda s: (s - s.rolling(20, min_periods=5).mean()) / (s.rolling(20, min_periods=5).std() + 1e-9))
    return df.dropna(subset=["ret_1", "ret_3", "vol_10", "fwd_ret"])


FEATURES = ["ret_1", "ret_3", "vol_10", "range_pct", "tick_z", "imbalance",
            "flow", "flow_conf", "signal_score", "taker_ratio"]


def standardise(train, test, cols):
    mu = train[cols].mean()
    sd = train[cols].std().replace(0, 1)
    return (train[cols] - mu) / sd, (test[cols] - mu) / sd, mu, sd


def fit_logistic(X, y, epochs=400, lr=0.35, l2=1e-3):
    w = np.zeros(X.shape[1])
    b = 0.0
    n = len(X)
    for _ in range(epochs):
        z = X @ w + b
        p = 1 / (1 + np.exp(-z))
        grad_w = (X.T @ (p - y)) / n + l2 * w
        grad_b = float(np.mean(p - y))
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def auc(y, p):
    order = np.argsort(p)
    y = np.asarray(y)[order]
    ranks = np.arange(1, len(y) + 1)
    pos = y == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="datasets/features.csv")
    ap.add_argument("--horizon", type=int, default=3, help="bars ahead for the label")
    ap.add_argument("--out", default="datasets/model")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit("no dataset at %s — run core/bin/historian first" % args.csv)

    raw = load(args.csv)
    df = add_features(raw, args.horizon)
    if len(df) < 40:
        sys.exit("dataset too small to train: %d rows" % len(df))

    # time split: the last 30% of bars is the test set, never shuffled
    cut = int(len(df) * 0.7)
    train, test = df.iloc[:cut], df.iloc[cut:]
    cols = [c for c in FEATURES if c in df.columns]
    Xtr, Xte, mu, sd = standardise(train, test, cols)
    w, b = fit_logistic(Xtr.to_numpy(), train["label"].to_numpy())

    p_tr = 1 / (1 + np.exp(-(Xtr.to_numpy() @ w + b)))
    p_te = 1 / (1 + np.exp(-(Xte.to_numpy() @ w + b)))
    acc_tr = float(((p_tr > 0.5) == (train["label"] > 0.5)).mean())
    acc_te = float(((p_te > 0.5) == (test["label"] > 0.5)).mean())
    base = float(max(test["label"].mean(), 1 - test["label"].mean()))

    metrics = {
        "dataset": args.csv,
        "rows": int(len(df)), "train_rows": int(len(train)), "test_rows": int(len(test)),
        "symbols": int(df["symbol"].nunique()),
        "horizon_bars": args.horizon,
        "features": cols,
        "train_accuracy": round(acc_tr, 4),
        "test_accuracy": round(acc_te, 4),
        "test_auc": round(auc(test["label"].to_numpy(), p_te), 4),
        "baseline_accuracy": round(base, 4),
        "edge_vs_baseline": round(acc_te - base, 4),
        "from": str(df["ts"].min()), "to": str(df["ts"].max()),
        "note": "small sample; treat accuracy near the baseline as no edge",
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)
    with open(os.path.join(args.out, "model.json"), "w") as fh:
        json.dump({"features": cols, "weights": w.tolist(), "bias": b,
                   "mean": mu.tolist(), "std": sd.tolist(),
                   "horizon_bars": args.horizon}, fh, indent=2)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
