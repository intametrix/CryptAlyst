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


TARGET_VOL_PCT = 0.60     # aim for roughly this ATR% per position


def vol_target(notional, atr_pct, lo=0.3, hi=2.2):
    """Scale size so risk is similar across symbols of different volatility."""
    try:
        a = float(atr_pct)
    except Exception:
        return notional
    if a <= 0:
        return notional
    scale = max(lo, min(hi, TARGET_VOL_PCT / a))
    return notional * scale


def size_for(notional, confidence, atr_pct=None):
    """Flow confidence scales size: 0.5x when unconvinced, 1.5x when aligned."""
    c = max(-100, min(100, confidence or 0))
    sized = notional * (0.5 + (c + 100) / 200.0)
    if atr_pct is not None:
        sized = vol_target(sized, atr_pct)
    return sized


def allow(symbol, side, notional, book_equity, expected_move_pct=None, slippage_bps=None,
          confidence=None, bar_age_s=None, signal_age_s=None):
    """Return (allowed, reason). Records the decision for the dashboard."""
    if notional <= 0:
        return False, "zero size"
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
    same = st["longs"] if side == "buy" else st["shorts"]
    if same >= DEFAULTS["max_same_direction"]:
        return False, "%d positions already %s" % (same, side)
    if confidence is not None and abs(confidence) < 10:
        return False, "money flow gives no confidence (%.0f)" % confidence
    if confidence is not None and side == "buy" and confidence < -30:
        return False, "flow opposes the long (%.0f)" % confidence
    if confidence is not None and side == "sell" and confidence > 30:
        return False, "flow opposes the short (%.0f)" % confidence
    if expected_move_pct is not None:
        slip = (slippage_bps or 0) / 100.0
        cost = DEFAULTS["fee_pct"] + slip + DEFAULTS["cost_buffer_pct"]
        if expected_move_pct < cost:
            return False, "edge %.3f%% below cost %.3f%% (fee %.2f + slip %.3f)" % (
                expected_move_pct, cost, DEFAULTS["fee_pct"], slip)
    return True, "ok"


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
    os.makedirs(STATE, exist_ok=True)
    with open(PATH + ".t", "w") as fh:
        json.dump(d, fh)
    os.replace(PATH + ".t", PATH)


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2)[:800])
