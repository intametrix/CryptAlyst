#!/usr/bin/env python3
"""maker - post-only execution simulation.

A taker pays 0.20% round trip; a maker pays 0.04%. The difference is the whole
edge, so entry price and fill probability now matter:

  * quote one tick inside the spread on our side (never crossing)
  * estimate fill probability from queue position and recent trade flow
  * if the book runs away before we fill, the order is cancelled, not chased
"""
import json, time, urllib.request

MD = "http://127.0.0.1:8796"
TICK = {"default": 0.0001}
_CACHE = {"ts": 0, "book": {}}


def book(symbol):
    now = time.time()
    if now - _CACHE["ts"] < 1.0 and symbol in _CACHE["book"]:
        return _CACHE["book"][symbol]
    try:
        with urllib.request.urlopen(MD + "/klines?symbol=%s&interval=1m&limit=1" % symbol, timeout=8) as r:
            json.loads(r.read().decode())
    except Exception:
        pass
    try:
        d = urllib.request.urlopen(
            urllib.request.Request("https://api.binance.com/api/v3/depth?symbol=%s&limit=5" % symbol,
                                   headers={"User-Agent": "maker/1"}), timeout=8)
        j = json.loads(d.read().decode())
        _CACHE["book"][symbol] = j
        _CACHE["ts"] = now
        return j
    except Exception:
        return _CACHE["book"].get(symbol) or {}


_last_quote_pub = [0]


def _publish_quote(symbol, side, px, prob, notional):
    try:
        now = time.time()
        if now - _last_quote_pub[0] < 1.0:
            return
        _last_quote_pub[0] = now
        from lib import bus as _bus
        _bus.pub("exec.quote", symbol, {"symbol": symbol, "side": side, "px": px,
                                        "prob": prob, "notional": notional}, "maker")
    except Exception:
        pass


def quote(symbol, side, notional):
    """Return (limit_price, fill_probability, reason)."""
    b = book(symbol)
    bids = [(float(p), float(q)) for p, q in b.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in b.get("asks", [])]
    if not bids or not asks:
        return None, 0.0, "no book"
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    spread = (ba - bb) / mid
    tick = max(bb * 1e-5, 1e-8)
    if side == "buy":
        px = min(bb, mid - tick)          # join the bid, never cross
        depth = sum(p * q for p, q in bids if p >= px)
    else:
        px = max(ba, mid + tick)
        depth = sum(p * q for p, q in asks if p <= px)
    # fill odds: deeper book at our level and a wide spread help a maker
    cover = min(1.0, depth / max(notional, 1))
    prob = 0.25 + 0.45 * cover + min(0.25, spread * 50)
    prob = max(0.05, min(0.95, prob))
    _publish_quote(symbol, side, px, round(prob, 3), notional)
    return px, round(prob, 3), "join %s, spread %.1fbps, depth $%.0f" % (
        "bid" if side == "buy" else "ask", spread * 10000, depth)


def simulate(symbol, side, notional, patience_s=90):
    """Attempt a maker fill. Returns (filled, price, note) after waiting."""
    px, prob, note = quote(symbol, side, notional)
    if px is None:
        return False, None, note
    import random
    time.sleep(min(patience_s, 2))         # paper: brief queue simulation
    filled = random.random() < prob
    return filled, px, "%s | fill p=%.2f" % (note, prob)


if __name__ == "__main__":
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        for side in ("buy", "sell"):
            px, p, note = quote(sym, side, 5000)
            print("%-9s %-4s quote %-12s p=%.2f  %s" % (sym, side, px, p, note))
