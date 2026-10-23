#!/usr/bin/env python3
"""streamfeed - all Binance market data over WebSocket (never poll).

Streams depth (order-book imbalance) and 1s klines (candles) for the given
symbols. Klines are seeded once (history) and then appended live from the
stream. Everything else is discarded on arrival — BPF-style filter at source.

    feed = Feed(["BTCUSDT", "ETHUSDT"])
    feed.snapshot("BTCUSDT")   # {'imb': -0.69, 'mid': 81795.0}
    feed.klines("BTCUSDT")     # [{'t','o','h','l','c','v'}, ...] (last 200)
    feed.best()                # (symbol, snapshot) with strongest |imb|
"""
import json, threading, time
import ratelimit
import websocket

SPOT = "https://api.binance.com"


def _seed_klines(sym, interval="1s", n=200):
    d = ratelimit.jget(SPOT + "/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (sym, interval, n),
                       8, {"User-Agent": "feed/1"})
    return [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
             "c": float(x[4]), "v": float(x[5])} for x in d]


class Feed:
    def __init__(self, symbols, kline_interval="1s", kline_n=200):
        self.symbols = [s.upper() for s in symbols]
        self.cache = {s: {"imb": 0.0, "mid": 0.0, "ts": 0.0} for s in self.symbols}
        self.k = {s: [] for s in self.symbols}
        # one-time seed (history), then live stream appends
        for s in self.symbols:
            try:
                self.k[s] = _seed_klines(s, kline_interval, kline_n)
            except Exception:
                self.k[s] = []
        streams = "/".join("%s@depth5@100ms/%s@kline_%s" % (s.lower(), s.lower(), kline_interval)
                           for s in self.symbols)
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
            stream = m.get("stream") or ""
            sym = stream.split("@")[0].upper()
            if sym not in self.cache:
                return
            if "@kline_" in stream:
                k = (m.get("data") or {}).get("k") or {}
                if k.get("x"):          # closed candle
                    candle = {"t": int(k["t"]), "o": float(k["o"]), "h": float(k["h"]),
                              "l": float(k["l"]), "c": float(k["c"]), "v": float(k["v"])}
                    self.k[sym].append(candle)
                    self.k[sym] = self.k[sym][-200:]
                return
            # depth
            data = m.get("data") or {}
            def side(arr):
                best = vol = 0.0
                for x in (arr or []):
                    if len(x) >= 2:
                        if not best:
                            best = float(x[0])
                        vol += float(x[1])
                return best, vol
            bb, bv = side(data.get("bids"))
            ba, av = side(data.get("asks"))
            imb = (bv - av) / (bv + av) if (bv + av) else 0.0
            self.cache[sym] = {"imb": imb, "mid": (bb + ba) / 2 if bb and ba else 0.0,
                               "ts": time.time()}
        except Exception:
            pass

    def snapshot(self, sym):
        c = self.cache.get(sym.upper())
        return c if (c and c["mid"] > 0) else None

    def klines(self, sym):
        return self.k.get(sym.upper()) or []

    def best(self):
        best, bestkey = None, 0.0
        for s, c in self.cache.items():
            if c["mid"] > 0 and abs(c["imb"]) > abs(bestkey):
                best, bestkey = s, c["imb"]
        return (best, self.cache[best]) if best else (None, None)
