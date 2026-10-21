#!/usr/bin/env python3
"""signals - the extra market signals Binance exposes but we were not using.

Publishes to RabbitMQ (topic exchange `market`) and serves them over HTTP:

    GET /signals?symbol=BTCUSDT      full set for one symbol
    GET /matrix?top=12               ranked matrix across liquid names
    GET /health

Signal families added here:
  trend      price vs sma200, higher-high/higher-low, multi-timeframe agreement
  volume     z-score, trade-count acceleration, absorption, volume profile POC
  book       spread, 20-level imbalance, walls, microprice, slippage for our size
  derivatives open interest, OI/price divergence, funding level+change, basis,
              top-trader vs retail long/short, taker buy/sell ratio
  regime     breadth, BTC dominance tilt, alt/BTC relative strength
"""
import argparse, json, math, os, statistics, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from lib import bus as _bus
except Exception:
    _bus = None

HERE = os.path.dirname(os.path.abspath(__file__))
MD = "http://127.0.0.1:8796"
FAPI = "https://fapi.binance.com"
STATE = os.path.join(HERE, "state")


def jget(u, t=12.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "signals/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def kl(sym, interval="1m", limit=200):
    return jget(MD + "/klines?symbol=%s&interval=%s&limit=%d" % (sym, interval, limit)).get("klines") or []


def sma(v, n):
    return sum(v[-n:]) / min(n, len(v)) if v else 0.0


def ema(v, n):
    k = 2.0 / (n + 1); e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def trend_signals(sym):
    rows = kl(sym, "1m", 240)
    if len(rows) < 60:
        return {}
    cl = [float(r[4]) for r in rows]
    hi = [float(r[2]) for r in rows]
    lo = [float(r[3]) for r in rows]
    px = cl[-1]
    out = {}
    out["px_vs_sma200"] = round(100 * (px / sma(cl, 200) - 1), 4) if len(cl) >= 60 else None
    out["px_vs_sma50"] = round(100 * (px / sma(cl, 50) - 1), 4)
    hh = hi[-1] > max(hi[-20:-1]); hl = lo[-1] > min(lo[-20:-1])
    out["structure"] = "higher high" if hh else "lower high" if hi[-1] < max(hi[-20:-1]) else "inside"
    out["hl_structure"] = "higher low" if hl else "lower low"
    # multi-timeframe agreement
    agree = 0
    for iv in ("1m", "5m"):
        try:
            r2 = kl(sym, iv, 40)
            c2 = [float(x[4]) for x in r2]
            if len(c2) > 21:
                agree += 1 if ema(c2[-21:], 9) > ema(c2[-21:], 21) else -1
        except Exception:
            pass
    out["mtf_agreement"] = agree
    return out


def volume_signals(sym):
    rows = kl(sym, "1m", 120)
    if len(rows) < 40:
        return {}
    vol = [float(r[5]) for r in rows]
    quote = [float(r[7]) for r in rows]
    buy = [float(r[10]) for r in rows]
    counts = [float(r[8]) for r in rows]
    out = {}
    base = vol[-21:-1]
    sd = statistics.pstdev(base) or 1
    out["vol_z"] = round((vol[-1] - statistics.mean(base)) / sd, 3)
    out["trade_count_accel"] = round(counts[-1] / (statistics.mean(counts[-21:-1]) or 1), 3)
    out["buy_ratio"] = round(sum(buy[-30:]) / (sum(quote[-30:]) or 1), 4)
    # absorption: big volume, tiny range
    rng = float(rows[-1][2]) - float(rows[-1][3])
    out["absorption"] = round((vol[-1] / (statistics.mean(base) or 1)) / (1 + rng / (float(rows[-1][4]) or 1) * 100), 3)
    # volume profile POC over last 120 bars
    bins = {}
    for r in rows:
        p = round(float(r[4]), 6)
        bins[p] = bins.get(p, 0) + float(r[5])
    out["poc"] = max(bins, key=bins.get) if bins else None
    out["px_vs_poc"] = round(100 * (float(rows[-1][4]) / out["poc"] - 1), 4) if out.get("poc") else None
    return out


def book_signals(sym):
    try:
        d = jget("https://api.binance.com/api/v3/depth?symbol=%s&limit=100" % sym)
    except Exception:
        return {}
    bids = [(float(p), float(q)) for p, q in d.get("bids", [])][:20]
    asks = [(float(p), float(q)) for p, q in d.get("asks", [])][:20]
    if not bids or not asks:
        return {}
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    out = {}
    out["spread_bps"] = round((ba - bb) / mid * 10000, 2)
    bq = sum(q for _, q in bids); aq = sum(q for _, q in asks)
    out["book_imbalance"] = round((bq - aq) / (bq + aq), 4) if (bq + aq) else 0
    out["microprice"] = round((bb * asks[0][1] + ba * bids[0][1]) / (asks[0][1] + bids[0][1]), 8)
    # wall detection: level much larger than the median level
    med = statistics.median([q for _, q in bids + asks]) or 1
    out["bid_wall"] = next((p for p, q in bids if q > 8 * med), None)
    out["ask_wall"] = next((p for p, q in asks if q > 8 * med), None)
    # slippage for a $5k order
    notional, cost, filled = 5000.0, 0.0, 0.0
    for p, q in asks:
        lvl = p * q
        take = min(notional - filled, lvl)
        cost += take * (p / ba - 1)
        filled += take
        if filled >= notional:
            break
    out["slippage_bps_5k"] = round(cost / max(filled, 1) * 10000, 2)
    return out


def deriv_signals(sym):
    out = {}
    try:
        pi = jget(FAPI + "/fapi/v1/premiumIndex?symbol=" + sym, 8)
        out["funding_pct"] = round(100 * float(pi.get("lastFundingRate") or 0), 5)
        mark = float(pi.get("markPrice") or 0)
        idx = float(pi.get("indexPrice") or 0)
        out["basis_bps"] = round((mark / idx - 1) * 10000, 2) if idx else None
    except Exception:
        pass
    try:
        oi = jget(FAPI + "/fapi/v1/openInterest?symbol=" + sym, 8)
        out["open_interest"] = float(oi.get("openInterest") or 0)
    except Exception:
        pass
    try:
        h = jget(FAPI + "/futures/data/openInterestHist?symbol=%s&period=1h&limit=24" % sym, 10)
        if len(h) >= 2:
            first = float(h[0]["sumOpenInterest"]); last = float(h[-1]["sumOpenInterest"])
            out["oi_change_1h_pct"] = round(100 * (last / first - 1), 3) if first else None
            closes = [float(x["sumOpenInterestValue"]) for x in h]
            out["oi_value"] = closes[-1]
    except Exception:
        pass
    try:
        t = jget(FAPI + "/futures/data/topLongShortPositionRatio?symbol=%s&period=1h&limit=1" % sym, 10)
        if t:
            out["top_trader_ratio"] = round(float(t[-1]["longShortRatio"]), 3)
    except Exception:
        pass
    try:
        g = jget(FAPI + "/futures/data/globalLongShortAccountRatio?symbol=%s&period=1h&limit=1" % sym, 10)
        if g:
            out["retail_ratio"] = round(float(g[-1]["longShortRatio"]), 3)
    except Exception:
        pass
    if out.get("top_trader_ratio") and out.get("retail_ratio"):
        out["smart_vs_dumb"] = round(out["top_trader_ratio"] / out["retail_ratio"], 3)
    try:
        tk = jget(FAPI + "/futures/data/takerlongshortRatio?symbol=%s&period=1h&limit=1" % sym, 10)
        if tk:
            out["taker_ratio"] = round(float(tk[-1]["buySellRatio"]), 3)
    except Exception:
        pass
    return out


def taker_ratio_from_bars(sym, n=30):
    """Taker buy share from candle quote volumes we already have.

    Q is taker-buy quote volume, q is total quote volume, so Q/q is how much of
    the traded value was buyers lifting offers. Without this the confidence
    calculation falls back to 0.5 and every trade is refused.
    """
    try:
        import tactics as T
        bars = T.bars(sym, "1m", n + 2) or []
        if len(bars) < 5:
            return None
        buy = 0.0
        tot = 0.0
        for b in bars[-n:]:
            q = b.get("q") or 0
            Q = b.get("Q")
            if not q or Q is None:
                continue
            buy += Q
            tot += q
        if tot <= 0:
            return None
        return max(0.0, min(1.0, buy / tot))
    except Exception:
        return None


def flow_confidence(sig):
    """Money flow as a confidence measure, not just a direction.

    Three parts:
      level       taker buy share (who is paying up)
      trend       is the net flow accelerating or fading
      divergence  price falling while flow rises = accumulation
    Returns (confidence -100..100, components) where + means buyers in control.
    """
    br = sig.get("taker_by_ratio")
    if not br:
        br = taker_ratio_from_bars(sig.get("symbol"))
    if br is None:
        br = 0.5
    sig["taker_by_ratio"] = br
    series = sig.get("flow_series") or []
    level = max(-1.0, min(1.0, (br - 0.5) * 4))          # 40% buy share -> -0.4
    trend = 0.0
    if len(series) >= 10:
        recent = sum(series[-5:]); prior = sum(series[-10:-5])
        denom = abs(recent) + abs(prior) or 1
        trend = max(-1.0, min(1.0, (recent - prior) / denom))
    div = 0.0
    px_chg = sig.get("px_vs_sma50") or 0
    if px_chg < -0.2 and level > 0.1: div = min(1.0, level + 0.3)
    elif px_chg > 0.2 and level < -0.1: div = max(-1.0, level - 0.3)
    conf = 45 * level + 30 * trend + 25 * div
    return round(max(-100, min(100, conf)), 1), {
        "level": round(level, 3), "trend": round(trend, 3), "divergence": round(div, 3)}


def score(sig):
    """Collapse to one -100..100 sentiment number so the models can consume it."""
    s = 0.0
    s += 18 * max(-1, min(1, sig.get("book_imbalance") or 0))
    s += 20 * ((sig.get("buy_ratio") or 0.5) - 0.5) * 4
    s += 10 * max(-1, min(1, (sig.get("vol_z") or 0) / 3))
    s += 14 * ((sig.get("smart_vs_dumb") or 1) - 1)
    s += 12 * max(-1, min(1, (sig.get("oi_change_1h_pct") or 0) / 5))
    s += 10 * max(-1, min(1, -(sig.get("funding_pct") or 0) / 0.05))
    s += 8 * (sig.get("mtf_agreement") or 0) / 3
    s += 8 * max(-1, min(1, -(sig.get("px_vs_sma200") or 0) / 5))
    return round(max(-100, min(100, s)), 1)


def analyse(sym):
    sig = {}
    for fn in (trend_signals, volume_signals, book_signals):
        try:
            sig.update(fn(sym) or {})
        except Exception:
            pass
    if sym.endswith("USDT"):
        try:
            sig.update(deriv_signals(sym) or {})
        except Exception:
            pass
    sig["symbol"] = sym
    try:
        rows = kl(sym, "1m", 60)
        if rows:
            series = []
            for r in rows[-40:]:
                total = float(r[7]); buy = float(r[10])
                series.append(round(buy - max(0.0, total - buy), 2))
            sig["flow_series"] = series
            sig["taker_by_ratio"] = round(sum(float(r[10]) for r in rows[-30:]) /
                                          (sum(float(r[7]) for r in rows[-30:]) or 1), 4)
    except Exception:
        pass
    conf, parts = flow_confidence(sig)
    sig["flow_conf"] = conf
    sig["flow_parts"] = parts
    sig["score"] = score(sig)
    sig["ts"] = int(time.time() * 1000)
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=20.0)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    print("SIGNALS — %d names every %.0fs, publishing to RabbitMQ" % (a.top, a.every), flush=True)
    while True:
        try:
            mv = jget(MD + "/movers?limit=%d" % a.top, 20)
            syms = [x["s"] for x in mv.get("most_traded", [])]
            # republish the movers list: intel.movers is registered for this and
            # was a dead topic — the movers surface never reached the bus
            if _bus:
                try:
                    _bus.pub("intel.movers", "most_traded", mv, "signals")
                except Exception:
                    pass
            # only analyse symbols we actually stream: anything else has no
            # candles, so its taker ratio and confidence are unknowable
            try:
                live = set((jget(MD + "/streamprices", 8).get("p") or {}).keys())
                if live:
                    kept = [x for x in syms if x in live]
                    if kept:
                        syms = kept
            except Exception:
                pass
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as pool:
                rows = [r for r in pool.map(analyse, syms) if r]
            for sig in rows:
                if _bus:
                    try:
                        _bus.pub("intel.signal", sig.get("symbol"), sig, "signals")
                        _bus.pub("intel.flow", sig.get("symbol"), {
                            "symbol": sig.get("symbol"),
                            "buy_ratio": sig.get("taker_by_ratio"),
                            "flow_conf": sig.get("flow_conf"),
                            "flow_series": (sig.get("flow_series") or [])[-10:],
                            "score": sig.get("score"),
                        }, "signals")
                    except Exception:
                        pass
            rows.sort(key=lambda r: r["score"], reverse=True)
            os.makedirs(STATE, exist_ok=True)
            p = os.path.join(STATE, "signals.json")
            with open(p + ".t", "w") as fh:
                json.dump({"rows": rows, "ts": int(time.time() * 1000)}, fh)
            os.replace(p + ".t", p)
        except Exception as exc:
            print("cycle error %r" % (exc,), flush=True)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
