"""Train a sequence model on the GPU and grade it honestly.

Everything else in this system selects or fits a formula. This actually learns:
a small temporal CNN reads the last `window` bars of returns, volume and clock
features for a symbol and predicts the next `horizon` returns. Trained across many
symbols on the GPU, evaluated on bars it never saw, against the flat-line
baseline that has beaten every other approach so far.
"""
import json
import math
import os
import sys
import time

# Silence library noise: the job protocol is one JSON object on stdout.
os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import torch
import torch.nn as nn


def series_from(payload):
    """List of {symbol, closes, volumes, times}."""
    out = []
    for s in payload.get("series") or []:
        c = s.get("closes") or []
        if len(c) < 80:
            continue
        v = s.get("volumes") or [0.0] * len(c)
        t = s.get("times") or [0] * len(c)
        out.append({"symbol": s.get("symbol", "?"), "c": np.asarray(c, dtype=np.float32),
                    "v": np.asarray(v, dtype=np.float32), "t": np.asarray(t, dtype=np.int64)})
    return out


def features(series, window):
    """Per-bar feature rows and the forward returns used as targets."""
    rows = []
    for s in series:
        c, v, t = s["c"], s["v"], s["t"]
        n = len(c)
        r = np.zeros(n, dtype=np.float32)
        r[1:] = c[1:] / np.maximum(c[:-1], 1e-9) - 1
        volz = np.zeros(n, dtype=np.float32)
        for i in range(n):
            lo = max(0, i - 20)
            seg = v[lo:i + 1]
            mu = seg.mean() if len(seg) else 0
            sd = seg.std() if len(seg) else 0
            volz[i] = (v[i] - mu) / sd if sd > 1e-9 else 0
        hours = ((t // 3_600_000) % 24).astype(np.float32)
        dows = (((t // 86_400_000) + 4) % 7).astype(np.float32)
        X = np.stack([
            np.clip(r * 100, -5, 5),
            np.clip(volz, -5, 5),
            np.sin(2 * math.pi * hours / 24), np.cos(2 * math.pi * hours / 24),
            np.sin(2 * math.pi * dows / 7), np.cos(2 * math.pi * dows / 7),
        ], axis=1)
        rows.append((X, c))
    return rows


class SeqNet(nn.Module):
    """Small dilated CNN over the lookback window."""

    def __init__(self, n_feat, horizon):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(n_feat, 64, 3, padding=1), nn.GELU(),
            nn.Conv1d(64, 64, 3, dilation=2, padding=2), nn.GELU(),
            nn.Conv1d(64, 64, 3, dilation=4, padding=4), nn.GELU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.head = nn.Sequential(nn.Linear(64, 64), nn.GELU(), nn.Linear(64, horizon))

    def forward(self, x):
        h = self.net(x).squeeze(-1)
        return self.head(h)


def main():
    raw = sys.stdin.read()
    job = json.loads(raw) if raw.strip() else {}
    payload = job.get("payload") or {}
    horizon = int(payload.get("horizon") or 24)
    window = int(payload.get("window") or 64)
    epochs = int(payload.get("epochs") or 12)
    lr = float(payload.get("lr") or 2e-3)

    series = series_from(payload)
    if len(series) < 2:
        print(json.dumps({"ok": False, "error": "need at least 2 symbols with 80+ bars"}))
        return

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(0)
    np.random.seed(0)

    feats = features(series, window)
    # Split by TIME inside each symbol: the last 20% of every symbol is test.
    train_x, train_y, test = [], [], []
    for (X, c) in feats:
        cut = int(len(c) * 0.8)
        for i in range(window, cut - horizon):
            train_x.append(X[i - window:i])
            train_y.append(c[i + 1:i + 1 + horizon] / c[i] - 1)
        for i in range(cut, len(c) - horizon):
            test.append((X[i - window:i], c[i], c[i + 1:i + 1 + horizon]))
    if len(train_x) < 200 or len(test) < 50:
        print(json.dumps({"ok": False, "error": "not enough samples to train"}))
        return

    Xtr = torch.from_numpy(np.stack(train_x)).permute(0, 2, 1).to(device)
    Ytr = torch.from_numpy(np.asarray(train_y, dtype=np.float32)).to(device)

    model = SeqNet(Xtr.shape[1], horizon).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    lossf = nn.HuberLoss(delta=0.01)
    bs = 256
    n = Xtr.shape[0]
    t0 = time.time()
    losses = []
    for ep in range(epochs):
        perm = torch.randperm(n, device=device)
        tot = 0.0
        for b in range(0, n, bs):
            idx = perm[b:b + bs]
            opt.zero_grad()
            loss = lossf(model(Xtr[idx]), Ytr[idx])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += float(loss) * len(idx)
        losses.append(round(tot / n, 6))

    model.eval()
    # Grade on held-out bars: the model's path vs a flat line.
    mae_m = mae_n = mape_m = mape_n = 0.0
    with torch.no_grad():
        for (xf, last, truth) in test:
            x = torch.from_numpy(xf).unsqueeze(0).permute(0, 2, 1).to(device)
            pred = (1 + model(x).cpu().numpy()[0]) * last
            flat = np.full(horizon, last)
            mae_m += np.abs(pred - truth).mean()
            mae_n += np.abs(flat - truth).mean()
            mape_m += (np.abs(pred - truth) / truth).mean()
            mape_n += (np.abs(flat - truth) / truth).mean()
    k = len(test)
    print(json.dumps({
        "ok": True,
        "device": device,
        "compute": "torch",
        "samples_train": n,
        "samples_test": k,
        "params": sum(p.numel() for p in model.parameters()),
        "epochs": epochs,
        "train_seconds": round(time.time() - t0, 1),
        "losses": losses,
        # numpy scalars are not JSON serialisable; cast every metric to float.
        "mae_model": float(mae_m / k),
        "mae_naive": float(mae_n / k),
        "mape_model_pct": float(100 * mape_m / k),
        "mape_naive_pct": float(100 * mape_n / k),
        "skill": float(1 - (mae_m / k) / max(mae_n / k, 1e-9)),
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
