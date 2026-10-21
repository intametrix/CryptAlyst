#!/usr/bin/env python3
"""risk - the portfolio governor. Every engine asks before it opens.

Rules enforced here, in one place instead of scattered across engines:

  exposure     gross notional must stay under a multiple of book equity
  concentration no single symbol above a share of the book
  daily loss   if the fleet is down more than the limit, everything is blocked
  cost floor   expected move must clear fee + measured slippage + a margin
  correlation  cap the number of open positions in the same direction

State is kept in state/risk.json so the dashboard can show why a trade was
refused rather than silently skipping it.
"""
import json, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
PATH = os.path.join(STATE, "risk.json")

DEFAULTS = {
    "execution_mode": "maker",   # post-only limits, 0.02% per side
    "max_gross_multiple": 3.0,     # total notional / total equity
    "max_symbol_share": 0.10,      # one symbol / book equity
    "max_same_direction": 12,      # concurrent positions one way
    "daily_loss_limit_pct": 3.0,   # fleet-wide stop
    "fee_pct": 0.04,               # maker round trip (taker 0.20)
    "min_edge_pct": 0.05,          # required edge after costs
    "cost_buffer_pct": 0.02,       # safety margin on top of costs
    "max_forecast_age_s": 1200,    # a forecast built on 2.5 stale 15m bars is blind
    "max_signal_age_s": 90,        # signals refresh every 12s; older means missing
}
_CACHE = {"ts": 0.0, "books": [], "state": {}}


def load_books():
    now = time.time()
    if now - _CACHE["ts"] < 1.0:
        return _CACHE["books"]
    books = []
    for fn in sorted(os.listdir(STATE)):
        if not (fn.startswith("pack_") or fn.startswith("alpha_")) or not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(STATE, fn)) as fh:
                books.append(json.load(fh))
        except Exception:
            continue
    _CACHE["books"], _CACHE["ts"] = books, now
    return books


def snapshot():
    books = load_books()
    equity = sum(b.get("equity") or 0 for b in books)
    start = sum(b.get("start_equity") or 0 for b in books) or 1
    notional = sum((p.get("notional") or 0) for b in books for p in (b.get("positions") or []))
    longs = sum(1 for b in books for p in (b.get("positions") or []) if (p.get("side") or "").lower() == "buy")
    shorts = sum(1 for b in books for p in (b.get("positions") or []) if (p.get("side") or "").lower() == "sell")
    per_symbol = {}
    for b in books:
        for p in b.get("positions") or []:
            per_symbol[p.get("symbol")] = per_symbol.get(p.get("symbol"), 0) + (p.get("notional") or 0)
    return {"equity": equity, "start_equity": start,
            "return_pct": 100 * (equity / start - 1) if start else 0,
            "gross_notional": notional,
            "gross_multiple": notional / equity if equity else 0,
            "longs": longs, "shorts": shorts, "per_symbol": per_symbol,
            "ts": int(time.time() * 1000)}


def trading_halted():
    st = snapshot()
    if st["return_pct"] <= -abs(DEFAULTS["daily_loss_limit_pct"]):
        return True, "fleet drawdown %.2f%% breached the %.1f%% limit" % (
            st["return_pct"], DEFAULTS["daily_loss_limit_pct"])
    return False, ""


def size_for(notional, confidence):
    """Flow confidence scales size: 0.5x when unconvinced, 1.5x when aligned."""
    c = max(-100, min(100, confidence or 0))
    return notional * (0.5 + (c + 100) / 200.0)


def _engine():
    """Which book is asking: the script name is the engine name everywhere else."""
    import os as _os, sys as _sys
    return _os.path.basename(_sys.argv[0] or "").replace(".py", "")


def allow(symbol, side, notional, book_equity, expected_move_pct=None, slippage_bps=None,
          confidence=None, bar_age_s=None, signal_age_s=None, book_positions=None):
    """Return (allowed, reason). Records the decision for the dashboard."""
    if notional <= 0:
        return False, "zero size"

    # owner: no new shorts. Entries still come from the indicators; only the direction
    # is restricted, and turning allow_shorts on restores them without a restart.
    try:
        import tactics as _T
        if str(side).lower() in ("sell", "short") and not _T.options().get("allow_shorts", False):
            return False, "no new shorts (owner: stop trading shorts)"
    except Exception:
        pass

    # earned capital: a promoted book may size up, a demoted one or a disabled one may
    # only size down, and a disabled book is refused outright (A6)
    try:
        import tactics as _T
        _mult, _slots = _T.capital(_engine())
        if _mult <= 0:
            return False, "no capital allocation (disabled book)"
        if _mult != 1.0:
            notional = notional * _mult
    except Exception:
        pass
    # STOP is global: every engine honours the flag even if it is still running
    import os as _os
    if _os.path.exists(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "state", "STOP")):
        return False, "trading stopped (control)"
    if bar_age_s is not None and bar_age_s > DEFAULTS["max_forecast_age_s"]:
        return False, "stale forecast: candles %.0fs old (max %ds)" % (
            bar_age_s, DEFAULTS["max_forecast_age_s"])
    if signal_age_s is not None and signal_age_s > DEFAULTS["max_signal_age_s"]:
        return False, "stale signals: %.0fs old (max %ds)" % (
            signal_age_s, DEFAULTS["max_signal_age_s"])
    try:
        import tactics as _T
        if _T.options().get("override_crazy"):
            return True, "OVERRIDE — every gate bypassed"
    except Exception:
        pass
    halted, why = trading_halted()
    if halted:
        return False, why
    st = snapshot()
    if st["gross_multiple"] + notional / max(book_equity, 1) > DEFAULTS["max_gross_multiple"]:
        return False, "gross exposure would exceed %.1fx" % DEFAULTS["max_gross_multiple"]
    if notional / max(book_equity, 1) > DEFAULTS["max_symbol_share"]:
        return False, "position above %.0f%% of the book" % (100 * DEFAULTS["max_symbol_share"])
    # direction cap is per book: one model's positions must not block another's.
    if book_positions is not None:
        same = sum(1 for pos in book_positions
                   if (pos.get("side") or "").lower() == side)
    else:
        same = st["longs"] if side == "buy" else st["shorts"]
    if same >= DEFAULTS["max_same_direction"]:
        return False, "%d positions already %s in this book" % (same, side)
    # The gate is about opposition, not enthusiasm. A neutral book (confidence
    # near zero) is not evidence against a trade; flow leaning the other way is.
    if confidence is not None:
        if side == "buy" and confidence <= -10:
            return False, "flow opposes the long (%.0f)" % confidence
        if side == "sell" and confidence >= 10:
            return False, "flow opposes the short (%.0f)" % confidence
    if expected_move_pct is not None:
        slip = (slippage_bps or 0) / 100.0
        cost = DEFAULTS["fee_pct"] + slip + DEFAULTS["cost_buffer_pct"]
        if expected_move_pct < cost:
            return False, "edge %.3f%% below cost %.3f%% (fee %.2f + slip %.3f)" % (
                expected_move_pct, cost, DEFAULTS["fee_pct"], slip)
    return True, "ok"


_LAST_STATE_PUB = 0


def record(symbol, side, allowed, reason):
    try:
        with open(PATH) as fh:
            d = json.load(fh)
    except Exception:
        d = {"decisions": []}
    d["decisions"].append({"symbol": symbol, "side": side, "allowed": allowed,
                           "reason": reason, "ts": int(time.time() * 1000)})
    d["decisions"] = d["decisions"][-200:]
    d["snapshot"] = snapshot()
    try:
        global _LAST_STATE_PUB
        now_ms = int(time.time() * 1000)
        if now_ms - _LAST_STATE_PUB > 2000:
            _LAST_STATE_PUB = now_ms
            from lib import bus as _bus
            _bus.pub("risk.state", "ALL", {"snapshot": d["snapshot"], "ts": now_ms}, "risk")
    except Exception:
        pass
    os.makedirs(STATE, exist_ok=True)
    # every process gets its own temp file: a shared name meant two engines
    # flushing at once would consume each other's file and crash on the rename
    tmp = "%s.%d.%d.t" % (PATH, os.getpid(), int(time.time() * 1000) % 100000)
    try:
        with open(tmp, "w") as fh:
            json.dump(d, fh)
        os.replace(tmp, PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2)[:800])
