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
    prev_oi = {}
    while True:
        t0 = time.time()
        try:
            uni = jget(BACKEND + "/api/universe800", 12).get("tickers") or []
        except Exception:
            uni = []
        syms = [u["s"] for u in uni if str(u.get("s", "")).endswith("USDT")][:a.symbols]
        out = {}
        for s in syms:
            b = safe(lambda s=s: book(s))
            if not b: continue
            cur = safe(lambda s=s: oi(s))
            chg = None
            if cur is not None and prev_oi.get(s):
                chg = 100 * (cur / prev_oi[s] - 1)
            if cur is not None: prev_oi[s] = cur
            print("  " + s, flush=True) if False else None
            out[s] = {"mid": b["mid"], "imbalance": round(b["imbalance"], 4),
                      "bid": round(b["bid"], 2), "ask": round(b["ask"], 2),
                      "oi": cur, "oi_chg_pct": None if chg is None else round(chg, 3),
                      "taker_ratio": safe(lambda s=s: taker(s))}
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "features.json.t"), "w") as fh:
            json.dump({"ts": int(time.time() * 1000), "features": out}, fh)
        os.replace(os.path.join(STATE, "features.json.t"), os.path.join(STATE, "features.json"))
        print("%s  %d symbols  imbalance %.3f  oi tracked %d" % (
            time.strftime("%H:%M:%S"), len(out),
            sum(v["imbalance"] for v in out.values()) / max(1, len(out)), len(prev_oi)), flush=True)
        time.sleep(max(5.0, a.every - (time.time() - t0)))

if __name__ == "__main__": main()
