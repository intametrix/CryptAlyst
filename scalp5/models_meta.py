#!/usr/bin/env python3
"""models_meta - one registry describing every model: what it does, the maths, its knobs.

Kept as data so the dashboard can drill into any engine without a second source of
truth. Each entry is deliberately short: a description a trader can read in ten
seconds, the formulas the code actually implements, and the parameters in force.
"""

BASE = {
    "alpha_rank": {
        "name": "alpha_rank",
        "family": "cross-sectional",
        "summary": "Ranks every symbol against every other symbol and trades the extremes. "
                   "Market direction is removed from the question: it asks which names are "
                   "strongest and weakest relative to the rest, not whether the market rises.",
        "math": [
            "z_ik = (x_ik - mean_k) / stdev_k          cross-sectional standardisation",
            "c_i  = sum_k(w_k * z_ik) / sum_k(w_k)     composite score in -1..+1",
            "long  = top decile (c_i >= +min_score)",
            "short = bottom decile (c_i <= -0.60) and funding >= 0",
            "w    = (X'X + lambda I)^-1 X'y           ridge fit once 25 fills exist",
        ],
        "params": {"min_score": 0.35, "lock": 0.35, "stop": 0.7, "clock": 900, "risk_pct": 10},
    },
    "alpha_signals": {
        "name": "alpha_signals",
        "family": "signal ensemble",
        "summary": "Ten independent models over the 20-field signal set. Each votes on direction; "
                   "the book only trades when its own model fires and the risk governor agrees.",
        "math": [
            "book_imb       long if imbalance > +0.35",
            "taker_flow     long if buy_ratio > 0.58, short if < 0.42",
            "smart_dumb     long if smart_vs_dumb > 1.4, short if < 0.7",
            "oi_divergence  price up + OI down => short signal",
            "funding_extreme long if funding < -0.02%, short if > +0.05%",
            "vol_z          enter when vol_z > 2.5 in the direction of the move",
            "mtf            requires 1m, 5m and 15m trends to agree",
        ],
        "params": {"every_s": 2, "risk_pct": 10},
    },
    "models10": {
        "name": "models10",
        "family": "classical strategies",
        "summary": "Ten standard technical strategies, each on its own book so their results are "
                   "comparable. No adaptation: these are the textbook rules, traded as written.",
        "math": [
            "donchian55   long if close > max(high[-55:-1]); exit min(low[-10:-1])",
            "orb          |close - open_1h| > 0.5 * ATR(14)",
            "boll_squeeze band width < 20th percentile, then break of the band",
            "rsi2         RSI(2) < 10 long, > 90 short",
            "vwap_revert  |price - vwap| > 1.5 sigma of the session",
            "macd         MACD(12,26,9) crosses its signal line",
            "vol_break    close beyond 20-bar range with volume > 1.5x median",
            "ema_pullback EMA50 > EMA200 and price touches EMA20",
            "atr_channel  close beyond a 20-period channel of 1.0 ATR",
            "funding_fade fade funding beyond +/-0.05%",
        ],
        "params": {"loop_s": 2, "probation_s": 300, "risk_pct": 10},
    },
    "crypto_pack": {
        "name": "crypto_pack",
        "family": "crypto playbooks",
        "summary": "Four crypto-specific playbooks sharing one risk framework: momentum, VWAP scalp, "
                   "open-interest squeeze and funding-plus-TA.",
        "math": [
            "ta_momentum  TA confirm + MA/RSI alignment; lock 0.35%, stop 0.70%",
            "vwap_scalp   |price - vwap| stretch; lock 0.25%, stop 0.50%",
            "oi_squeeze   OI rising into a range break; lock 0.40%, trail 0.60%",
            "funding_ta   funding extreme + TA agreement; lock 0.30%, stop 0.60%",
        ],
        "params": {"loop_s": 2, "min_liq": 100e6, "risk_pct": 10},
    },
    "hft": {
        "name": "hft",
        "family": "market microstructure",
        "summary": "Trades the order book, not candles. Microprice momentum plus queue imbalance, "
                   "entered maker only and exited within seconds.",
        "math": [
            "micro     = (bid*ask_qty + ask*bid_qty) / (ask_qty + bid_qty)",
            "move      = micro(t) / micro(t-8s) - 1",
            "sigma     = stdev of 1s returns",
            "z         = move / (sigma * sqrt(n))",
            "imb       = (bid_qty - ask_qty) / (bid_qty + ask_qty)",
            "enter     z > 2.2 and imb > 0.20 and spread < 4bp",
            "exit      target 0.18%, stop 0.25%, clock 120s",
        ],
        "params": {"symbols": 40, "cost_multiple": 2.0, "risk_pct": 10},
    },
    "movers": {
        "name": "movers",
        "family": "momentum ignition",
        "summary": "Trades the 24-hour movers in the direction of the move, then checks the position "
                   "three seconds later and closes it if it is profitable.",
        "math": [
            "select    top 12 gainers + top 12 losers by 24h change, quote volume > $2M",
            "confirm   10s momentum must agree with the 24h direction",
            "entry     maker at the touch, spread < 12bp",
            "check     at t+3s: if net > target then close, else keep checking",
            "safety    stop 1.0%, clock 600s",
        ],
        "params": {"check_at_s": 3, "target_pct": 0.05, "short_weight": 1.5, "risk_pct": 10},
    },
    "grid": {
        "name": "grid",
        "family": "market making",
        "summary": "Harvests oscillation instead of predicting direction: levels above and below an "
                   "anchor, each round trip earning the spacing minus fees.",
        "math": [
            "spacing = clamp(median 1m bar range * 1.0, 0.10%, 0.40%)",
            "levels  = 8 each side of the anchor mid",
            "fill    = ask <= level (buy) or bid >= level (sell)",
            "exit    = neighbouring level on the opposite side",
            "net     = spacing - fee*2 - buffer, skipped if <= 0",
        ],
        "params": {"max_symbols": 15, "levels": 8, "reanchor_s": 300, "risk_pct": 10},
    },
    "executor": {
        "name": "executor",
        "family": "alert execution",
        "summary": "Trades exactly what the advisor flags, but only when TA agrees. Locks profit fast "
                   "and trails the remainder.",
        "math": [
            "entry   advisor alert score >= min_alert",
            "confirm TA must agree with the alert direction",
            "lock    0.50% profit taken",
            "trail   arm 0.60%, gap 0.30%",
            "clock   900s",
        ],
        "params": {"min_alert": 60, "lock": 0.5, "clock": 900, "risk_pct": 10},
    },
    "skim_pump": {
        "name": "skim_pump",
        "family": "event driven",
        "summary": "Skims pump and dump flow the second it appears, holding for seconds rather than "
                   "minutes.",
        "math": [
            "entry   alert score >= min_score",
            "target  0.15% take profit",
            "stop    0.30%",
            "hold    8s",
        ],
        "params": {"hold_s": 8, "tp_pct": 0.15, "sl_pct": 0.30, "risk_pct": 10},
    },
    "curve_rider": {
        "name": "curve_rider",
        "family": "short-horizon trend",
        "summary": "Reads BTC's very short-term slope, takes the strongest liquid mover in that "
                   "direction, rides it briefly and waits.",
        "math": [
            "slope   = EMA9(15s) - EMA21(15s) on BTC",
            "pick    strongest liquid mover moving with the slope",
            "hold    fixed seconds, then close",
        ],
        "params": {"hold_s": 8, "wait_s": 60, "risk_pct": 10},
    },
    "market_agent": {
        "name": "market_agent",
        "family": "observer",
        "summary": "Does not trade. Watches the liquid universe and publishes the opportunity board "
                   "the other models read.",
        "math": ["score = w1*change + w2*volume + w3*range z-scores"],
        "params": {"every_s": 5, "top": 40},
    },
}


def for_engine(key, book=None, profile=None):
    """Return the registry entry, filled in with whatever the book reports."""
    meta = dict(BASE.get(key) or {
        "name": key, "family": "book", "summary": "No description registered for this engine yet.",
        "math": [], "params": {},
    })
    meta["key"] = key
    if book:
        meta["live"] = {
            "equity": book.get("equity"),
            "return_pct": book.get("return_pct"),
            "closed": book.get("closed"),
            "trades": book.get("trade_count") or len(book.get("trades") or []),
            "open": len(book.get("positions") or []),
        }
    if profile:
        meta["params"] = dict(meta.get("params") or {}, **profile)
    return meta
