#!/usr/bin/env python3
"""market_data - one shared, rate-limited cache in front of the exchange.

Every engine used to fetch klines itself: ten models scanning ten symbols every
two seconds is ~1000 requests a minute, which earned an HTTP 418 ban. This
service is now the only thing that talks upstream.

    GET /klines?symbol=BTCUSDT&interval=1m&limit=120
    GET /price?symbol=BTCUSDT
    GET /health
"""
import json, os, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SPOT = "https://api.binance.com"
ENV_PATH = os.path.expanduser("~/.cryptalyst/binance.env")


def load_key():
    """Read BINANCE_API_KEY from a 0600 file outside the repo. Never logged."""
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    if key:
        return key
    try:
        st = os.stat(ENV_PATH)
        if st.st_mode & 0o077:
            print("warning: %s is not 0600 — refusing to load it" % ENV_PATH, flush=True)
            return ""
        for line in open(ENV_PATH):
            line = line.strip()
            if line.startswith("BINANCE_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


API_KEY = load_key()
KL_TTL = float(os.environ.get("KL_TTL", "20"))     # per symbol+interval
MAX_RPS = float(os.environ.get("MAX_RPS", "4"))    # hard ceiling upstream
HERE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = {}
LOCK = threading.Lock()
BUCKET = {"t": 0.0, "n": 0}
BANNED_UNTIL = {"t": 0.0}
STATS = {"served": 0, "upstream": 0, "errors": 0, "banned": 0}


def throttle():
    """Token bucket: never exceed MAX_RPS upstream, no matter who asks."""
    while True:
        with LOCK:
            now = time.time()
            if now < BANNED_UNTIL["t"]:
                return False
            if now - BUCKET["t"] >= 1.0:
                BUCKET["t"], BUCKET["n"] = now, 0
            if BUCKET["n"] < MAX_RPS:
                BUCKET["n"] += 1
                STATS["upstream"] += 1
                return True
        time.sleep(0.05)


def load_secret():
    sec = os.environ.get("BINANCE_API_SECRET", "").strip()
    if sec:
        return sec
    try:
        for line in open(ENV_PATH):
            line = line.strip()
            if line.startswith("BINANCE_API_SECRET="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


API_SECRET = load_secret()


ORDER_ENDPOINTS_BLOCKED = True   # this system is paper-only; it never sends orders


def signed(path, params=None):
    """HMAC-SHA256 signed request, as Binance requires for account endpoints."""
    import hashlib, hmac, urllib.parse
    if not (API_KEY and API_SECRET):
        raise RuntimeError("no api credentials loaded")
    if ORDER_ENDPOINTS_BLOCKED and any(k in path for k in
                                       ("/order", "/order/", "/oco", "/sor")):
        raise RuntimeError("order endpoints are blocked: paper trading only")
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = urllib.parse.urlencode(params)
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = "https://api.binance.com" + path + "?" + query + "&signature=" + sig
    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": API_KEY,
                                               "User-Agent": "market-data/1"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode())


def upstream(path):
    if not throttle():
        raise RuntimeError("rate limited locally or exchange ban active")
    headers = {"User-Agent": "market-data/1"}
    if API_KEY:
        headers["X-MBX-APIKEY"] = API_KEY          # authenticated calls get the higher limit
    req = urllib.request.Request("https://api.binance.com" + path, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


FLOW = {"ts": 0.0, "data": {}}


def flow(symbol, bars=60):
    """Money flow from taker-side volume: who is actually paying up.

    A 1m kline carries both sides of the tape:
      [7] quote volume, [10] taker BUY quote volume
    so sell quote = total - buy, and net flow = buy - sell.
    """
    k = get_klines(symbol, "1m", bars)
    if not k:
        return None
    buy_q = sell_q = 0.0
    series = []
    for row in k:
        try:
            total = float(row[7])
            buy = float(row[10])
        except Exception:
            continue
        sell = max(0.0, total - buy)
        buy_q += buy
        sell_q += sell
        series.append(round(buy - sell, 2))
    if total <= 0:
        pass
    net = buy_q - sell_q
    tot = buy_q + sell_q
    ratio = (buy_q / tot) if tot else 0.5
    sentiment = ("strong buying" if ratio > 0.60 else
                 "buying" if ratio > 0.53 else
                 "strong selling" if ratio < 0.40 else
                 "selling" if ratio < 0.47 else "balanced")
    return {"symbol": symbol, "net_quote": round(net, 2), "buy_quote": round(buy_q, 2),
            "sell_quote": round(sell_q, 2), "buy_ratio": round(ratio, 4),
            "sentiment": sentiment, "bars": len(series), "series": series[-40:]}


def flow_table(top=20):
    """Net flow across the most liquid names - a sentiment map."""
    now = time.time()
    if FLOW["data"] and now - FLOW["ts"] < 20:
        return FLOW["data"]
    movers_data = movers(30, 3_000_000)
    rows = []
    for x in movers_data.get("most_traded", [])[:top]:
        f = flow(x["s"], 30)
        if not f:
            continue
        f["pct"] = x["pct"]
        f["qv"] = x["qv"]
        rows.append(f)
    rows.sort(key=lambda r: r["net_quote"], reverse=True)
    out = {"rows": rows, "ts": int(now * 1000),
           "buying": sum(1 for r in rows if r["buy_ratio"] > 0.53),
           "selling": sum(1 for r in rows if r["buy_ratio"] < 0.47)}
    FLOW["data"], FLOW["ts"] = out, now
    return out


KCACHE = os.path.join(HERE_DIR, "state", "kcache")
MOVERS = {"ts": 0.0, "data": {}}


def movers(limit=12, min_quote=3_000_000):
    """Most traded, biggest gainers, biggest losers - one upstream call, cached."""
    now = time.time()
    if MOVERS["data"] and now - MOVERS["ts"] < 60:
        return MOVERS["data"]
    try:
        rows = upstream("/api/v3/ticker/24hr")
    except Exception:
        if MOVERS["data"]:
            return MOVERS["data"]        # stale beats an error page
        raise
    usdt = []
    for x in rows:
        sym = x.get("symbol") or ""
        if not sym.endswith("USDT"):
            continue
        try:
            qv = float(x.get("quoteVolume") or 0)
            pct = float(x.get("priceChangePercent") or 0)
            last = float(x.get("lastPrice") or 0)
            trades = int(x.get("count") or 0)
        except Exception:
            continue
        if qv < min_quote or last <= 0:
            continue
        usdt.append({"s": sym, "p": last, "pct": pct, "qv": qv, "n": trades})
    by_vol = sorted(usdt, key=lambda x: x["qv"], reverse=True)[:limit]
    gainers = sorted(usdt, key=lambda x: x["pct"], reverse=True)[:limit]
    losers = sorted(usdt, key=lambda x: x["pct"])[:limit]
    out = {"most_traded": by_vol, "gainers": gainers, "losers": losers,
           "universe": len(usdt), "ts": int(now * 1000)}
    MOVERS["data"], MOVERS["ts"] = out, now
    return out


WARM = {"symbols": [], "ts": 0.0}


def warmer(top=20, every=20.0):
    """The ONLY thing that calls the exchange for candles.

    Engines read the cache; this loop refreshes it slowly enough to stay inside
    Binance's limits. 20 symbols x 2 intervals every 20s = 120 req/min.
    """
    while True:
        try:
            if time.time() - WARM["ts"] > 300 or not WARM["symbols"]:
                rows = upstream("/api/v3/ticker/24hr")
                usdt = [x for x in rows if (x.get("symbol") or "").endswith("USDT")]
                usdt.sort(key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)
                WARM["symbols"] = [x["symbol"] for x in usdt[:top]]
                WARM["ts"] = time.time()
                MOVERS["data"] = {
                    "most_traded": [{"s": x["symbol"], "p": float(x["lastPrice"]),
                                     "pct": float(x["priceChangePercent"]),
                                     "qv": float(x["quoteVolume"]), "n": int(x.get("count") or 0)}
                                    for x in usdt[:12]],
                    "gainers": [{"s": x["symbol"], "p": float(x["lastPrice"]),
                                 "pct": float(x["priceChangePercent"]),
                                 "qv": float(x["quoteVolume"]), "n": int(x.get("count") or 0)}
                                for x in sorted(usdt, key=lambda y: float(y["priceChangePercent"]), reverse=True)[:12]],
                    "losers": [{"s": x["symbol"], "p": float(x["lastPrice"]),
                                "pct": float(x["priceChangePercent"]),
                                "qv": float(x["quoteVolume"]), "n": int(x.get("count") or 0)}
                               for x in sorted(usdt, key=lambda y: float(y["priceChangePercent"]))[:12]],
                    "universe": len(usdt), "ts": int(time.time() * 1000)}
                MOVERS["ts"] = time.time()
            for sym in WARM["symbols"]:
                for iv in ("1m", "15m"):
                    try:
                        get_klines(sym, iv, 200)
                    except Exception:
                        pass
                time.sleep(0.2)
        except Exception:
            pass
        time.sleep(every)


def get_klines(symbol, interval, limit):
    key = (symbol, interval, limit)
    now = time.time()
    hit = CACHE.get(key)
    if hit and now - hit[0] < KL_TTL:
        STATS["served"] += 1
        return hit[1]
    try:
        d = upstream("/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (symbol, interval, limit))
        CACHE[key] = (now, d)
        try:
            os.makedirs(KCACHE, exist_ok=True)
            with open(os.path.join(KCACHE, "%s_%s.json" % (symbol, interval)), "w") as fh:
                json.dump(d, fh)
        except Exception:
            pass
        return d
    except Exception as exc:
        try:
            with open(os.path.join(KCACHE, "%s_%s.json" % (symbol, interval))) as fh:
                return json.load(fh)
        except Exception:
            pass
        if "418" in str(exc):
            BANNED_UNTIL["t"] = time.time() + 60
            STATS["banned"] += 1
        STATS["errors"] += 1
        if hit:
            return hit[1]          # stale beats nothing
        raise


def publish_loop(every=0.1):
    """Batch the stream into the event bus: one event per 100ms, not per tick."""
    import urllib.request as _u
    while True:
        time.sleep(every)
        try:
            prices, ts, n = MARKET_STREAM.snapshot()
            if not prices:
                continue
            body = json.dumps({"p": prices, "ts": ts, "msgs": n}).encode()
            c = rmq_client()
            if c is not None:
                c.publish("ticks", {"p": prices, "ts": ts, "msgs": n})
            else:
                req = _u.Request(EVENTBUS + "/publish?topic=ticks", data=body,
                                 method="POST", headers={"Content-Type": "application/json"})
                _u.urlopen(req, timeout=3).read()
        except Exception:
            pass


def refresh_subs(top=60):
    """Pick the symbols worth streaming: the deepest books."""
    while True:
        try:
            req = urllib.request.Request("https://api.binance.com/api/v3/ticker/24hr",
                                         headers={"User-Agent": "market-data/1"})
            with urllib.request.urlopen(req, timeout=15) as r:
                rows = json.loads(r.read().decode())
            usdt = [x for x in rows if x["symbol"].endswith("USDT")]
            usdt.sort(key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)
            syms = [x["symbol"] for x in usdt[:top]]
            if syms:
                MARKET_STREAM.subscribe(syms)
        except Exception:
            pass
        time.sleep(600)


class MarketStream:
    """Live trade/bookTicker stream - prices pushed, not polled."""

    def __init__(self):
        self.prices = {}
        self.lock = threading.Lock()
        self.status = "idle"
        self.msgs = 0
        self.subs = []
        self.last_ms = 0
        self.reconnects = 0
        self.listeners = []

    def subscribe(self, symbols):
        with self.lock:
            self.subs = [s.lower() for s in symbols]

    def url(self):
        streams = []
        for s in self.subs:
            streams.append(s + "@bookTicker")
        return "wss://stream.binance.com:9443/stream?streams=" + "/".join(streams)

    def run(self):
        while True:
            try:
                if not self.subs:
                    time.sleep(2); continue
                sock = UserStream.connect(self.url())
                self.status = "streaming %d symbols" % len(self.subs)
                while True:
                    op, payload = UserStream.read_frame(sock)
                    if op == 0x9:
                        UserStream.write_frame(sock, 0xA, payload); continue
                    if op == 0x8:
                        break
                    if op not in (0x1, 0x2):
                        continue
                    try:
                        env = json.loads(payload.decode("utf-8", "replace"))
                        d = env.get("data") or {}
                        sym = d.get("s")
                        if not sym:
                            continue
                        px = float(d.get("b") or d.get("a") or 0)
                        if px <= 0:
                            continue
                        with self.lock:
                            self.prices[sym] = px
                            self.msgs += 1
                            self.last_ms = int(time.time() * 1000)
                        for q in self.listeners:
                            q.put_nowait({"s": sym, "p": px, "t": self.last_ms})
                    except Exception:
                        continue
            except Exception as exc:
                self.status = "reconnecting: %s" % str(exc)[:60]
                self.reconnects += 1
                time.sleep(5)

    def snapshot(self):
        with self.lock:
            return dict(self.prices), self.last_ms, self.msgs


MARKET_STREAM = MarketStream()
EVENTBUS = "http://127.0.0.1:8797"
RMQ = {"client": None}


def rmq_client():
    """One long-lived STOMP connection to RabbitMQ, created lazily."""
    if RMQ["client"] is not None:
        return RMQ["client"]
    try:
        from rmq import Stomp
        c = Stomp()
        c.connect()
        RMQ["client"] = c
    except Exception:
        RMQ["client"] = None
    return RMQ["client"]


class UserStream:
    """Live account stream: fills and balance changes as Binance pushes them."""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()
        self.status = "idle"
        self.listen_key = None
        self.reconnects = 0

    # ---- listenKey lifecycle
    def new_key(self):
        req = urllib.request.Request(
            "https://api.binance.com/api/v3/userDataStream",
            data=b"", method="POST",
            headers={"X-MBX-APIKEY": API_KEY, "User-Agent": "market-data/1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.listen_key = json.loads(r.read().decode())["listenKey"]
        self.status = "keyed"
        return self.listen_key

    def keepalive(self):
        if not self.listen_key:
            return
        try:
            req = urllib.request.Request(
                "https://api.binance.com/api/v3/userDataStream?listenKey=" + self.listen_key,
                data=b"", method="PUT",
                headers={"X-MBX-APIKEY": API_KEY, "User-Agent": "market-data/1"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass

    # ---- websocket
    @staticmethod
    def connect(url):
        import base64, os as _os, socket
        host = url.split("//")[1].split("/")[0]
        path = "/" + url.split("//")[1].split("/", 1)[1]
        sock = socket.create_connection((host.split(":")[0], 443), timeout=15)
        import ssl
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(sock, server_hostname=host.split(":")[0])
        key = base64.b64encode(_os.urandom(16)).decode()
        sock.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(4096)
        return sock

    @staticmethod
    def read_frame(sock):
        import struct
        def rd(n):
            b = b""
            while len(b) < n:
                c = sock.recv(n - len(b))
                if not c:
                    raise EOFError
                b += c
            return b
        h = rd(2)
        op = h[0] & 0x0F
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", rd(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", rd(8))[0]
        return op, rd(ln)

    @staticmethod
    def write_frame(sock, op, data):
        import os as _os, struct
        mask = _os.urandom(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        header = bytearray([0x80 | op])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126); header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127); header += struct.pack(">Q", n)
        sock.sendall(bytes(header) + mask + payload)

    def run(self):
        while True:
            try:
                if not self.listen_key:
                    self.new_key()
                sock = self.connect("wss://stream.binance.com:9443/ws/" + self.listen_key)
                self.status = "streaming"
                last_keep = time.time()
                while True:
                    op, payload = self.read_frame(sock)
                    if op == 0x9:                      # ping
                        self.write_frame(sock, 0xA, payload)
                        continue
                    if op == 0x8:                      # close
                        break
                    if op in (0x1, 0x2):
                        try:
                            ev = json.loads(payload.decode("utf-8", "replace"))
                        except Exception:
                            continue
                        ev["_recv_ms"] = int(time.time() * 1000)
                        with self.lock:
                            self.events.append(ev)
                            if len(self.events) > 500:
                                del self.events[:-500]
                    if time.time() - last_keep > 1500:
                        self.keepalive()
                        last_keep = time.time()
            except Exception as exc:
                self.status = "reconnecting: %s" % str(exc)[:60]
                self.reconnects += 1
                time.sleep(5)

    def run_polling(self, every=5.0):
        """Binance has retired POST /api/v3/userDataStream (410 Gone), so balance
        changes are tracked by signed polling instead of a pushed stream."""
        prev = {}
        while True:
            try:
                if not (API_KEY and API_SECRET):
                    time.sleep(every); continue
                d = signed("/api/v3/account")
                cur = {b["asset"]: (float(b["free"]), float(b["locked"]))
                       for b in d.get("balances", [])
                       if float(b["free"]) or float(b["locked"])}
                changed = []
                for asset, (free, locked) in cur.items():
                    if asset not in prev:
                        changed.append({"asset": asset, "to": free, "from": None})
                    elif abs(prev[asset][0] - free) > 1e-12:
                        changed.append({"asset": asset, "from": prev[asset][0], "to": free})
                for asset in prev:
                    if asset not in cur:
                        changed.append({"asset": asset, "from": prev[asset][0], "to": 0.0})
                if changed and prev:
                    with self.lock:
                        self.events.append({"type": "balance", "changes": changed,
                                            "_recv_ms": int(time.time() * 1000)})
                        if len(self.events) > 500:
                            del self.events[:-500]
                prev = cur
                self.status = "polling balances (%d assets, %ds)" % (len(cur), int(every))
            except Exception as exc:
                self.status = "balance poll error: %s" % str(exc)[:60]
            time.sleep(every)

    def snapshot(self, limit=50):
        with self.lock:
            return self.events[-limit:]


USER_STREAM = UserStream()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/klines":
                sym = q.get("symbol", ["BTCUSDT"])[0]
                iv = q.get("interval", ["1m"])[0]
                lim = int(q.get("limit", ["120"])[0])
                return self._send(200, {"symbol": sym, "klines": get_klines(sym, iv, lim)})
            if u.path == "/price":
                sym = q.get("symbol", ["BTCUSDT"])[0]
                d = upstream("/api/v3/ticker/price?symbol=" + sym)
                return self._send(200, d)
            if u.path == "/flow":
                sym = q.get("symbol", ["BTCUSDT"])[0]
                bars = int(q.get("bars", ["60"])[0])
                f = flow(sym, bars)
                return self._send(200 if f else 502, f or {"error": "no data"})
            if u.path == "/flows":
                top = int(q.get("top", ["20"])[0])
                return self._send(200, flow_table(top))
            if u.path == "/movers":
                lim = int(q.get("limit", ["12"])[0])
                mq = float(q.get("minq", ["3000000"])[0])
                return self._send(200, movers(lim, mq))
            if u.path == "/streamprices":
                p_, ts, n = MARKET_STREAM.snapshot()
                q = urllib.parse.parse_qs(u.query).get("syms", [""])[0]
                if q:
                    want = [x for x in q.split(",") if x]
                    p_ = {k: v for k, v in p_.items() if k in want}
                return self._send(200, {"p": p_, "ts": ts, "msgs": n,
                                        "age_ms": int(time.time() * 1000) - ts if ts else None,
                                        "status": MARKET_STREAM.status,
                                        "reconnects": MARKET_STREAM.reconnects})
            if u.path == "/sse":
                import queue
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                q_ = queue.Queue(maxsize=200)
                MARKET_STREAM.listeners.append(q_)
                try:
                    p_, ts, n = MARKET_STREAM.snapshot()
                    self.wfile.write(("data: " + json.dumps({"p": p_, "ts": ts}) + "\n\n").encode())
                    self.wfile.flush()
                    while True:
                        try:
                            ev = q_.get(timeout=15)
                            self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
                except Exception:
                    pass
                finally:
                    try: MARKET_STREAM.listeners.remove(q_)
                    except ValueError: pass
                return
            if u.path == "/userstream":
                return self._send(200, {"status": USER_STREAM.status,
                                        "reconnects": USER_STREAM.reconnects,
                                        "events": USER_STREAM.snapshot(50)})
            if u.path == "/account":
                try:
                    d = signed("/api/v3/account")
                    bal = [{"asset": b["asset"], "free": float(b["free"]),
                            "locked": float(b["locked"])}
                           for b in d.get("balances", [])
                           if float(b["free"]) or float(b["locked"])]
                    return self._send(200, {"canTrade": d.get("canTrade"),
                                            "accountType": d.get("accountType"),
                                            "balances": bal[:40], "count": len(bal)})
                except Exception as exc:
                    return self._send(502, {"error": str(exc)[:200]})
            if u.path == "/health":
                return self._send(200, {"ok": True, "service": "market_data",
                                        "key_loaded": bool(API_KEY), "stats": STATS,
                                        "cached": len(CACHE), "ttl_s": KL_TTL,
                                        "max_rps": MAX_RPS,
                                        "banned_for": max(0, round(BANNED_UNTIL["t"] - time.time()))})
        except Exception as exc:
            return self._send(502, {"error": str(exc)[:160], "stats": STATS})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8796)
    a = ap.parse_args()
    print("market_data on :%d (ttl %.0fs, %.0f rps, key %s)" % (
        a.port, KL_TTL, MAX_RPS, "loaded" if API_KEY else "absent"), flush=True)
    threading.Thread(target=refresh_subs, args=(60,), daemon=True).start()
    threading.Thread(target=warmer, args=(20, 20.0), daemon=True).start()
    threading.Thread(target=publish_loop, daemon=True).start()
    threading.Thread(target=MARKET_STREAM.run, daemon=True).start()
    if API_KEY:
        threading.Thread(target=USER_STREAM.run_polling, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
