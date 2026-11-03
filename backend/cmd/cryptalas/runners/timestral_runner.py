"""Timestral forecasting runner.

Mistral's Timestral is a time-series foundation model: a pretrained patch
transformer that forecasts zero-shot, so it needs no fitting on our data. It is
the only model here that was trained on forecasting rather than assembled from
moving averages, which is why it is worth the dependency and the 837MB of
weights.

Protocol is the same as forecast.py: read one JSON job on stdin, write one JSON
object on stdout.
"""
import json
import os
import sys
import traceback

# The runner lives in a cache directory that Python puts first on sys.path, and
# an older copy of this job named `timestral.py` may still sit there. That shadowed
# the installed package: `import timestral` found the stale script instead of the
# library. Drop this directory from the import path before importing anything.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]

# The job protocol is one JSON object on stdout. HuggingFace prints download
# progress and warnings on the same pipe, which broke parsing, so they are
# silenced rather than tolerated: noise on stdout is a protocol violation.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
import warnings

warnings.filterwarnings("ignore")

import numpy as np

MODELS = {
    "timestral5": "mistralai/Timestral-5M",
    "timestral28": "mistralai/Timestral-28M",
    "timestral123": "mistralai/Timestral-123M",
    "timestral209": "mistralai/Timestral-209M",
    "timestral": "mistralai/Timestral-209M",
}

_CACHE = {}


def load(repo, device):
    key = (repo, device)
    if key in _CACHE:
        return _CACHE[key]
    from timestral import TimestralForecaster

    forecaster = TimestralForecaster.from_pretrained(repo, device=device)
    _CACHE[key] = forecaster
    return forecaster


def to_np(v):
    """Torch tensors on CUDA cannot go straight to numpy; copy to host first."""
    try:
        return v.detach().cpu().numpy()
    except AttributeError:
        return np.asarray(v)


def pick_device(requested):
    if requested and requested not in ("auto", ""):
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def series_from(payload, job):
    """Closes from whichever shape the caller used."""
    series = payload.get("series") or job.get("series") or []
    closes = []
    for row in series:
        if isinstance(row, dict):
            v = row.get("c") or row.get("close") or row.get("v")
            if v is not None:
                closes.append(float(v))
        elif isinstance(row, (int, float)):
            closes.append(float(row))
    if not closes:
        closes = [float(x) for x in (payload.get("closes") or [])]
    return closes


def main():
    raw = sys.stdin.read()
    job = json.loads(raw) if raw.strip() else {}
    payload = job.get("payload") or {}
    method = str(payload.get("method") or job.get("model") or "timestral").lower()
    horizon = int(payload.get("horizon") or job.get("horizon") or 24)
    horizon = max(1, min(horizon, 1024))

    repo = MODELS.get(method, MODELS["timestral"])
    closes = series_from(payload, job)
    if len(closes) < 64:
        print(json.dumps({"ok": False, "error": "need at least 64 closes, got %d" % len(closes)}))
        return

    device = pick_device(payload.get("device") or os.environ.get("TIMESTRAL_DEVICE"))
    forecaster = load(repo, device)

    # Context is (series, variates, time); the most recent steps matter, and the
    # model keeps the latest if the context is longer than its trained window.
    context = np.asarray(closes, dtype=np.float64).reshape(1, 1, -1)
    out = forecaster.forecast(context, prediction_length=horizon)

    median = to_np(out.median).reshape(-1)[:horizon]
    result = {
        "ok": True,
        "forecast": [float(v) for v in median],
        "horizon": horizon,
        "method": method,
        "model": repo,
        "device": device,
        "compute": "torch",
        "last": float(closes[-1]),
        "runner": "timestral.py",
    }
    try:
        result["quantiles"] = {
            "0.1": [float(v) for v in to_np(out.quantile(0.1)).reshape(-1)[:horizon]],
            "0.9": [float(v) for v in to_np(out.quantile(0.9)).reshape(-1)[:horizon]],
        }
    except Exception:
        pass
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - the job protocol needs a JSON error
        print(json.dumps({"ok": False, "error": str(exc), "trace": traceback.format_exc()[-800:]}))
