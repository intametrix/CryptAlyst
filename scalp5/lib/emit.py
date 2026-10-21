"""emit - the one place engines publish outcomes.

model.fill is the event the research loop depends on: it carries the entry
feature vector, the exit, the reason and the realised net. fit_weights reads it.

Every decision now carries a decision_id and every fill carries a fill_id plus
the parent decision_id, so the decision->fill->ledger chain is auditable. The
decisions ledger is state/decisions.jsonl; export_decisions() joins the two
ledgers and flags orphans (a fill whose decision_id references no decision).
"""
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")
FILLS = os.path.join(STATE, "fills.jsonl")
DECISIONS = os.path.join(STATE, "decisions.jsonl")

try:
    from lib import bus
except Exception:
    bus = None

_seq = [0]


def new_id(prefix):
    """A unique, time-sortable id: <prefix>-<epoch_ms>-<random>."""
    _seq[0] += 1
    return "%s-%d-%s" % (prefix, int(time.time() * 1000), uuid.uuid4().hex[:6])


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
           "slippage_bps": trade.get("slippage_bps"),
           "fill_id": trade.get("fill_id") or new_id("f"),
           "decision_id": trade.get("decision_id")}
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


def decision(engine, symbol, side, allowed, reason, confidence=None, decision_id=None):
    """Publish a decision and append it to the durable decisions ledger.

    Returns the decision_id so the caller can thread it into the eventual fill.
    """
    if decision_id is None:
        decision_id = new_id("d")
    rec = {"decision_id": decision_id, "engine": engine, "symbol": symbol,
           "side": side, "allowed": bool(allowed), "reason": reason,
           "confidence": confidence, "ts_ms": int(time.time() * 1000)}
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(DECISIONS, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    if bus:
        try:
            bus.pub("model.decision", symbol, rec, engine)
        except Exception:
            pass
    return decision_id


def export_decisions():
    """Join the decisions and fills ledgers; flag orphan fills.

    A fill is an orphan when it names a decision_id that no decision carries.
    Fills with no decision_id are legacy/untraced, counted separately, not
    orphans. Zero-orphan acceptance is the target for post-instrumentation fills.
    """
    decisions = {}
    try:
        with open(DECISIONS) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                did = d.get("decision_id")
                if did:
                    decisions[did] = d
    except Exception:
        pass

    fills = []
    orphans = 0
    matched = 0
    untraced = 0
    try:
        with open(FILLS) as fh:
            for line in fh:
                try:
                    f = json.loads(line)
                except Exception:
                    continue
                fills.append(f)
                did = f.get("decision_id")
                if not did:
                    untraced += 1
                elif did in decisions:
                    matched += 1
                else:
                    orphans += 1
    except Exception:
        pass

    return {"decisions": len(decisions), "fills": len(fills),
            "matched": matched, "orphans": orphans, "untraced": untraced,
            "decision_ids": sorted(decisions)}
