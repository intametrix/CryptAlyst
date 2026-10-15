"""Throwaway probe #4: measure REST endpoint weights and payload shapes."""
import json
import time
import urllib.parse
import urllib.request

SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"


def call(base, path, params=None):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "pumpdump-probe/1.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
    dt = time.time() - t0
    return json.loads(body), hdrs, len(body), dt


def show(label, fn):
    try:
        data, hdrs, size, dt = fn()
        n = len(data) if isinstance(data, list) else "dict"
        w = hdrs.get("x-mbx-used-weight-1m", "?")
        print(
            f"{label:<44} n={str(n):<6} bytes={size:>9,} {dt*1000:>7.0f}ms used_weight_1m={w}",
            flush=True,
        )
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"{label:<44} FAILED {type(exc).__name__}: {exc}", flush=True)
        return None


print("=== spot ===")
all24 = show("ticker/24hr (no param)", lambda: call(SPOT, "/api/v3/ticker/24hr"))
show("ticker/price (no param)", lambda: call(SPOT, "/api/v3/ticker/price"))
show("ticker/24hr symbols=3", lambda: call(
    SPOT, "/api/v3/ticker/24hr", {"symbols": json.dumps(["BTCUSDT", "ETHUSDT", "SOLUSDT"])}))
kl = show("klines 1m limit=60", lambda: call(
    SPOT, "/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 60}))
show("exchangeInfo", lambda: call(SPOT, "/api/v3/exchangeInfo"))

print("\n=== futures ===")
show("ticker/24hr (no param)", lambda: call(FAPI, "/fapi/v1/ticker/24hr"))
show("ticker/price (no param)", lambda: call(FAPI, "/fapi/v1/ticker/price"))
show("klines 1m limit=60", lambda: call(
    FAPI, "/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 60}))
show("openInterest", lambda: call(FAPI, "/fapi/v1/openInterest", {"symbol": "BTCUSDT"}))
show("premiumIndex", lambda: call(FAPI, "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}))

print("\n=== kline shape ===")
if kl:
    print("bars returned:", len(kl))
    print("bar[0]:", kl[0])
    print("bar[-1]:", kl[-1])
    print("n_fields:", len(kl[0]))

print("\n=== spot 24hr sample keys ===")
if all24:
    usdt = [t for t in all24 if t["symbol"].endswith("USDT")]
    print("total", len(all24), "usdt", len(usdt))
    print("keys:", sorted(all24[0].keys()))
    top = sorted(usdt, key=lambda t: float(t["quoteVolume"]), reverse=True)[:3]
    for t in top:
        print(" ", t["symbol"], t["lastPrice"], f"q={float(t['quoteVolume']):,.0f}", "n=", t["count"])
