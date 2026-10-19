"""Throwaway probe #2: try several stream URLs and report what actually arrives."""
import asyncio
import json

import websockets

CASES = [
    ("spot btcusdt@trade", "wss://stream.binance.com:9443/ws/btcusdt@trade"),
    ("spot !ticker@arr", "wss://stream.binance.com:9443/ws/!ticker@arr"),
    ("spot !ticker@arr :443", "wss://stream.binance.com/ws/!ticker@arr"),
    ("spot combined !ticker@arr", "wss://stream.binance.com:9443/stream?streams=!ticker@arr"),
    ("vision !ticker@arr", "wss://data-stream.binance.vision/ws/!ticker@arr"),
    ("vision !miniTicker@arr", "wss://data-stream.binance.vision/ws/!miniTicker@arr"),
    ("futures btcusdt@aggTrade", "wss://fstream.binance.com/ws/btcusdt@aggTrade"),
    ("futures !ticker@arr", "wss://fstream.binance.com/ws/!ticker@arr"),
    ("futures !miniTicker@arr", "wss://fstream.binance.com/ws/!miniTicker@arr"),
]


async def probe(name: str, url: str, listen: float = 12.0) -> None:
    got = 0
    note = ""
    try:
        async with websockets.connect(
            url, open_timeout=12, ping_interval=None
        ) as ws:
            try:
                while got < 2:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=listen - (got * 1.0)
                    )
                    msg = json.loads(raw)
                    got += 1
                    if got == 1:
                        if isinstance(msg, list):
                            note = (
                                f"list len={len(msg)} "
                                f"keys={sorted(msg[0].keys()) if msg else []}"
                            )
                        elif isinstance(msg, dict):
                            note = (
                                f"dict keys={sorted(msg.keys())} "
                                f"data_keys={sorted(msg['data'][0].keys()) if isinstance(msg.get('data'), list) and msg['data'] else sorted(msg.get('data', {}).keys()) if isinstance(msg.get('data'), dict) else 'n/a'}"
                            )
            except asyncio.TimeoutError:
                note = note or "(connected, no message)"
    except Exception as exc:  # noqa: BLE001
        note = f"{type(exc).__name__}: {exc}"
    print(f"{name:<28} msgs={got:<3} {note}")


async def main() -> None:
    await asyncio.gather(*(probe(n, u) for n, u in CASES))


if __name__ == "__main__":
    asyncio.run(main())
