"""Vectorised backtest sweep on the GPU.

One job carries the whole search grid: many (model, lookback, window, horizon)
combinations are scored in a single process. Python and torch startup costs about
three seconds, which dwarfs the GPU work, so batching is what makes a large sweep
fast — a thousand parameter tests in one call instead of a hundred.
"""
import json
import os
import sys
import time

import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
import torch


def rolling_sum(x, w):
    """Trailing window sum of width w, so index i covers [i-w+1, i]."""
    cs = torch.cumsum(x, dim=0)
    out = cs.clone()
    out[w:] = cs[w:] - cs[:-w]
    return out


def vwap_paths(p, v, anchors, lookbacks, horizon):
    res = []
    pv = p * v
    for lb in lookbacks:
        num = rolling_sum(pv, lb)
        den = rolling_sum(v, lb)
        level = num / torch.clamp(den, min=1e-12)
        anchor = p[anchors]
        lvl = level[anchors]
        steps = torch.arange(1, horizon + 1, device=p.device, dtype=p.dtype)
        w = 1 - torch.pow(torch.tensor(0.5, device=p.device, dtype=p.dtype), steps / max(1.0, horizon / 2))
        res.append(anchor.unsqueeze(-1) + (lvl - anchor).unsqueeze(-1) * w.unsqueeze(0))
    return torch.stack(res, dim=0)


def vwtrend_paths(p, v, anchors, lookbacks, horizon):
    T = p.shape[0]
    i = torch.arange(T, device=p.device, dtype=p.dtype)
    res = []
    for lb in lookbacks:
        w = v
        sw = rolling_sum(w, lb)
        swx = rolling_sum(w * i, lb)
        swy = rolling_sum(w * p, lb)
        swxy = rolling_sum(w * i * p, lb)
        swxx = rolling_sum(w * i * i, lb)
        den = sw * swxx - swx * swx
        ok = den.abs() > 1e-12
        slope = torch.where(ok, (sw * swxy - swx * swy) / torch.where(ok, den, torch.ones_like(den)), torch.zeros_like(den))
        anchor = p[anchors]
        s = slope[anchors]
        steps = torch.arange(1, horizon + 1, device=p.device, dtype=p.dtype).unsqueeze(0)
        res.append(anchor.unsqueeze(-1) + s.unsqueeze(-1) * steps)
    return torch.stack(res, dim=0)


def vwdrift_paths(p, v, anchors, lookbacks, horizon):
    """Recent move, scaled by how unusual the volume is at the anchor."""
    res = []
    for lb in lookbacks:
        base = torch.roll(p, lb, 0)
        base[:lb] = p[0]
        move = p / torch.clamp(base, min=1e-12) - 1
        prev = torch.roll(v, 1, 0)
        prev[0] = v[0]
        z = torch.clamp((v - prev) / torch.clamp(prev, min=1e-9), -2, 2)
        drift = torch.clamp(move * 0.35 * (1 + 0.1 * z), -0.25, 0.25)
        anchor = p[anchors]
        d = drift[anchors]
        steps = torch.arange(1, horizon + 1, device=p.device, dtype=p.dtype).unsqueeze(0)
        res.append(anchor.unsqueeze(-1) * (1 + d.unsqueeze(-1) * steps / horizon))
    return torch.stack(res, dim=0)


def hybrid_paths(p, v, anchors, lookbacks, horizon):
    """Blend of vwap and vwtrend: components that fail differently."""
    return 0.6 * vwap_paths(p, v, anchors, lookbacks, horizon) + 0.4 * vwtrend_paths(p, v, anchors, lookbacks, horizon)


FAMILIES = (
    ("vwap", vwap_paths),
    ("vwtrend", vwtrend_paths),
    ("vwdrift", vwdrift_paths),
    ("hybrid", hybrid_paths),
)


def main():
    raw = sys.stdin.read()
    job = json.loads(raw) if raw.strip() else {}
    payload = job.get("payload") or {}

    closes = payload.get("closes") or []
    volumes = payload.get("volumes") or []
    times = payload.get("times") or []
    if len(closes) < 200 or len(volumes) != len(closes):
        print(json.dumps({"ok": False, "error": "need 200+ bars with matching volumes"}))
        return

    lookbacks = [int(x) for x in (payload.get("lookbacks") or [2, 3, 5, 8, 10, 12, 20, 30, 50])]
    # One family per phase: the optimiser works a model to exhaustion, then moves
    # to the next, rather than averaging attention across all of them at once.
    wanted = payload.get("methods")
    families = FAMILIES if not wanted else tuple((n, f) for (n, f) in FAMILIES if n in wanted)
    default_h = int(payload.get("horizon") or 12)
    default_b = int(payload.get("bars") or 600)
    raw_grid = payload.get("grid") or []
    grid = [(int(c.get("horizon") or default_h), int(c.get("bars") or default_b)) for c in raw_grid if isinstance(c, dict)]
    if not grid:
        grid = [(default_h, default_b)]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    p = torch.tensor(closes, dtype=torch.float32, device=device)
    v = torch.tensor(volumes, dtype=torch.float32, device=device)
    n = p.shape[0]

    t0 = time.time()
    out = []
    kept = {}
    for (h, b) in grid:
        h = max(2, min(h, 60))
        b = max(120, min(b, n))
        start_i = max(60, n - b)
        end_i = n - h
        if end_i - start_i < 40:
            continue
        anchors = torch.arange(start_i, end_i, h, device=device)
        if anchors.numel() < 3:
            continue
        actual = torch.stack([p[anchors + i + 1] for i in range(h)], dim=1)
        a = actual.unsqueeze(0)
        mu = actual.mean()
        ss_tot = ((actual - mu) ** 2).sum()
        flat_mae = (actual[0, 0] - actual).abs().mean()
        for name, fn in families:
            paths = fn(p, v, anchors, lookbacks, h)
            err = (paths - a).abs()
            mape = (err / torch.clamp(a.abs(), min=1e-12)).mean(dim=(1, 2)) * 100
            mae = err.mean(dim=(1, 2))
            ss_res = ((paths - a) ** 2).sum(dim=(1, 2))
            r2 = 1 - ss_res / torch.clamp(ss_tot, min=1e-12)
            skill = 1 - mae / torch.clamp(flat_mae, min=1e-12)
            for i, lb in enumerate(lookbacks):
                out.append({
                    "method": name, "lookback": lb, "horizon": h, "bars": b,
                    "mape_pct": float(mape[i]), "mae": float(mae[i]),
                    "r2": float(r2[i]), "skill_vs_flat": float(skill[i]),
                })
                kept[(name, lb, h, b)] = (paths[i], anchors, actual)

    if not out:
        print(json.dumps({"ok": False, "error": "grid produced no results"}))
        return

    out.sort(key=lambda r: -r["r2"])
    top = out[0]

    points = []
    series = []
    ctx = kept.get((top["method"], top["lookback"], top["horizon"], top["bars"]))
    if ctx is not None and times:
        path, anchors, actual = ctx
        a_idx = anchors.cpu().tolist()
        pth = path.cpu().tolist()
        act = actual.cpu().tolist()
        h = int(top["horizon"])
        for ai, anchor in enumerate(a_idx):
            for step in range(h):
                ti = anchor + step + 1
                if ti >= len(times):
                    continue
                points.append({
                    "t": int(times[ti]),
                    "forecast": float(pth[ai][step]),
                    "actual": float(act[ai][step]),
                })
        series.append({
            "method": top["method"], "lookback": top["lookback"],
            "mape_pct": top["mape_pct"], "r2": top["r2"], "points": points,
        })

    print(json.dumps({
        "ok": True, "device": device, "compute": "torch",
        "combinations": len(grid), "candidates": len(out),
        "seconds": round(time.time() - t0, 3),
        "results": out, "points": points, "series": series,
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
