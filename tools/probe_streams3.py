"""Throwaway probe #3: sequential WS tests with a longer listen window."""
import asyncio
import json

import websockets

CASES = [
    ("spot !miniTicker@arr", "wss://stream.binance.com:9443/ws/!miniTicker@arr"),
    ("spot !bookTicker", "wss://stream.binance.com:9443/ws/!bookTicker"),
    ("spot !ticker@arr", "wss://stream.binance.com:9443/ws/!ticker@arr"),
    ("futures !miniTicker@arr", "wss://fstream.binance.com/ws/!miniTicker@arr"),
    ("futures !bookTicker", "wss://fstream.binance.com/ws/!bookTicker"),
    ("futures !ticker@arr", "wss://fstream.binance.com/ws/!ticker@arr"),
    ("futures btcusdt@aggTrade", "wss://fstream.binance.com/ws/btcusdt@aggTrade"),
]


async def probe(name: str, url: str, listen: float = 20.0) -> None:
    got = 0
    note = ""
    try:
        async with websockets.connect(url, open_timeout=12) as ws:
            deadline = asyncio.get_event_loop().time() + listen
            while got < 3:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                got += 1
                if got == 1:
                    if isinstance(msg, list):
                        note = (
                            f"list len={len(msg)} "
                            f"keys={sorted(msg[0].keys()) if msg else []}"
                        )
                    else:
                        note = f"dict keys={sorted(msg.keys())}"
    except Exception as exc:  # noqa: BLE001
        note = f"{type(exc).__name__}: {exc}"
    if not note:
        note = "(connected, no message)"
    print(f"{name:<26} msgs={got:<3} {note}", flush=True)


async def main() -> None:
    for name, url in CASES:
        await probe(name, url)


if __name__ == "__main__":
    asyncio.run(main())
