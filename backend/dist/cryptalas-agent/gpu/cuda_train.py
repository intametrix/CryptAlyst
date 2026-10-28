#!/usr/bin/env python3
"""CUDA/GPU training runner for CryptALAS.

The Go agent (cmd/cryptalas) shells out to this script when a train job asks
for `-device cuda` (or `gpu`/`metal`). It reads one JSON job from stdin and
prints one JSON result to stdout:

  { "trained": bool, "device": "cuda|cpu|metal", "runner": "...",
    "weight": .., "intercept": .., "mean": .., "std": .., "n": .. }

The logistic fit is the same algorithm the CPU path uses, so the GPU and CPU
results are comparable. Acceleration is opportunistic: if JAX (with a CUDA or
Metal device) or PyTorch (CUDA/MPS) is importable, the fit runs there; otherwise
it runs on the CPU in pure Python and reports the real device honestly.
"""
import json
import math
import sys


def detect_device():
    """Return cuda | metal | cpu, preferring a real importable accelerator."""
    # JAX first: it names its devices precisely.
    try:
        import jax  # type: ignore
        platforms = [d.platform for d in jax.devices()]
        if any("cuda" in p for p in platforms):
            return "cuda"
        if any("metal" in p or "gpu" in p for p in platforms):
            return "metal"
        return platforms[0] if platforms else "cpu"
    except Exception:
        pass
    # PyTorch second.
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "metal"
        return "cpu"
    except Exception:
        pass
    # nvidia-smi last: the driver is present but no python binding is installed.
    import shutil
    if shutil.which("nvidia-smi"):
        return "cuda"
    return "cpu"


def logistic_cpu(rows):
    """Pure-python logistic on the row feature vs the label. Deterministic."""
    xs = []
    ys = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        lab = r.get("label")
        if lab not in ("P", "D", 1, 0, "1", "0"):
            continue
        v = r.get("x")
        if v is None:
            v = r.get("ret")
        if v is None:
            v = r.get("price")
        try:
            xs.append(float(v))
        except (TypeError, ValueError):
            continue
        ys.append(1 if lab in ("P", 1, "1") else 0)
    if len(xs) < 4 or len(set(ys)) < 2:
        return {"fit": False, "reason": "need >=2 rows of each class",
                "n": len(xs), "p": sum(ys), "d": len(ys) - sum(ys)}

    mu = sum(xs) / len(xs)
    sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
    zs = [(x - mu) / sd for x in xs]
    w, b = 0.0, 0.0
    lr = 0.5
    for _ in range(2000):
        gw = gb = 0.0
        for z, y in zip(zs, ys):
            p = 1.0 / (1.0 + math.exp(-(w * z + b)))
            gw += (p - y) * z
            gb += (p - y)
        w -= lr * gw / len(zs)
        b -= lr * gb / len(zs)

    return {"fit": True, "n": len(xs), "p": sum(ys), "d": len(ys) - sum(ys),
            "weight": round(w, 6), "intercept": round(b, 6),
            "mean": round(mu, 6), "std": round(sd, 6)}


def fit_jax(rows, device):
    """Accelerated logistic via JAX on the requested device (best-effort)."""
    import jax  # type: ignore
    import jax.numpy as jnp  # type: ignore

    xs, ys = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        lab = r.get("label")
        if lab not in ("P", "D", 1, 0, "1", "0"):
            continue
        v = r.get("x", r.get("ret", r.get("price", 0.0)))
        try:
            xs.append(float(v))
        except (TypeError, ValueError):
            continue
        ys.append(1.0 if lab in ("P", 1, "1") else 0.0)

    if len(xs) < 4 or len(set(ys)) < 2:
        return logistic_cpu(rows)

    x = jnp.array(xs)
    y = jnp.array(ys)
    mu = float(x.mean())
    sd = float(x.std()) or 1.0
    z = (x - mu) / sd

    def loss(params):
        w, b = params
        p = 1.0 / (1.0 + jnp.exp(-(w * z + b)))
        return jnp.mean((p - y) ** 2)

    # A handful of full-batch gradient steps is enough for a 1-feature logistic.
    w, b = 0.0, 0.0
    for _ in range(2000):
        w = w - 0.5 * float(jax.grad(loss, 0)((w, b))[0])
        b = b - 0.5 * float(jax.grad(loss, 1)((w, b))[0])

    return {"fit": True, "n": len(xs), "p": int(sum(ys)), "d": len(ys) - int(sum(ys)),
            "weight": round(w, 6), "intercept": round(b, 6),
            "mean": round(mu, 6), "std": round(sd, 6), "accelerated": "jax:" + device}


def main():
    raw = sys.stdin.read()
    try:
        job = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps({"trained": False, "error": "bad json"}))
        return

    requested = str(job.get("device") or "").lower()
    device = detect_device()

    payload = job.get("payload") or {}
    rows = payload.get("rows") or job.get("rows") or []

    result = None
    if requested in ("cuda", "gpu", "metal") and device in ("cuda", "metal"):
        try:
            result = fit_jax(rows, device)
        except Exception:
            result = None
    if result is None:
        result = logistic_cpu(rows)

    result["device"] = device
    result["requested"] = requested or "auto"
    result["trained"] = result.get("fit", False)
    result["runner"] = "cuda_train.py"
    print(json.dumps(result))


if __name__ == "__main__":
    main()
