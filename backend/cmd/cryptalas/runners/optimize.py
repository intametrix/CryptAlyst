#!/usr/bin/env python3
"""Iterative hyperparameter optimiser for the CryptALAS agent.

A single pass over a grid is not an optimiser: it cannot refine around a good
region. This runs a sequence of rounds, each proposing a batch of candidates
around the current incumbent, scoring them all, and moving when one wins.

  {"payload": {"series": [...], "horizon": N, "windows": W, "iterations": 50}}

  {"ok": true, "best": {...}, "iterations": 50, "evaluations": 3200,
   "history": [{"iteration": 1, "mae": .., "params": {...}}, ...],
   "device": "cuda|cpu", "compute": "jax|numpy|python"}

Why this belongs on a GPU: each round scores a batch of candidates against every
walk-forward window at once, which is a dense array reduction. 50 rounds of a
64-candidate batch is 3,200 full backtests, done as tensor work.

The model being tuned:

    forecast[i] = w_sma * sma(n) + w_ema * ema(alpha) + w_trend * trend(damp)[i]

with the weights on the simplex. `sma`, `alpha` and `damp` are continuous, so the
search can refine, which a fixed grid cannot do.
"""
import json
import math
import sys

# --------------------------------------------------------------------- device


def _probe_runtime(argv, timeout=40):
    import json as _json
    import subprocess

    code = r"""
import json, sys
out = {"exe": sys.executable}
try:
    import jax
    try:
        devs = [d.platform for d in jax.devices()]
        out["jax"] = "cuda" if any("cuda" in p for p in devs) else "cpu"
    except Exception as exc:
        out["jax"] = "error"; out["jax_error"] = str(exc)[:200]
except ImportError as exc:
    out["jax"] = "absent"; out["jax_error"] = str(exc)[:120]
try:
    import torch
    try:
        out["torch"] = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception as exc:
        out["torch"] = "error"; out["torch_error"] = str(exc)[:200]
except ImportError as exc:
    out["torch"] = "absent"; out["torch_error"] = str(exc)[:120]
print(json.dumps(out))
"""
    info = {"exe": " ".join(argv), "jax": "error", "torch": "error"}
    try:
        proc = subprocess.run(argv + ["-c", code], capture_output=True, text=True, timeout=timeout)
        lines = (proc.stdout or "").strip().splitlines()
        if lines:
            info.update(_json.loads(lines[-1]))
        else:
            info["detail"] = (proc.stderr or "")[:200]
    except Exception as exc:
        info["detail"] = str(exc)[:200]
    return info


def detect_backend():
    """Return (device, backend)."""
    try:
        import jax

        platforms = [d.platform for d in jax.devices()]
        if any("cuda" in p for p in platforms):
            return "cuda", "jax"
        if any("gpu" in p or "metal" in p for p in platforms):
            return "metal", "jax"
    except Exception:
        pass
    try:
        import numpy  # noqa: F401

        return "cpu", "numpy"
    except Exception:
        pass
    return "cpu", "python"


# ------------------------------------------------------------------- objective
#
# Per walk-forward cut we can pre-compute everything that does not depend on the
# candidate, then score a whole batch at once:
#
#   sma(n)      = (prefix[cut] - prefix[cut-n]) / n          -- O(1) per candidate
#   trend(damp) = last_y + slope * sum(damp^k, k=1..i)       -- slope is fixed
#   ema(alpha)  = fold(alpha * c[t] + (1-alpha) * e)         -- loop over bars,
#                 vectorised across candidates
#


def pick_cuts(n, horizon, windows):
    latest = n - horizon
    if latest <= 60:
        return []
    stride = max(1, horizon)
    if windows * stride + 60 > latest:
        stride = max(1, (latest - 60) // max(1, windows))
    cuts, c = [], latest
    while c > 60 and len(cuts) < windows:
        cuts.append(c)
        c -= stride
    return sorted(cuts)


def batch_mae_numpy(np, closes, cuts, horizon, batch):
    """MAE for a batch of candidates: batch[k] = (w1, w2, w3, n, alpha, damp)."""
    C = len(batch)
    w = np.array([(b[0], b[1], b[2]) for b in batch], dtype=np.float64)
    n_sma = np.array([max(3, int(b[3])) for b in batch])
    alpha = np.array([b[4] for b in batch], dtype=np.float64)
    damp = np.array([b[5] for b in batch], dtype=np.float64)

    total_abs = np.zeros(C, dtype=np.float64)
    points = 0

    closes = np.asarray(closes, dtype=np.float64)
    prefix = np.concatenate(([0.0], np.cumsum(closes)))

    for cut in cuts:
        if cut + horizon > len(closes):
            continue
        actual = closes[cut:cut + horizon]

        # sma per candidate, in O(1) from prefix sums.
        lo = np.maximum(0, cut - n_sma)
        sma = (prefix[cut] - prefix[lo]) / np.maximum(1, cut - lo)

        # ema per candidate: loop over bars, vector op across candidates.
        e = np.full(C, closes[0], dtype=np.float64)
        for t in range(1, cut):
            e = alpha * closes[t] + (1.0 - alpha) * e

        # Least-squares slope on the prefix is candidate-independent.
        m = float(cut)
        xs = np.arange(cut, dtype=np.float64)
        sx = xs.sum()
        sxx = (xs * xs).sum()
        sy = closes[:cut].sum()
        sxy = (xs * closes[:cut]).sum()
        den = m * sxx - sx * sx
        slope = (m * sxy - sx * sy) / den if den else 0.0
        last_y = (sy - slope * sx) / m + slope * (m - 1)

        # Damped extrapolation per candidate: cumulative weight per step.
        steps = np.zeros((C, horizon), dtype=np.float64)
        acc = np.zeros(C, dtype=np.float64)
        scale = np.ones(C, dtype=np.float64)
        for i in range(horizon):
            scale = scale * damp
            acc = acc + scale
            steps[:, i] = last_y + slope * acc

        # Combine.
        fc = (
            w[:, 0:1] * sma[:, None]
            + w[:, 1:2] * e[:, None]
            + w[:, 2:3] * steps
        )
        err = np.abs(fc - actual[None, :])
        total_abs += err.sum(axis=1)
        points += horizon

    return total_abs / max(1, points)


def batch_mae_python(closes, cuts, horizon, batch):
    out = []
    for (w1, w2, w3, n_sma, alpha, damp) in batch:
        n = max(3, int(n_sma))
        total, points = 0.0, 0
        for cut in cuts:
            if cut + horizon > len(closes):
                continue
            lo = max(0, cut - n)
            sma = sum(closes[lo:cut]) / max(1, cut - lo)

            e = closes[0]
            for t in range(1, cut):
                e = alpha * closes[t] + (1 - alpha) * e

            xs = range(cut)
            m = float(cut)
            sx = sum(xs)
            sxx = sum(i * i for i in xs)
            sy = sum(closes[:cut])
            sxy = sum(i * closes[i] for i in xs)
            den = m * sxx - sx * sx
            slope = (m * sxy - sx * sy) / den if den else 0.0
            last_y = (sy - slope * sx) / m + slope * (m - 1)

            step = 0.0
            scale = 1.0
            for i in range(horizon):
                scale *= damp
                step += slope * scale
                f = w1 * sma + w2 * e + w3 * (last_y + step)
                total += abs(f - closes[cut + i])
            points += horizon
        out.append(total / max(1, points))
    return out


# --------------------------------------------------------------------- search


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def simplex(w):
    """Project onto the probability simplex so weights sum to 1."""
    w = [max(0.0, x) for x in w]
    s = sum(w)
    if s <= 0:
        return [1.0, 0.0, 0.0]
    return [x / s for x in w]


def propose(best, step, rng):
    """One candidate near the incumbent, as a parameter dict."""
    w = simplex([
        best["w"][0] + rng.gauss(0, step),
        best["w"][1] + rng.gauss(0, step),
        best["w"][2] + rng.gauss(0, step),
    ])
    return {
        "w": w,
        "n": clamp(best["n"] + rng.gauss(0, step * 60), 3, 200),
        "alpha": clamp(best["alpha"] + rng.gauss(0, step * 0.25), 0.01, 0.9),
        "damp": clamp(best["damp"] + rng.gauss(0, step * 0.15), 0.5, 1.0),
    }


def as_row(p):
    return (p["w"][0], p["w"][1], p["w"][2], p["n"], p["alpha"], p["damp"])


def to_params(p):
    return {
        "weights": [round(x, 4) for x in p["w"]],
        "sma": int(round(p["n"])),
        "alpha": round(p["alpha"], 4),
        "damp": round(p["damp"], 4),
    }


def main():
    raw = sys.stdin.read()
    try:
        job = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": "bad json", "runner": "optimize.py"}))
        return

    payload = job.get("payload") or {}
    closes = []
    for x in (payload.get("series") or []):
        v = x.get("v") if isinstance(x, dict) else x
        try:
            closes.append(float(v))
        except (TypeError, ValueError):
            continue

    try:
        horizon = max(1, min(int(payload.get("horizon") or 12), 500))
    except (TypeError, ValueError):
        horizon = 12
    try:
        windows = max(2, min(int(payload.get("windows") or 8), 30))
    except (TypeError, ValueError):
        windows = 8
    try:
        iterations = max(1, min(int(payload.get("iterations") or 50), 500))
    except (TypeError, ValueError):
        iterations = 50
    batch_size = 64

    if len(closes) < 80:
        print(json.dumps({"ok": False, "error": "need >= 80 bars to optimise, got %d" % len(closes),
                          "runner": "optimize.py"}))
        return

    cuts = pick_cuts(len(closes), horizon, windows)
    if not cuts:
        print(json.dumps({"ok": False, "error": "not enough history for an optimisation window",
                          "runner": "optimize.py"}))
        return

    device, backend = detect_backend()
    import random as _random

    rng = _random.Random(12345)  # deterministic: the same data must give the same answer

    def score(params_list):
        rows = [as_row(p) for p in params_list]
        if backend in ("numpy", "jax"):
            import numpy as np

            mae = batch_mae_numpy(np, closes, cuts, horizon, rows)
            return [float(v) for v in mae]
        return batch_mae_python(closes, cuts, horizon, rows)

    # Seed the incumbent with a coarse evaluation of a handful of shapes, so the
    # iterative search starts somewhere sane instead of at random.
    seeds = []
    for w in ([1, 0, 0], [0, 1, 0], [0.34, 0.33, 0.33], [0, 0, 1]):
        for n in (10, 50, 100):
            for a in (0.2, 0.4, 0.6):
                seeds.append({"w": list(w), "n": float(n), "alpha": a, "damp": 0.9})
    seed_scores = score(seeds)
    best_i = min(range(len(seed_scores)), key=lambda i: seed_scores[i])
    best = seeds[best_i]
    best_score = seed_scores[best_i]

    history = [{"iteration": 0, "mae": round(best_score, 4), "params": to_params(best)}]
    evaluations = len(seeds)
    step = 0.35
    stale = 0

    for it in range(1, iterations + 1):
        batch = [propose(best, step, rng) for _ in range(batch_size)]
        scores = score(batch)
        evaluations += len(batch)
        j = min(range(len(scores)), key=lambda i: scores[i])
        if scores[j] < best_score:
            best, best_score = batch[j], scores[j]
            stale = 0
            step = clamp(step * 1.05, 0.02, 0.6)  # reward: allow bigger moves
        else:
            stale += 1
            step = clamp(step * 0.92, 0.006, 0.6)  # no progress: refine locally
        history.append({
            "iteration": it,
            "mae": round(best_score, 4),
            "step": round(step, 4),
            "params": to_params(best),
        })
        # Converged: the step has collapsed and nothing has improved for a while.
        if step <= 0.006 and stale > 8:
            break

    level = abs(closes[-1]) or 1.0
    print(json.dumps({
        "ok": True,
        "runner": "optimize.py",
        "device": device if backend != "python" else "cpu",
        "compute": backend,
        "iterations": len(history) - 1,
        "requested_iterations": iterations,
        "evaluations": evaluations,
        "batch_size": batch_size,
        "windows": len(cuts),
        "horizon": horizon,
        "best": {
            "params": to_params(best),
            "mae": round(best_score, 4),
            "mape_pct": round(100.0 * best_score / level, 4),
        },
        "history": history,
    }))


if __name__ == "__main__":
    main()
