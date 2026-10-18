#!/usr/bin/env python3
"""metalabel - learn which trades actually clear the fee, from our own record.

Primary models pick the side. This trains a second model on the outcome of those
picks: did the trade finish net-positive after costs? Then it writes a gate that
engines consult before taking a trade.

Pure Python (no numpy): logistic regression by gradient descent on standardised
features. Small data, so heavy regularisation and a feature cap keep it honest.

    python3 metalabel.py --train
"""
import argparse, json, math, os, statistics, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"


def jget(u, t=12.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "metalabel/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def features(t, eng):
    """Only things knowable at entry time."""
    return {
        "score": 1.0 if (t.get("why") or "").upper().find("SCORE") >= 0 else 0.0,
        "long_side": 1.0 if (t.get("side") == "buy") else 0.0,
        "notional": math.log1p(abs(t.get("notional") or 0)),
        "held": math.log1p(abs(t.get("held_s") or 0)),
        "engine_swing": 1.0 if eng == "swing" else 0.0,
        "engine_multi": 1.0 if eng in ("multimodels", "stats_models") else 0.0,
        "engine_slow": 1.0 if eng in ("vol_drift", "pump_flag", "climax_fade",
                                      "capitulation", "btc_beta_lag", "donchian4h",
                                      "squeeze1h", "rs_leader", "sympathy_lag") else 0.0,
    }


def sigmoid(z):
    if z < -30: return 0.0
    if z > 30: return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def train(X, y, l2=0.05, lr=0.25, epochs=4000):
    n, d = len(X), len(X[0])
    w = [0.0] * d; b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d; gb = 0.0
        for xi, yi in zip(X, y):
            p = sigmoid(sum(wj * xj for wj, xj in zip(w, xi)) + b)
            err = p - yi
            for j in range(d):
                gw[j] += err * xi[j]
            gb += err
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
        b -= lr * gb / n
    return w, b


def collect():
    d = jget(MASTER + "/api/all", 20)
    rows = []
    for e in d.get("engines") or []:
        for t in (e.get("trades") or []):
            if t.get("net_pct") is None: continue
            rows.append((features(t, e["key"]), 1.0 if t["net_pct"] > 0 else 0.0, t["net_pct"]))
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--train", action="store_true")
    a = ap.parse_args()
    rows = collect()
    print("trades available:", len(rows), flush=True)
    if len(rows) < 30:
        print("not enough trades to train honestly — need 30+, have %d" % len(rows))
        json.dump({"trained": False, "n": len(rows), "ts": int(time.time() * 1000)},
                  open(os.path.join(STATE, "gate.json"), "w"))
        return
    names = list(rows[0][0].keys())
    X = [[r[0][k] for k in names] for r in rows]
    y = [r[1] for r in rows]
    mu = [statistics.mean(c) for c in zip(*X)]
    sd = [(statistics.pstdev(c) or 1.0) for c in zip(*X)]
    Xn = [[(xi - m) / s for xi, m, s in zip(row, mu, sd)] for row in X]
    w, b = train(Xn, y)
    base = sum(y) / len(y)
    keep = []
    for xi, yi, net in zip(Xn, y, [r[2] for r in rows]):
        p = sigmoid(sum(wj * xj for wj, xj in zip(w, xi)) + b)
        keep.append((p, yi, net))
    thr = 0.55
    kept = [k for k in keep if k[0] >= thr]
    out = {
        "trained": True, "n": len(rows), "ts": int(time.time() * 1000),
        "features": names, "w": [round(x, 4) for x in w], "b": round(b, 4),
        "mu": [round(x, 4) for x in mu], "sd": [round(x, 4) for x in sd],
        "threshold": thr,
        "base_win_rate": round(100 * base, 2),
        "gated_win_rate": round(100 * (sum(1 for k in kept if k[1] > 0) / len(kept)), 2) if kept else None,
        "gated_net_pct": round(statistics.mean([k[2] for k in kept]), 4) if kept else None,
        "base_net_pct": round(statistics.mean([k[2] for k in keep]), 4),
        "kept_share": round(100 * len(kept) / len(keep), 1),
    }
    os.makedirs(STATE, exist_ok=True)
    json.dump(out, open(os.path.join(STATE, "gate.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))
    print("\nif gated_net_pct > base_net_pct, the filter is real. If not, it is noise.")


if __name__ == "__main__":
    main()
