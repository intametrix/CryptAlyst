"""tactics - the shared playbook: confirm with TA, size by conviction, lock fast."""
import json, time, urllib.request

FEE = 0.001
_CACHE = {}
_TTL = 12.0


def jget(u, t=6.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "tactics/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def bars(sym, interval="1m", limit=60):
    key = (sym, interval)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    d = jget("https://api.binance.com/api/v3/klines?symbol=%s&interval=%s&limit=%d"
             % (sym, interval, limit))
    b = [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
          "c": float(r[4]), "v": float(r[5])} for r in d]
    _CACHE[key] = (time.time(), b)
    return b


def ema(v, n):
    k = 2.0 / (n + 1)
    e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def rsi(bs, n=14):
    g = l = 0.0
    for i in range(1, len(bs)):
        ch = bs[i]["c"] - bs[i - 1]["c"]
        g += max(ch, 0.0); l += max(-ch, 0.0)
    if l == 0:
        return 100.0
    return 100 - 100 / (1 + (g / n) / (l / n))


def atr(bs, n=14):
    tr = [max(bs[i]["h"] - bs[i]["l"], abs(bs[i]["h"] - bs[i-1]["c"]),
              abs(bs[i]["l"] - bs[i-1]["c"])) for i in range(1, len(bs))]
    return sum(tr[-n:]) / max(1, len(tr[-n:]))


def ta_confirm(sym, side, interval="1m"):
    """0-4 independent confirmations in the direction of the trade."""
    try:
        bs = bars(sym, interval)
    except Exception:
        return 0, "no data"
    if len(bs) < 30:
        return 0, "thin"
    cl = [b["c"] for b in bs]
    e9, e21 = ema(cl[-30:], 9), ema(cl[-30:], 21)
    r = rsi(bs[-30:])
    px = cl[-1]
    vwap = sum(b["c"] * b["v"] for b in bs[-30:]) / (sum(b["v"] for b in bs[-30:]) or 1)
    volmed = sorted(b["v"] for b in bs[-21:-1])[10] or 1e-9
    volx = bs[-1]["v"] / volmed
    c, why = 0, []
    if side == "buy":
        if e9 > e21: c += 1; why.append("ema up")
        if r < 68: c += 1; why.append("rsi %.0f" % r)
        if px > vwap: c += 1; why.append(">vwap")
    else:
        if e9 < e21: c += 1; why.append("ema dn")
        if r > 32: c += 1; why.append("rsi %.0f" % r)
        if px < vwap: c += 1; why.append("<vwap")
    if volx > 1.2: c += 1; why.append("vol %.1fx" % volx)
    return c, ", ".join(why)


def risk_for(base, conf):
    """Conviction sizing: more confirmations, more capital."""
    return round(base * (0.6 + 0.2 * min(conf, 4)), 3)


def exit_reason(chg, peak, lock, trail_arm, trail_gap, stop, held, clock,
                skim=None, skim_after=2.5, hard_stop=None, opposing=False):
    """Asymmetric policy: skim winners fast, give losers room.

    winners : skim once chg >= skim and held >= skim_after, or the full lock
    losers  : no stop until hard_stop; if an opposing signal appears, re-measure
              and close; otherwise run to the clock
    """
    if opposing and chg < 0:
        return "signal_flip"
    if chg >= lock:
        return "lock"
    if skim is not None and chg >= skim and held >= skim_after:
        return "skim"
    if peak >= trail_arm and chg <= peak - trail_gap:
        return "trail"
    if hard_stop is not None:
        if chg <= -hard_stop:
            return "hard_stop"
    elif chg <= -stop:
        return "stop"
    if held >= clock:
        return "clock"
    return None
    if chg >= lock: return "lock"
    if peak >= trail_arm and chg <= peak - trail_gap: return "trail"
    if chg <= -stop: return "stop"
    if held >= clock: return "clock"
    return None
