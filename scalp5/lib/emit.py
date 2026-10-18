"""emit - the one place engines publish outcomes.

model.fill is the event the research loop depends on: it carries the entry
feature vector, the exit, the reason and the realised net. fit_weights reads it.
"""
import json, os, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")
FILLS = os.path.join(STATE, "fills.jsonl")

try:
    from lib import bus
except Exception:
    bus = None


def fill(engine, trade):
    """Publish a closed trade and append it to the durable fills log."""
    rec = {"engine": engine,
           "symbol": trade.get("symbol"), "side": trade.get("side"),
           "entry": trade.get("entry"), "exit": trade.get("exit"),
           "gross_pct": trade.get("gross_pct"), "net_pct": trade.get("net_pct"),
           "peak_pct": trade.get("peak_pct"), "reason": trade.get("reason"),
           "held_s": trade.get("held_s"), "notional": trade.get("notional"),
           "pnl_usd": trade.get("pnl_usd"),
           "entry_ms": trade.get("entry_ms"), "exit_ms": trade.get("exit_ms") or int(time.time()*1000),
           "features": trade.get("features") or trade.get("feat") or {},
           "rank_score": trade.get("rank_score"), "why": trade.get("why"),
           "slippage_bps": trade.get("slippage_bps")}
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(FILLS, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    if bus:
        try:
            bus.pub("model.fill", rec.get("symbol"), rec, engine)
        except Exception:
            pass
    return rec


def decision(engine, symbol, side, allowed, reason, confidence=None):
    if bus:
        try:
            bus.pub("model.decision", symbol,
                    {"engine": engine, "symbol": symbol, "side": side,
                     "allowed": bool(allowed), "reason": reason,
                     "confidence": confidence}, engine)
        except Exception:
            pass
