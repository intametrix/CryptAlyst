#!/usr/bin/env python3
"""streamfeed - real-time Binance order-book imbalance via WebSocket (never poll).

BPF-inspired: filter at the source. Subscribe to the minimal depth5@100ms stream
(5 levels, not 20) and keep ONLY the single number that matters — the bid/ask
volume imbalance. Everything else is discarded the instant it arrives.

    feed = Feed(["BTCUSDT", "ETHUSDT"])   # background thread
    snap = feed.snapshot("BTCUSDT")        # {'imb': -0.69, 'mid': 81795.0, 'ts': ...}
"""
import json, threading, time
import websocket


class Feed:
    def __init__(self, symbols, levels=5):
        self.symbols = [s.upper() for s in symbols]
        self.cache = {s: {"imb": 0.0, "mid": 0.0, "ts": 0.0} for s in self.symbols}
        streams = "/".join("%s@depth%d@100ms" % (s.lower(), levels) for s in self.symbols)
        self.url = "wss://stream.binance.com:9443/stream?streams=" + streams
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        ws = websocket.WebSocketApp(self.url, on_message=self._on_msg,
                                    on_error=lambda w, e: None,
                                    on_close=lambda w, *a: None)
        ws.run_forever(ping_interval=30, ping_timeout=10)

    def _on_msg(self, ws, msg):
        try:
            m = json.loads(msg) or {}
            sym = (m.get("stream") or "").split("@")[0].upper()
            if sym not in self.cache:
                return
            data = m.get("data") or {}
            def side(b, a):
                best = vol = 0.0
                for x in (b or []):
                    if len(x) >= 2:
                        if not best:
                            best = float(x[0])
                        vol += float(x[1])
                return best, vol
            bb, bv = side(data.get("bids"), data.get("asks"))
            ba, av = side(data.get("asks"), data.get("bids"))
            imb = (bv - av) / (bv + av) if (bv + av) else 0.0
            self.cache[sym] = {"imb": imb, "mid": (bb + ba) / 2 if bb and ba else 0.0,
                               "ts": time.time()}
        except Exception:
            pass

    def snapshot(self, sym):
        c = self.cache.get(sym.upper())
        return c if (c and c["mid"] > 0) else None

    def best(self):
        best, bestkey = None, 0.0
        for s, c in self.cache.items():
            if c["mid"] > 0 and abs(c["imb"]) > abs(bestkey):
                best, bestkey = s, c["imb"]
        return (best, self.cache[best]) if best else (None, None)
