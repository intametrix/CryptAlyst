"""slippage - measure actual round-trip slippage from the fills ledger.

A fill is measurable only when it records both the expected (signal) price and
the recorded fill price. Fills without both are untracked, not zero-slippage:
the hand-constant 0 in slippage_bps is not a measurement.
"""
import json


def measure(fills_path):
    """Return slippage stats over fills that have both signal_price and entry.

    Adverse-slippage convention: positive bps = the fill was worse than the
    signal (paid more on a buy, received less on a sell).
    """
    measured = 0
    untracked = 0
    bps = []
    try:
        with open(fills_path) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                sp = d.get("signal_price")
                entry = d.get("entry")
                if not sp or not entry:
                    untracked += 1
                    continue
                side = d.get("side")
                frac = (entry / sp - 1.0) if side == "buy" else (1.0 - entry / sp)
                bps.append(round(frac * 10000.0, 2))  # fraction -> basis points
                measured += 1
    except Exception:
        pass
    if not bps:
        return {"measured": 0, "untracked": untracked,
                "mean_bps": None, "p50_bps": None, "p90_bps": None}
    bps.sort()
    return {"measured": measured, "untracked": untracked,
            "mean_bps": round(sum(bps) / len(bps), 2),
            "p50_bps": bps[len(bps) // 2],
            "p90_bps": bps[min(len(bps) - 1, int(len(bps) * 0.9))]}
