"""Throwaway probe: confirm the shape of the all-market ticker streams."""
import asyncio
import json
import sys

import websockets

URLS = {
    "spot": "wss://stream.binance.com:9443/ws/!ticker@arr",
    "futures": "wss://fstream.binance.com/ws/!ticker@arr",
}


async def probe(name: str, url: str) -> None:
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
            msg = json.loads(raw)
            print(f"=== {name}: type={type(msg).__name__} len={len(msg)}")
            if isinstance(msg, list) and msg:
                sample = msg[0]
                print(f"    keys: {sorted(sample.keys())}")
                print(f"    sample: {json.dumps(sample)[:600]}")
                usdt = [t for t in msg if t.get("s", "").endswith("USDT")]
                print(f"    total={len(msg)} usdt_pairs={len(usdt)}")
                top = sorted(usdt, key=lambda t: float(t.get("q", 0)), reverse=True)[:3]
                for t in top:
                    print(
                        f"    {t['s']:<12} c={t['c']:<12} P={t['P']:<8} "
                        f"q={float(t['q']):,.0f} n={t['n']}"
                    )
    except Exception as exc:  # noqa: BLE001
        print(f"=== {name}: FAILED {type(exc).__name__}: {exc}")


async def main() -> int:
    await asyncio.gather(*(probe(n, u) for n, u in URLS.items()))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
