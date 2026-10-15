"""Throwaway probe #5: only SPOT, testing the streams needed for 10s onset detection.

Goal: find a feed that delivers per-symbol price AND 24h quote volume at ~1s
cadence for a whole universe (so 10-second volume flow = delta of quote volume).
"""
import asyncio
import json

import websockets

BASE = "wss://stream.binance.com:9443"

CASES = [
    ("single @miniTicker", f"{BASE}/ws/btcusdt@miniTicker"),
    ("single @ticker", f"{BASE}/ws/btcusdt@ticker"),
    ("single @bookTicker", f"{BASE}/ws/btcusdt@bookTicker"),
    ("single @aggTrade", f"{BASE}/ws/btcusdt@aggTrade"),
    (
        "combined 3x @miniTicker",
        f"{BASE}/stream?streams=btcusdt@miniTicker/ethusdt@miniTicker/solusdt@miniTicker",
    ),
    (
        "combined 3x @ticker",
        f"{BASE}/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker",
    ),
    (
        "combined 3x @aggTrade",
        f"{BASE}/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade/solusdt@aggTrade",
    ),
]

SYMS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
    "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT", "MATICUSDT", "LTCUSDT", "BCHUSDT",
    "UNIUSDT", "ATOMUSDT", "ETCUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "NEARUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT", "PEPEUSDT", "SHIBUSDT",
    "WIFUSDT", "BONKUSDT",
]
CASES.append((
    f"combined {len(SYMS)}x @miniTicker",
    f"{BASE}/stream?streams=" + "/".join(f"{s.lower()}@miniTicker" for s in SYMS),
))


async def probe(name, url, listen=15.0):
    seen = {}
    counts = {}
    note = ""
    first = ""
    try:
        async with websockets.connect(url, open_timeout=12) as ws:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + listen
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                data = msg.get("data", msg) if isinstance(msg, dict) else msg
                if not first:
                    first = json.dumps(data)[:220]
                if isinstance(data, dict) and "s" in data:
                    counts[data["s"]] = counts.get(data["s"], 0) + 1
                    seen[data["s"]] = sorted(data.keys())
    except Exception as exc:  # noqa: BLE001
        note = f"{type(exc).__name__}: {exc}"
    symbols = len(counts)
    total = sum(counts.values())
    print(f"{name:<32} syms={symbols:<4} msgs={total:<5} {note}")
    if first:
        print(f"    first: {first}")
    if seen:
        sample_sym = next(iter(seen))
        print(f"    keys[{sample_sym}]: {seen[sample_sym]}")


async def main():
    for name, url in CASES:
        await probe(name, url)


if __name__ == "__main__":
    asyncio.run(main())
