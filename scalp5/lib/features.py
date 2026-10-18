"""features - turn raw signals into a comparable matrix.

Design pattern: Pipeline (extract -> transform -> normalise -> score).

The important change from the old models: thresholds are replaced by
cross-sectional ranks, so "high" means high *relative to the universe right now*,
not against a constant someone guessed.
"""
import math

FIELDS = [
    ("book_imbalance", +1), ("buy_ratio", +1), ("smart_vs_dumb", +1),
    ("oi_change_1h_pct", +1), ("flow_conf", +1), ("score", +1),
    ("taker_ratio", +1), ("px_vs_sma50", +1), ("mtf_agreement", +1),
    ("funding_pct", -1), ("spread_bps", -1), ("slippage_bps_5k", -1),
]


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def matrix(rows):
    """rows: list of signal dicts -> {symbol: {feature: rank in -1..1}}."""
    syms = [r.get("symbol") for r in rows if r.get("symbol")]
    out = {s: {} for s in syms}
    for field, sign in FIELDS:
        vals = []
        for r in rows:
            v = _num(r.get(field))
            if v is not None:
                vals.append((r.get("symbol"), v * sign))
        if len(vals) < 3:
            continue
        vals.sort(key=lambda kv: kv[1])
        n = len(vals)
        for i, (sym, _v) in enumerate(vals):
            if sym in out:
                out[sym][field] = round(2 * (i / (n - 1)) - 1, 4)   # -1 .. +1
    return out


WEIGHTS_PATH = None
_W = {"data": None, "ts": 0.0}


def load_weights():
    """Fitted weights if a fit exists, otherwise an equal-weight prior."""
    global WEIGHTS_PATH
    import json, os, time
    if WEIGHTS_PATH is None:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        WEIGHTS_PATH = os.path.join(here, "state", "weights.json")
    if _W["data"] is not None and time.time() - _W["ts"] < 30:
        return _W["data"]
    try:
        with open(WEIGHTS_PATH) as fh:
            d = json.load(fh)
        w = d.get("weights") or {}
        _W["data"] = w if w else None
        _W["ts"] = time.time()
        return _W["data"]
    except Exception:
        _W["data"] = None
        return None


def composite(features, weights=None):
    """Weighted sum of the normalised features; missing values count as neutral."""
    weights = weights or load_weights() or {f: 1.0 / len(FIELDS) for f, _ in FIELDS}
    total = 0.0
    used = 0.0
    for field, w in weights.items():
        if field in features:
            total += features[field] * w
            used += abs(w)
    return round(total / used, 4) if used else 0.0
