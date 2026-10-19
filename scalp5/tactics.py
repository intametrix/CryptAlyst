"""tactics - the shared playbook: confirm with TA, size by conviction, lock fast."""
import json, os, time, urllib.request

FEE = 0.0002        # maker: 0.02% per side, 0.04% round trip (taker is 0.001)
TAKER_FEE = 0.001
MARKET_DATA = "http://127.0.0.1:8796"
FORECAST_HORIZON = 3600      # seconds: the horizon every model now targets
TA_INTERVAL = "15m"          # bars used for confirmations
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
    d = jget(MARKET_DATA + "/klines?symbol=%s&interval=%s&limit=%d"
             % (sym, interval, limit)).get("klines") or []
    if d and isinstance(d[0], dict):
        # keep the quote-volume fields: Q is taker-buy volume, q is the total,
        # and Q/q is the only taker-aggression measure we get for free
        b = [{"t": int(r["t"]), "o": float(r["o"]), "h": float(r["h"]),
              "l": float(r["l"]), "c": float(r["c"]), "v": float(r["v"]),
              "q": float(r.get("q") or 0), "Q": float(r.get("Q") or 0)} for r in d]
    else:
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


_REG = {"ts": 0, "trend": "flat"}


def btc_regime():
    """BTC 4h trend - the drift every alt inherits. Cached 60s."""
    if time.time() - _REG["ts"] < 60:
        return _REG["trend"]
    try:
        bs = bars("BTCUSDT", "4h", 60)
        cl = [b["c"] for b in bs]
        e20 = ema(cl[-20:], 20)
        up = cl[-1] > e20 * 1.001
        down = cl[-1] < e20 * 0.999
        _REG["trend"] = "up" if up else "down" if down else "flat"
    except Exception:
        pass
    _REG["ts"] = time.time()
    return _REG["trend"]


# allow_counter_trend lives in state/options.json now (see options())


def min_confirmations(side):
    """Trend-following gate: do not fight the BTC drift.

    In an uptrend a short needs perfection that pullback signals can supply by
    accident, so the counter-trend side is refused outright. Set
    ALLOW_COUNTER_TREND = True to allow it with 4 confirmations.
    """
    t = btc_regime()
    if t == "up" and side == "sell":
        # a counter-trend short is allowed when the carry pays us and the book
        # is not screaming squeeze (price up with open interest rising)
        if not options().get("allow_counter_trend"):
            return 99
        try:
            x = ext_signals() or {}
            oi = x.get("oi_change_1h_pct")
            f = x.get("funding_pct")
            if f is not None and f < 0:
                return 99                      # we would pay to hold it
            if oi is not None and oi > 3.0:
                return 99                      # squeeze setup, not a short
        except Exception:
            pass
        return 4
    if t == "down" and side == "buy":
        return 4 if options().get("allow_counter_trend") else 99
    return 2          # trend-aligned entries stay easy


def short_ok(sym):
    """A short is only worth holding if the carry is not against us.

    Positive funding means longs pay shorts. Negative funding means we pay to
    hold the winning trade, which is the regime our shorts kept losing in.
    """
    try:
        x = ext_signals(sym) or {}
        f = x.get("funding_pct")
        if f is None:
            return True, "funding unknown"
        if f < 0:
            return False, "negative funding %.3f%% — shorts pay to hold" % (100 * f)
        return True, "funding %+.3f%%" % (100 * f)
    except Exception:
        return True, "funding unavailable"


# directive: a position is never closed at a loss. No time limit, no exceptions.
HOLD_LOSERS_FOREVER = True


def hold_loser(held_s, chg_pct):
    """True whenever an exit would book negative PnL. Always, by directive."""
    if HOLD_LOSERS_FOREVER:
        return chg_pct < 0
    return chg_pct < 0 and held_s < 24 * 3600


def no_progress(chg, held, limit_s=120.0, min_chg=0.02):
    """A trade that has gone nowhere after ~3x the median win hold is dead money.

    Measured on realised fills: winning trades exit at a median 25s, losers at
    157s. Time is the strongest loss predictor we have.
    """
    return held >= limit_s and chg < min_chg


def atr_pct(sym, interval=None, n=14):
    """Realised volatility as a percentage of price - drives position sizing."""
    try:
        bs = bars(sym, interval or TA_INTERVAL, 120)
        if len(bs) < n + 2:
            return None
        trs = []
        for i in range(len(bs) - n, len(bs)):
            tr = max(bs[i]["h"] - bs[i]["l"],
                     abs(bs[i]["h"] - bs[i - 1]["c"]),
                     abs(bs[i - 1]["c"] - bs[i]["l"]))
            trs.append(tr)
        atr = sum(trs) / len(trs)
        px = bs[-1]["c"]
        return round(100 * atr / px, 4) if px else None
    except Exception:
        return None


def ta_confirm(sym, side, interval=None):
    """0-4 independent confirmations in the direction of the trade."""
    try:
        bs = bars(sym, interval or TA_INTERVAL, 200)
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


FC_API = "http://127.0.0.1:8790"
OPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "options.json")
DEFAULT_OPTIONS = {
    "override_crazy": False,        # bypass every gate - dangerous, deliberate
    "allow_counter_trend": False,   # trade against the BTC 4h trend?
    "forecast_gate": True,          # consult the forecast supervisor at all?
    "forecast_min": 0.45,           # minimum blended probability to enter
    "max_positions": 8,             # per model
    "clock_s": 180,                 # max hold
    "lock_pct": 0.35,               # take profit
    "stop_pct": 1.00,               # max loss per trade
    "min_expected_move": 0.0,       # require forecast move >= this (3x fee = 0.60)
}
_OPT = {"ts": 0.0, "v": dict(DEFAULT_OPTIONS)}


def options(force=False):
    """Live options, reloaded from state/options.json at most every 2 seconds."""
    now = time.time()
    if not force and now - _OPT["ts"] < 2.0:
        return _OPT["v"]
    v = dict(DEFAULT_OPTIONS)
    try:
        with open(OPT_PATH) as fh:
            v.update(json.load(fh) or {})
    except Exception:
        pass
    if v.get("override_crazy"):
        v = dict(v)
        v.update({"allow_counter_trend": True, "forecast_gate": False,
                  "min_expected_move": 0.0, "max_positions": 30,
                  "clock_s": 300, "lock_pct": 0.50, "stop_pct": 1.50})
    _OPT["v"], _OPT["ts"] = v, now
    return v


def set_option(key, value):
    v = dict(options(True))
    v[key] = value
    os.makedirs(os.path.dirname(OPT_PATH), exist_ok=True)
    with open(OPT_PATH + ".t", "w") as fh:
        json.dump(v, fh)
    os.replace(OPT_PATH + ".t", OPT_PATH)
    options(True)
    return v
TA_BUS = "http://127.0.0.1:8792"
_ta_cur = {"v": 0}
TA_LIVE = {}


def ta_signals():
    """Pull the TA daemon's new signals. Returns the list; fills TA_LIVE."""
    try:
        d = jget(TA_BUS + "/latest?since=%d" % _ta_cur["v"], 2) if False else json.loads(
            __import__("urllib.request", fromlist=["request"]).urlopen(
                TA_BUS + "/latest?since=%d" % _ta_cur["v"], timeout=2).read())
    except Exception:
        return []
    _ta_cur["v"] = d.get("cursor") or _ta_cur["v"]
    out = d.get("signals") or []
    for sig in out:
        TA_LIVE.setdefault(sig["symbol"], []).append(sig)
        TA_LIVE[sig["symbol"]] = TA_LIVE[sig["symbol"]][-12:]
    return out


_EXT = {"ts": 0, "rows": {}}


_INTEL = None


def _intel():
    global _INTEL
    if _INTEL is None:
        try:
            from lib import intel as _i
            _i.Intel.instance().start()
            _INTEL = _i
        except Exception:
            _INTEL = False
    return _INTEL or None


def ext_signals(sym=None):
    """The extended signal set, read from the bus first, the file second."""
    now = time.time()
    if now - _EXT["ts"] > 2:
        lib = _intel()
        rows = None
        if lib is not None:
            try:
                rows = lib.all()
            except Exception:
                rows = None
        if rows:
            _EXT["rows"] = rows
            _EXT["source"] = "bus"
        else:
            try:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "state", "signals.json")
                with open(path) as fh:
                    d = json.load(fh)
                _EXT["rows"] = {r["symbol"]: r for r in d.get("rows") or []}
                _EXT["source"] = "file"
            except Exception:
                pass
        _EXT["ts"] = now
    if sym is None:
        return _EXT["rows"]
    return _EXT["rows"].get(sym)


