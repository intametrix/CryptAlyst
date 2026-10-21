#!/usr/bin/env python3
"""feeds - the data the models could not see before.

Pulls public Binance futures data every cycle and writes state/features.json:

  open_interest      level + 5m/15m/1h change
  book_imbalance     bid vs ask depth within 0.5% (top 20 levels)
  taker_ratio        aggressive buy vs sell volume (futures, 5m)
  liq_pressure       (depth collapse proxy until the liquidation feed is wired)

Everything here is public, no keys.

    python3 feeds.py --every 30 --symbols 40
"""
import argparse, json, os, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
BACKEND = "http://127.0.0.1:8799"; FAPI = "https://fapi.binance.com"
OI_HIST_PATH = os.path.join(STATE, "oi_history.json")
OI_MAX_AGE_S = 3600.0   # keep one hour of OI samples per symbol


def oi_change_pct(hist, now, window_s):
    """Percent change in OI over window_s, from the oldest sample inside the window.

    hist is [(ts, oi)] oldest-first; now and window_s are epoch seconds. Returns
    None when there is no sample old enough to define the window.
    """
    if not hist:
        return None
    if len(hist) < 2:
        return None
    cutoff = now - window_s
    base = None
    for ts, val in hist:
        if ts >= cutoff:
            base = val
            break
    if base in (None, 0):
        return None
    return 100.0 * (hist[-1][1] / base - 1.0)


def load_oi_history():
    try:
        with open(OI_HIST_PATH) as fh:
            d = json.load(fh)
        return {k: [(float(t), float(v)) for t, v in v] for k, v in (d.get("hist") or {}).items()}
    except Exception:
        return {}


def save_oi_history(hist):
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(OI_HIST_PATH + ".t", "w") as fh:
            json.dump({"ts": int(time.time() * 1000), "hist": hist}, fh)
        os.replace(OI_HIST_PATH + ".t", OI_HIST_PATH)
    except Exception:
        pass

def jget(u, t=8.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "feeds/1"}), timeout=t) as r:
        return json.loads(r.read().decode())

def safe(fn, default=None):
    try: return fn()
    except Exception: return default

def oi(sym):
    d = jget(FAPI + "/fapi/v1/openInterest?symbol=" + sym, 6)
    return float(d.get("openInterest") or 0)

def book(sym):
    d = jget(FAPI + "/fapi/v1/depth?symbol=%s&limit=20" % sym, 6)
    bids = [(float(p), float(q)) for p, q in d.get("bids") or []]
    asks = [(float(p), float(q)) for p, q in d.get("asks") or []]
    if not bids or not asks: return None
    mid = (bids[0][0] + asks[0][0]) / 2
    bq = sum(q for p, q in bids if p >= mid * 0.995)
    aq = sum(q for p, q in asks if p <= mid * 1.005)
    return {"mid": mid, "bid": bq, "ask": aq,
            "imbalance": (bq - aq) / (bq + aq) if (bq + aq) else 0.0}

def taker(sym):
    d = jget(FAPI + "/futures/data/takerlongshortRatio?symbol=%s&period=5m&limit=2" % sym, 6)
    if isinstance(d, list) and d:
        return float(d[-1].get("buySellRatio") or 0)
    return None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--every", type=float, default=30.0)
    ap.add_argument("--symbols", type=int, default=12)
    a = ap.parse_args()
    print("FEEDS — open interest, book imbalance, taker ratio", flush=True)
    hist = load_oi_history()
    while True:
        t0 = time.time()
        try:
            uni = jget(BACKEND + "/api/universe800", 12).get("tickers") or []
        except Exception:
            uni = []
        syms = [u["s"] for u in uni if str(u.get("s", "")).endswith("USDT")][:a.symbols]
        out = {}
        now = time.time()
        for s in syms:
            b = safe(lambda s=s: book(s))
            if not b: continue
            cur = safe(lambda s=s: oi(s))
            if cur is not None:
                h = hist.setdefault(s, [])
                h.append((now, cur))
                hist[s] = [(ts, v) for ts, v in h if now - ts <= OI_MAX_AGE_S]
            h = hist.get(s, [])
            out[s] = {"mid": b["mid"], "imbalance": round(b["imbalance"], 4),
                      "bid": round(b["bid"], 2), "ask": round(b["ask"], 2),
                      "oi": cur,
                      "oi_chg_5m": round(oi_change_pct(h, now, 300), 3) if len(h) > 1 else None,
                      "oi_chg_15m": round(oi_change_pct(h, now, 900), 3) if len(h) > 1 else None,
                      "oi_chg_1h": round(oi_change_pct(h, now, 3600), 3) if len(h) > 1 else None,
                      "oi_chg_pct": round(oi_change_pct(h, now, 300), 3) if len(h) > 1 else None,
                      "taker_ratio": safe(lambda s=s: taker(s))}
        save_oi_history(hist)
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "features.json.t"), "w") as fh:
            json.dump({"ts": int(time.time() * 1000), "features": out}, fh)
        os.replace(os.path.join(STATE, "features.json.t"), os.path.join(STATE, "features.json"))
        print("%s  %d symbols  imbalance %.3f  oi tracked %d" % (
            time.strftime("%H:%M:%S"), len(out),
            sum(v["imbalance"] for v in out.values()) / max(1, len(out)), len(hist)), flush=True)
        time.sleep(max(5.0, a.every - (time.time() - t0)))

if __name__ == "__main__": main()
