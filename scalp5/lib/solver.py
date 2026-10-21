"""solver - tier-1 backtest optimiser core (observe-only).

A typed, bounded parameter space; a first-class fee model; and a reproducible
recommendation. Tier-1 recommendations are labelled backtest_only and never
reach owner-promote for books with real ledger history (tier-2 replay is the
gate). This module is the pure core the optimiser service calls; no network.
"""

VERSION = 1

# The declared parameter space: every tunable knob is typed and bounded. The
# solver may only search within these; it never invents a parameter.
PARAM_SPACE = {
    "lock_pct":    {"type": "float", "low": 0.1, "high": 2.0, "default": 0.4},
    "stop_pct":    {"type": "float", "low": 0.2, "high": 3.0, "default": 1.0},
    "clock_s":     {"type": "int", "low": 60, "high": 3600, "default": 300},
    "spacing_pct": {"type": "float", "low": 0.05, "high": 1.0, "default": 0.25},
}


def bounds_ok(params):
    """Every param must be declared and within its bounds."""
    if not isinstance(params, dict) or not params:
        return False
    for k, v in params.items():
        spec = PARAM_SPACE.get(k)
        if spec is None:
            return False
        if not (spec["low"] <= v <= spec["high"]):
            return False
    return True


def fee_model(fee_bps=20.0, slippage_bps=0.0, provenance="hand-constant"):
    """First-class fee input; provenance names where the numbers come from."""
    return {"fee_bps": fee_bps, "slippage_bps": slippage_bps,
            "round_trip_bps": fee_bps + slippage_bps, "provenance": provenance}


def objective(net_pct_list, round_trip_bps=20.0):
    """Risk-adjusted fee-inclusive objective: mean net minus a drawdown penalty.

    net_pct_list is already fee-inclusive per-trade returns. The drawdown penalty
    keeps the solver from chasing one lucky tail.
    """
    if not net_pct_list:
        return None
    mean = sum(net_pct_list) / len(net_pct_list)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in net_pct_list:
        cum += t
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(mean - 0.5 * max_dd, 6)


def recommend(model, params, objective_score, evidence, code_hash, budget,
              priors, window, tier="backtest_only"):
    """A reproducible recommendation.

    A recommendation without code_hash + budget + priors + window cannot be
    replayed, so it is an opinion, not a recommendation (reproducible=False).
    """
    reproducible = bool(code_hash and budget and priors and window)
    return {"schema_version": VERSION, "model": model, "params": params,
            "objective": objective_score, "evidence": evidence,
            "code_hash": code_hash, "budget": budget, "priors": priors,
            "window": window, "tier": tier, "reproducible": reproducible}
