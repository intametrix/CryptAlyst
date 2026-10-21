#!/usr/bin/env python3
"""oi_gate — observe-only price×OI quadrant scoring.

Classifies a symbol's 15m price×OI change into a quadrant and returns a score
ADJUSTMENT (never a filter) plus an envelope feature. Strategy-aware: momentum
prefers confirmation/accumulation (continuation), reversion prefers divergence/
distribution (fade). An extreme OI move is a climax tail and its adjustment is
capped (exhaustion, not signal). Complement-not-duplicate: this reads the price×
OI CONJUNCTION, distinct from oi_squeeze/oi_divergence which read OI level alone.
"""

CLIMAX_OI_PCT = 8.0   # |oi_chg_15m| >= this is a climax tail (exhaustion)
FLAT_PX_PCT = 0.05    # |price_chg_15m| < this is flat (no directional read)

# Per-quadrant score multiplier for a LONG entry, by strategy family. "default"
# is neutral (no OI weighting). Values around 1.0 so a short entry can invert
# them with (2.0 - mult).
LONG = {
    "default":   {"confirmation": 1.00, "accumulation": 1.00, "divergence": 1.00,
                  "distribution": 1.00, "climax": 1.00, "flat": 1.00},
    "momentum":  {"confirmation": 1.15, "accumulation": 1.10, "divergence": 0.85,
                  "distribution": 0.80, "climax": 0.70, "flat": 1.00},
    "reversion": {"confirmation": 0.90, "accumulation": 0.90, "divergence": 1.10,
                  "distribution": 1.10, "climax": 0.60, "flat": 1.00},
}


def quadrant(px_chg_pct, oi_chg_pct):
    """Classify the 15m price×OI change into a quadrant."""
    if abs(oi_chg_pct) >= CLIMAX_OI_PCT:
        return "climax"
    if abs(px_chg_pct) < FLAT_PX_PCT:
        return "flat"
    if px_chg_pct > 0:
        return "confirmation" if oi_chg_pct > 0 else "divergence"
    return "accumulation" if oi_chg_pct > 0 else "distribution"


def adjust(score, q, side, strategy="default"):
    """Return score adjusted by the quadrant (capped at climax).

    side is "buy"/"long" or "sell"/"short". A short inverts the table: a bearish
    quadrant (divergence/distribution) is favourable for it.
    """
    table = LONG.get(strategy, LONG["default"])
    mult = table.get(q, 1.0)
    if q == "climax":
        # exhaustion is a demote in BOTH directions, not a side-dependent signal:
        # skip the short inversion so a climax never becomes a boost.
        return round(score * mult, 6)
    if side in ("sell", "short"):
        mult = 2.0 - mult
    return round(score * mult, 6)


def feature(px_chg_pct, oi_chg_pct):
    """The envelope feature carried on a decision (observe-only, not a filter)."""
    q = quadrant(px_chg_pct, oi_chg_pct)
    return {"oi_quadrant": q, "oi_oi_chg_15m": round(oi_chg_pct, 3),
            "oi_px_chg_15m": round(px_chg_pct, 3)}
