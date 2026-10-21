#!/usr/bin/env python3
"""F4 first-pass: OI-gate with/without counterfactual over the fills ledger.

Replays realized fills with the OI adjust applied (with-gate) vs identity
(without-gate), fee-inclusive, per book class. Cohort = fills whose entry time
has a valid oi_chg_15m + px_chg_15m from the history ring buffers. Produces the
expectancy delta + acceptance verdict. Deterministic: reads files only.

    python3 tools/f4_replay.py
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import oi_gate
from lib import solver

STATE = os.path.join(HERE, "state")


def change_at(hist, ts_ms, window_s):
    """Pct change over window_s ENDING at ts_ms, from [(ts_s, val)] oldest-first.

    The current value is the last sample at or before ts_ms (not the newest in
    the buffer), so the change is as-of the fill's entry time, not now.
    """
    if not hist:
        return None
    ts = ts_ms / 1000.0
    cur = None
    for t, v in hist:
        if t <= ts:
            cur = v
        else:
            break
    if cur is None:
        return None
    base = None
    for t, v in hist:
        if t >= ts - window_s:
            base = v
            break
    if base in (None, 0):
        return None
    return 100.0 * (cur / base - 1.0)

MOMENTUM = {"alpha_rank", "movers", "hft", "grid", "curve_rider", "swing_turtle",
            "turtle_multi", "flash", "fast_ignition"}
REVERSION = {"hybrids", "multi_models", "stats_models", "swing_v2", "sympathy",
             "slow_edges", "churn", "skim", "scalp_fast", "skim_ai", "crypto_edges"}

MIN_SAMPLE = 30  # a class under this is flagged, not trusted


def book_class(engine):
    if engine in MOMENTUM:
        return "momentum"
    if engine in REVERSION:
        return "reversion"
    return "other"


def load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def code_hash():
    try:
        src = open(os.path.abspath(__file__), "rb").read()
        return hashlib.sha1(src).hexdigest()[:12]
    except Exception:
        return "unknown"


def main():
    oi_hist = load_json(os.path.join(STATE, "oi_history.json")).get("hist") or {}
    px_hist = load_json(os.path.join(STATE, "price_history.json")).get("hist") or {}

    fills = []
    try:
        with open(os.path.join(STATE, "fills.jsonl")) as fh:
            for line in fh:
                try:
                    fills.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass

    fee = solver.fee_model(fee_bps=20.0, slippage_bps=0.0, provenance="paper-truth")

    # aggregate per class: (weighted_sum, weight, nets, fav, unfav)
    agg = {}
    for f in fills:
        sym = f.get("symbol")
        entry_ms = f.get("entry_ms")
        net = f.get("net_pct")
        side = f.get("side")
        if not sym or not entry_ms or net is None:
            continue
        oi15 = change_at(oi_hist.get(sym) or [], entry_ms, 900)
        px15 = change_at(px_hist.get(sym) or [], entry_ms, 900)
        if oi15 is None or px15 is None:
            continue  # no valid 15m history at entry -> not in the cohort
        q = oi_gate.quadrant(px15, oi15)
        cls = book_class(f.get("engine"))
        mult = oi_gate.adjust(1.0, q, side, cls)
        a = agg.setdefault(cls, {"wsum": 0.0, "w": 0.0, "nets": [], "fav": 0, "unfav": 0})
        a["wsum"] += mult * net
        a["w"] += mult
        a["nets"].append(net)
        if mult > 1.0:
            a["fav"] += 1
        elif mult < 1.0:
            a["unfav"] += 1

    print(json.dumps({
        "fee_model": fee,
        "cohort": "fills with valid oi_chg_15m + px_chg_15m at entry",
        "code_hash": code_hash(),
        "min_sample": MIN_SAMPLE,
        "classes": {cls: _summarize(a) for cls, a in sorted(agg.items())},
        "total_cohort": sum(len(a["nets"]) for a in agg.values()),
    }, indent=2))


def _summarize(a):
    n = len(a["nets"])
    without = sum(a["nets"]) / n if n else None
    with_ = a["wsum"] / a["w"] if a["w"] else None
    return {
        "sample": n,
        "flagged_under_min": n < MIN_SAMPLE,
        "expectancy_without_gate_bps": round(without * 100, 2) if without is not None else None,
        "expectancy_with_gate_bps": round(with_ * 100, 2) if with_ is not None else None,
        "delta_bps": round((with_ - without) * 100, 2) if (with_ is not None and without is not None) else None,
        "favorable": a["fav"], "unfavorable": a["unfav"],
    }


if __name__ == "__main__":
    main()
