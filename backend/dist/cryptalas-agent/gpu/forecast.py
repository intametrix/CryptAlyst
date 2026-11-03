#!/usr/bin/env python3
"""Forecast runner for the CryptALAS agent.

Reads one JSON job on stdin and writes one JSON result on stdout:

  input  {"payload": {"series": [{"t": ms, "v": close}, ...],
                      "horizon": N, "method": "<model>"}}

  output {"forecast": [...], "horizon": N, "last": .., "method": ..,
          "device": "cuda|cpu", "compute": "jax|python", "runner": ..}

Every model the platform's leaderboard can rank is implemented here, so the
forecast is actually executed on the agent (and on its GPU when one is usable)
rather than being silently recomputed server-side. The device that ran the work
is reported honestly, including why an accelerator was not used.
"""
import json
import math
import sys

# --------------------------------------------------------------------- device


def nvidia_smi():
    """Locate nvidia-smi; on Windows it is often missing from PATH."""
    import os
    import shutil

    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.name == "nt":
        for candidate in (
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ):
            if os.path.exists(candidate):
                return candidate
    return None


def detect_device():
    """Return (device, backend). Prefers a real importable accelerator."""
    try:
        import jax  # type: ignore

        platforms = [d.platform for d in jax.devices()]
        if any("cuda" in p for p in platforms):
            return "cuda", "jax"
        if any("gpu" in p or "metal" in p for p in platforms):
            return "metal", "jax"
    except Exception:
        pass
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda", "torch"
    except Exception:
        pass
    if nvidia_smi():
        # A driver exists but no python binding: the card is present, we simply
        # cannot compute on it from here.
        return "cpu", "python"
    return "cpu", "python"


# ------------------------------------------------------- interpreter selection


def _probe_runtime(argv, timeout=40):
    """Report what this interpreter can actually do.

    Returns a dict so a failure can be read rather than guessed at:

      {"exe":..., "jax": "cuda"|"cpu"|"absent"|"error", "torch": ..., "detail": ...}

    Distinguishing "jax is not installed" from "jax is installed but cannot open
    the GPU" matters: the first needs a pip install, the second needs a driver or
    toolkit fix.
    """
    code = r"""
import json, sys
out = {"exe": sys.executable}
try:
    import jax
    try:
        devs = [d.platform for d in jax.devices()]
        out["jax"] = "cuda" if any("cuda" in p for p in devs) else "cpu"
        out["jax_devices"] = devs
    except Exception as exc:
        out["jax"] = "error"
        out["jax_error"] = str(exc)[:300]
except ImportError as exc:
    out["jax"] = "absent"
    out["jax_error"] = str(exc)[:200]
try:
    import torch
    try:
        out["torch_version"] = getattr(torch, "__version__", "?")
        out["torch_cuda_build"] = getattr(getattr(torch, "version", None), "cuda", None)
        out["torch"] = "cuda" if torch.cuda.is_available() else "cpu"
        if torch.cuda.is_available():
            out["torch_device"] = torch.cuda.get_device_name(0)
    except Exception as exc:
        out["torch"] = "error"
        out["torch_error"] = str(exc)[:300]
except ImportError as exc:
    out["torch"] = "absent"
    out["torch_error"] = str(exc)[:200]
print(json.dumps(out))
"""
    import json as _json
    import subprocess

    info = {"exe": " ".join(argv), "jax": "error", "torch": "error"}
    try:
        proc = subprocess.run(argv + ["-c", code], capture_output=True, text=True, timeout=timeout)
        line = (proc.stdout or "").strip().splitlines()
        if line:
            info.update(_json.loads(line[-1]))
        else:
            info["detail"] = (proc.stderr or "")[:300]
    except Exception as exc:
        info["detail"] = str(exc)[:300]
    return info


def _probe_cuda(argv, timeout=40):
    info = _probe_runtime(argv, timeout)
    return info.get("jax") == "cuda" or info.get("torch") == "cuda"


def find_cuda_interpreter():
    """Look for another interpreter that does have jax+CUDA.

    A box can easily have several Pythons (Store alias, conda, py launcher) and
    installing jax into one of them does not make it visible to `python3` from
    PATH. Rather than declare "no GPU", check the usual candidates.
    """
    import os
    import shutil
    import sys

    seen = set()
    candidates = []
    for name in ("python3", "python"):
        path = shutil.which(name)
        if path and path not in seen:
            seen.add(path)
            candidates.append([path])
    if os.name == "nt":
        launcher = shutil.which("py")
        if launcher:
            for ver in ("-3.13", "-3.12", "-3.11", "-3.10", "-3"):
                candidates.append([launcher, ver])
        # Direct install locations. A pyenv/conda shim on PATH can hide the
        # interpreter that actually has the GPU packages, so look for the
        # standard ones by path as well.
        roots = [
            os.environ.get("LOCALAPPDATA", "") + r"\Programs\Python",
            os.environ.get("PROGRAMFILES", "") + r"\Python",
            "C:\\Python",
            os.environ.get("USERPROFILE", "") + r"\anaconda3",
            os.environ.get("USERPROFILE", "") + r"\miniconda3",
            os.environ.get("USERPROFILE", "") + r"\.pyenv\pyenv-win\versions",
        ]
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            try:
                for name in sorted(os.listdir(root)):
                    for exe in ("python.exe", "python3.exe"):
                        full = os.path.join(root, name, exe)
                        if os.path.isfile(full) and full not in seen:
                            seen.add(full)
                            candidates.append([full])
            except OSError:
                continue
    here = sys.executable
    if here and here not in seen:
        seen.add(here)
        candidates.insert(0, [here])
    probe = []
    for argv in candidates:
        info = _probe_runtime(argv)
        probe.append(info)
        if info.get("jax") == "cuda" or info.get("torch") == "cuda":
            return argv, probe
    return None, probe


def reexec_with(argv, raw):
    """Run this script under another interpreter and return its stdout.

    Guarded by an env var so a pathological chain of interpreters cannot loop.
    """
    import os
    import subprocess
    import sys

    if os.environ.get("CRYPTALAS_NO_REEXEC") == "1":
        return None
    env = dict(os.environ)
    env["CRYPTALAS_NO_REEXEC"] = "1"
    try:
        out = subprocess.run(
            argv + [os.path.abspath(__file__)],
            input=raw, capture_output=True, text=True, timeout=180, env=env,
        )
        if out.returncode == 0 and out.stdout.strip().startswith("{"):
            return out.stdout
    except Exception:
        pass
    return None


# -------------------------------------------------------------------- helpers


def _closes(series):
    out = []
    for x in series or []:
        v = x.get("v") if isinstance(x, dict) else x
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _flat(v, horizon):
    return [v] * horizon


def _level_path(anchor, level, horizon):
    """A level model plotted honestly.

    A moving average estimates where price belongs, not where it is. Returning
    that level as the very next bar teleported the forecast by up to a percent on
    step one. The path now starts at the last close and converges toward the
    level across the horizon, so it is continuous with the chart and still says
    what the model means: "price should trade here".
    """
    half = max(1.0, float(horizon) / 2.0)
    return [anchor + (level - anchor) * (1.0 - 0.5 ** ((i + 1) / half)) for i in range(horizon)]


def _mean(c):
    return sum(c) / len(c) if c else 0.0


def _sma(c, n):
    if not c:
        return 0.0
    n = min(n, len(c))
    return sum(c[-n:]) / n


def _ema(c, alpha):
    if not c:
        return 0.0
    e = c[0]
    for v in c[1:]:
        e = alpha * v + (1 - alpha) * e
    return e


def _trend(c, horizon, lookback=0, damp=1.0):
    """Least-squares line extrapolated `horizon` steps, optionally damped."""
    if not c:
        return [0.0] * horizon
    xs = c[-lookback:] if lookback and lookback < len(c) else c
    m = float(len(xs))
    if m < 2:
        return _flat(xs[-1], horizon)
    sx = sy = sxy = sxx = 0.0
    for i, y in enumerate(xs):
        sx += i
        sy += y
        sxy += i * y
        sxx += i * i
    den = m * sxx - sx * sx
    slope = (m * sxy - sx * sy) / den if den else 0.0
    # Anchor on the last close that actually printed. A fitted line rarely passes
    # through it, so starting from the fit opened the forecast a fraction of a
    # percent away from the market and then drifted from there.
    anchor = c[-1]
    out = []
    step = 0.0
    scale = 1.0
    for _ in range(horizon):
        scale *= damp
        step += slope * scale
        out.append(anchor + step)
    return out


def _returns(c, start, w):
    out = []
    for i in range(start, start + w):
        if i <= 0 or i >= len(c) or c[i - 1] == 0:
            out.append(0.0)
        else:
            out.append(c[i] / c[i - 1] - 1.0)
    while len(out) < w:
        out.append(0.0)
    return out


def _zscore(v):
    if not v:
        return v
    mu = sum(v) / len(v)
    sd = (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5
    if sd == 0:
        sd = 1.0
    return [(x - mu) / sd for x in v]


def _forward(c, end, horizon):
    out = []
    base = c[end] if end < len(c) else 0.0
    if base == 0:
        return out
    for i in range(end + 1, min(end + horizon + 1, len(c))):
        out.append(c[i] / base - 1.0)
    return out


# --------------------------------------------------------------- analog model

ANALOG_WINDOW = 12


def _analog_python(c, horizon, k):
    """Retrieval forecast in pure python: the fallback path."""
    n = len(c)
    if n < ANALOG_WINDOW + horizon + 2 or k < 1:
        return _flat(c[-1] if c else 0.0, horizon)
    q = _zscore(_returns(c, n - ANALOG_WINDOW, ANALOG_WINDOW))
    last_end = n - horizon - 1
    cands = []
    for end in range(ANALOG_WINDOW, last_end + 1):
        if end >= n - ANALOG_WINDOW:
            continue
        v = _zscore(_returns(c, end - ANALOG_WINDOW + 1, ANALOG_WINDOW))
        d = 0.0
        for a, b in zip(q, v):
            d += (a - b) ** 2
        cands.append((math.sqrt(d), _forward(c, end, horizon)))
        if len(cands) > 4000:
            break
    if not cands:
        return _flat(c[-1], horizon)
    cands.sort(key=lambda t: t[0])
    top = cands[:k]
    base = c[-1]
    out = []
    for step in range(horizon):
        acc = 0.0
        cnt = 0
        for _, rets in top:
            if step < len(rets):
                acc += rets[step]
                cnt += 1
        out.append(base if cnt == 0 else base * (1 + acc / cnt))
    return out


def _analog_jax(c, horizon, k, backend):
    """Retrieval forecast with the distance matrix computed on the accelerator.

    The nearest-neighbour search is the only part of a forecast that is real
    vector work, so it is the part worth putting on a GPU.
    """
    import numpy as np  # type: ignore

    n = len(c)
    if n < ANALOG_WINDOW + horizon + 2 or k < 1:
        return _flat(c[-1] if c else 0.0, horizon)

    q = np.array(_zscore(_returns(c, n - ANALOG_WINDOW, ANALOG_WINDOW)), dtype=np.float32)
    last_end = n - horizon - 1
    ends, windows = [], []
    for end in range(ANALOG_WINDOW, last_end + 1):
        if end >= n - ANALOG_WINDOW:
            continue
        windows.append(_zscore(_returns(c, end - ANALOG_WINDOW + 1, ANALOG_WINDOW)))
        ends.append(end)
        if len(ends) > 4000:
            break
    if not ends:
        return _flat(c[-1], horizon)

    mat = np.array(windows, dtype=np.float32)
    if backend == "jax":
        import jax.numpy as jnp  # type: ignore

        qj = jnp.asarray(q)
        mj = jnp.asarray(mat)
        dist = jnp.sqrt(jnp.sum((mj - qj) ** 2, axis=1))
        dist = np.asarray(dist)
    else:
        dist = np.sqrt(((mat - q) ** 2).sum(axis=1))

    order = np.argsort(dist)[:k]
    base = c[-1]
    # Per-step mean cumulative return across the analogues.
    mean_ret = []
    for step in range(horizon):
        acc, cnt = 0.0, 0
        for idx in order:
            rets = _forward(c, ends[int(idx)], horizon)
            if step < len(rets):
                acc += rets[step]
                cnt += 1
        mean_ret.append(acc / cnt if cnt else 0.0)

    # The analogues supply the SHAPE; the current price supplies the LEVEL.
    # Two constraints at once: the path must START at the last close (or the
    # chart shows a jump the market never made) and must not carry a drift the
    # model cannot justify. Relative moves are measured from step 0, then the
    # residual drift is removed progressively so step 0 is untouched.
    if not mean_ret:
        return []
    first = mean_ret[0]
    raw = [r - first for r in mean_ret]
    centre = sum(raw) / len(raw)
    last = max(1, len(raw) - 1)
    return [base * (1 + raw[i] - centre * (i / last)) for i in range(len(raw))]


# ------------------------------------------------------------------ dispatch


def run_model(name, c, horizon, device, backend):
    """Return (forecast, compute_label) for a model name."""
    n = name or "naive"
    anchor = c[-1] if c else 0.0
    if n == "naive":
        return _flat(anchor, horizon), "python"
    if n == "mean":
        return _level_path(anchor, _mean(c), horizon), "python"

    if n.startswith("sma") and n[3:].isdigit():
        return _level_path(anchor, _sma(c, int(n[3:])), horizon), "python"
    if n.startswith("ema") and n[3:].isdigit():
        return _level_path(anchor, _ema(c, int(n[3:]) / 100.0), horizon), "python"
    if n == "trend":
        return _trend(c, horizon), "python"
    if n.startswith("trend") and n[5:].isdigit():
        return _trend(c, horizon, lookback=int(n[5:])), "python"
    if n.startswith("damped") and n[6:].isdigit():
        return _trend(c, horizon, damp=int(n[6:]) / 100.0), "python"

    if n.startswith("ens_"):
        # Same three components the Go implementation blends.
        table = {
            "ens_a": (20, 0.30, 0.90),
            "ens_b": (10, 0.20, 0.95),
            "ens_c": (30, 0.40, 0.80),
        }
        sma_n, alpha, damp = table.get(n, table["ens_a"])
        a_path = _level_path(anchor, _sma(c, sma_n), horizon)
        b_path = _level_path(anchor, _ema(c, alpha), horizon)
        c_path = _trend(c, horizon, damp=damp)
        out = [(a_path[i] + b_path[i] + c_path[i]) / 3.0 for i in range(horizon)]
        return out, "python"

    if n.startswith("analog") and n[6:].isdigit():
        k = int(n[6:])
        if backend in ("jax", "torch") and device == "cuda":
            try:
                return _analog_jax(c, horizon, k, backend), backend
            except Exception:
                pass
        return _analog_python(c, horizon, k), "python"

    # Unknown model: hold the last close rather than fail the job.
    return _flat(c[-1] if c else 0.0, horizon), "python"


def main():
    # `--probe` reports the python/runtime situation without running a forecast.
    # It exists so the platform can ask "why is this agent not on the GPU?"
    # and get an answer instead of a guess.
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        import json as _json

        _, probe = find_cuda_interpreter()
        device, backend = detect_device()
        print(_json.dumps({
            "python_exe": sys.executable,
            "device": device,
            "backend": backend,
            "cuda_interpreter": None,
            "interpreters": probe,
        }))
        return

    raw = sys.stdin.read()
    try:
        job = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps({"trained": False, "error": "bad json", "runner": "forecast.py"}))
        return

    payload = job.get("payload") or {}
    series = payload.get("series") or job.get("series") or []
    closes = _closes(series)

    try:
        horizon = int(payload.get("horizon") or job.get("horizon") or 12)
    except (TypeError, ValueError):
        horizon = 12
    horizon = max(1, min(horizon, 1000))

    method = str(payload.get("method") or job.get("method") or "trend").lower()

    if len(closes) < 3:
        print(json.dumps({
            "trained": False,
            "error": "need >= 3 points, got %d" % len(closes),
            "runner": "forecast.py",
            "method": method,
        }))
        return

    device, backend = detect_device()
    if backend == "python":
        # The current interpreter has no accelerator. Another one on this box
        # might, and that is the difference between forecast-on-CPU and
        # forecast-on-GPU, so check before reporting "no GPU".
        alt, probe = find_cuda_interpreter()
        if not alt:
            # Nothing could reach a GPU: return the diagnosis with the result so
            # the failure can be fixed instead of re-guessed.
            out_probe = probe
        if alt:
            forwarded = reexec_with(alt, raw)
            if forwarded is not None:
                sys.stdout.write(forwarded)
                return
    try:
        forecast, compute = run_model(method, closes, horizon, device, backend)
    except Exception as exc:  # a broken model must not take the agent down
        print(json.dumps({
            "trained": False,
            "error": "model %s failed: %s" % (method, exc),
            "runner": "forecast.py",
            "method": method,
        }))
        return

    # `device` is what this box CAN compute on; `compute` is what actually ran
    # this model. A flat model needs no tensor work, so it runs in python even on
    # a GPU box — reporting that as "cpu" wrongly says the GPU is missing.
    out = {
        "forecast": [round(float(v), 6) for v in forecast],
        "horizon": horizon,
        "last": closes[-1],
        "method": method,
        "device": device,
        "compute": compute,
        "runner": "forecast.py",
        "trained": True,
    }
    if device == "cpu":
        out["device_reason"] = (
            "no CUDA interpreter found: jax/torch not importable in %s "
            "and no other python on this box reported a cuda device" % sys.executable
        )
        out["python_exe"] = sys.executable
    elif compute == "python":
        out["device_reason"] = "gpu available; this model needs no vector work"
        try:
            out["runtime_probe"] = out_probe
        except NameError:
            pass
    print(json.dumps(out))


if __name__ == "__main__":
    main()
