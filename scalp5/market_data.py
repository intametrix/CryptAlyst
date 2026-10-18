#!/usr/bin/env python3
"""market_data - streams only. No polling of market data, ever.

Binance's rules, as published in the REST "LIMITS" page:

  * a 429 means you exceeded the request rate and must back off
  * repeated 429s, or failing to back off, become an IP ban (418)
  * bans scale from 2 minutes to 3 days
  * Retry-After says exactly how long to wait
  * limits are per IP, not per API key

So this service never polls market data. It holds ONE websocket to
/stream?streams=... carrying:

    !miniTicker@arr      every symbol's 24h open/high/low/volume/change
    <sym>@kline_1m       candles, pushed as they form and close
    <sym>@bookTicker     best bid/ask, pushed on every change

Everything the engines and the dashboard ask for is answered from memory.
The only REST calls are exchangeInfo (once, for the real limits) and an
optional signed account read.
"""
import json, os, socket, ssl, struct, sys, threading, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from lib import bus as _bus
except Exception:
    _bus = None
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.expanduser("~/.cryptalyst/binance.env")
STATE = os.path.join(HERE, "state")
WS_URL = "wss://stream.binance.com:9443/stream?streams="
TOP_N = int(os.environ.get("TOP_N", "40"))


# ---------------------------------------------------------------- credentials
def _env(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        st = os.stat(ENV_PATH)
        if st.st_mode & 0o077:
            print("refusing credentials: %s is not 0600" % ENV_PATH, flush=True)
            return ""
        for line in open(ENV_PATH):
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


API_KEY = _env("BINANCE_API_KEY")
API_SECRET = _env("BINANCE_API_SECRET")


# ------------------------------------------------------------------- limits
class Limits:
    """Tracks the real limits from exchangeInfo and the weights we are charged."""

    def __init__(self):
        self.rules = []                 # [{"type":..,"intervalNum":..,"intervalLetter":..,"limit":..}]
        self.used = {}                  # type -> weight used in the current window
        self.window_start = {}          # type -> timestamp
        self.paused_until = 0.0         # set from Retry-After / 418
        self.pause_reason = ""
        self.violations = 0

    def load(self):
        try:
            with urllib.request.urlopen("https://api.binance.com/api/v3/exchangeInfo",
                                        timeout=20) as r:
                d = json.loads(r.read().decode())
            self.rules = d.get("rateLimits") or []
            print("limits from exchange: %s" % self.rules, flush=True)
            return d.get("symbols") or []
        except Exception as exc:
            print("exchangeInfo failed: %r" % (exc,), flush=True)
            return []

    def note_headers(self, headers):
        for k, v in headers.items():
            if k.lower().startswith("x-mbx-used-weight"):
                try:
                    self.used["REQUEST_WEIGHT"] = int(v)
                except Exception:
                    pass

    def pause(self, seconds, reason):
        self.paused_until = max(self.paused_until, time.time() + seconds)
        self.pause_reason = reason
        self.violations += 1
        print("PAUSED %.0fs — %s" % (seconds, reason), flush=True)

    def allowed(self):
        """True only if we are inside every published limit and not paused."""
        if time.time() < self.paused_until:
            return False
        for rule in self.rules:
            if rule.get("rateLimitType") == "REQUEST_WEIGHT":
                limit = rule["limit"]
                if self.used.get("REQUEST_WEIGHT", 0) > limit * 0.8:
                    return False
        return True

    def status(self):
        return {"paused_for": max(0, round(self.paused_until - time.time())),
                "reason": self.pause_reason, "used_weight": self.used.get("REQUEST_WEIGHT", 0),
                "rules": self.rules, "violations": self.violations}


LIMITS = Limits()


def rest(path, signed=False, params=None):
    """Every REST call goes through here: obey pause, obey weight, honour Retry-After."""
    import hashlib, hmac, urllib.parse
    if signed and not (API_KEY and API_SECRET):
        raise RuntimeError("no credentials")
    if not LIMITS.allowed():
        raise RuntimeError("paused by rate limits: %s" % LIMITS.pause_reason)
    q = urllib.parse.urlencode(params or {})
    if signed:
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        q = urllib.parse.urlencode(params)
        q += "&signature=" + hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    url = "https://api.binance.com" + path + ("?" + q if q else "")
    req = urllib.request.Request(url, headers={
        "User-Agent": "cryptalyst/1", **({"X-MBX-APIKEY": API_KEY} if API_KEY else {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            LIMITS.note_headers(dict(r.headers))
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        retry = exc.headers.get("Retry-After")
        if exc.code == 429:
            LIMITS.pause(float(retry or 60), "429 too many requests")
        elif exc.code == 418:
            LIMITS.pause(float(retry or 120), "418 IP ban")
        raise


# ------------------------------------------------------------------- streams
KLINES = {}      # (symbol, interval) -> [ {t,o,h,l,c,v,q,V,Q} ]  closed candles
MINI = {}        # symbol -> 24h stats from !miniTicker@arr
PRICES = {}      # symbol -> last bookTicker price
STATS = {"msgs": 0, "reconnects": 0, "last_ms": 0, "status": "idle"}
LOCK = threading.Lock()
SYMBOLS = []


class WS:
    @staticmethod
    def connect(url):
        host_path = url.split("//", 1)[1]
        host, path = host_path.split("/", 1)
        sock = socket.create_connection((host.split(":")[0], 443), timeout=20)
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host.split(":")[0])
        import base64
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(4096)
        return sock

    @staticmethod
    def read(sock):
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
    def pong(sock, data):
        mask = os.urandom(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask + payload)


def on_mini(d):
    """!miniTicker@arr -> every symbol's 24h stats. One message, whole market."""
    with LOCK:
        for x in d:
            sym = x.get("s")
            if not sym:
                continue
            o = float(x.get("o") or 0); c = float(x.get("c") or 0)
            MINI[sym] = {"s": sym, "o": o, "h": float(x.get("h") or 0),
                         "l": float(x.get("l") or 0), "c": c,
                         "v": float(x.get("v") or 0), "q": float(x.get("q") or 0),
                         "pct": (100 * (c / o - 1)) if o else 0.0}


def on_kline(k):
    sym = k.get("s"); iv = k.get("i")
    row = {"t": k.get("t"), "o": float(k["o"]), "h": float(k["h"]), "l": float(k["l"]),
           "c": float(k["c"]), "v": float(k["v"]), "q": float(k["q"]),
           "V": float(k.get("V") or 0), "Q": float(k.get("Q") or 0)}
    key = (sym, iv)
    with LOCK:
        series = KLINES.setdefault(key, [])
        if k.get("x"):                       # candle closed
            if series and series[-1]["t"] == row["t"]:
                series[-1] = row
            else:
                series.append(row)
            del series[:-300]
        else:                                # candle forming
            if series and series[-1]["t"] == row["t"]:
                series[-1] = row
            else:
                series.append(row)


def _publish_prices():
    while True:
        time.sleep(0.25)
        try:
            with LOCK:
                snap = dict(PRICES)
            if snap and _bus:
                _bus.pub("market.price", "ALL", {"p": snap}, "market_data")
        except Exception:
            pass


def on_book(d):
    sym = d.get("s")
    bid = d.get("b"); ask = d.get("a")
    try:
        px = float(bid or ask or 0)
    except Exception:
        return
    if sym and px > 0:
        with LOCK:
            PRICES[sym] = px


def seed_history(symbols, intervals=("1m", "15m"), limit=200):
    """Ask for history ONCE per symbol so indicators have depth, then never again.

    40 symbols x 2 intervals = 80 requests at boot, weight ~160 of a 6000/min budget.
    The seeded candles are written to disk so a restart does not repeat it.
    """
    os.makedirs(os.path.join(STATE, "seed"), exist_ok=True)
    for sym in symbols:
        for iv in intervals:
            path = os.path.join(STATE, "seed", "%s_%s.json" % (sym, iv))
            if os.path.exists(path) and time.time() - os.path.getmtime(path) < 3600:
                try:
                    rows = json.load(open(path))
                    with LOCK:
                        KLINES[(sym, iv)] = rows[-limit:]
                    continue
                except Exception:
                    pass
            if not LIMITS.allowed():
                time.sleep(5)
                continue
            try:
                rows = rest("/api/v3/klines",
                            params={"symbol": sym, "interval": iv, "limit": limit})
                candles = [{"t": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                            "c": float(r[4]), "v": float(r[5]), "q": float(r[7]),
                            "V": float(r[9]), "Q": float(r[10])} for r in rows]
                with LOCK:
                    KLINES[(sym, iv)] = candles
                with open(path, "w") as fh:
                    json.dump(candles, fh)
            except Exception:
                pass
            time.sleep(0.25)


def stream_loop():
    streams = ["!miniTicker@arr"]
    for s in SYMBOLS:
        streams.append(s.lower() + "@kline_1m")
        streams.append(s.lower() + "@kline_15m")
        streams.append(s.lower() + "@bookTicker")
    url = WS_URL + "/".join(streams)
    while True:
        try:
            sock = WS.connect(url)
            STATS["status"] = "streaming %d streams" % len(streams)
            while True:
                op, payload = WS.read(sock)
                if op == 0x9:
                    WS.pong(sock, payload); continue
                if op == 0x8:
                    break
                if op not in (0x1, 0x2):
                    continue
                try:
                    env = json.loads(payload.decode("utf-8", "replace"))
                    data = env.get("data")
                    ev = env.get("stream") or ""
                    if ev == "!miniTicker@arr":
                        on_mini(data)
                    elif ev.endswith("@kline_1m"):
                        on_kline(data.get("k") or {})
                    elif ev.endswith("@bookTicker"):
                        on_book(data)
                    STATS["msgs"] += 1
                    STATS["last_ms"] = int(time.time() * 1000)
                    if _bus:
                        try:
                            if ev == "!miniTicker@arr":
                                _bus.pub("market.mini24h", "ALL", {"count": len(data or [])}, "market_data")
                            elif ev.endswith("@kline_1m"):
                                kk = data.get("k") or {}
                                _bus.pub("market.candle", kk.get("s"),
                                         {"iv": "1m", "c": float(kk.get("c") or 0),
                                          "closed": bool(kk.get("x"))}, "market_data")
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception as exc:
            STATS["status"] = "reconnecting: %s" % str(exc)[:60]
            STATS["reconnects"] += 1
            time.sleep(5)


# ---------------------------------------------------------------- derived
def klines(symbol, interval="1m", limit=200):
    with LOCK:
        return list(KLINES.get((symbol, interval), []))[-limit:]


def movers(limit=12, min_quote=0.0):
    with LOCK:
        rows = [dict(v) for v in MINI.values() if v["s"].endswith("USDT") and v["q"] >= min_quote]
    by_vol = sorted(rows, key=lambda x: x["q"], reverse=True)[:limit]
    gain = sorted(rows, key=lambda x: x["pct"], reverse=True)[:limit]
    lose = sorted(rows, key=lambda x: x["pct"])[:limit]
    norm = lambda xs: [{"s": x["s"], "p": x["c"], "pct": round(x["pct"], 3),
                        "qv": x["q"], "n": 0} for x in xs]
    return {"most_traded": norm(by_vol), "gainers": norm(gain), "losers": norm(lose),
            "universe": len(rows), "ts": int(time.time() * 1000)}


def flow(symbol, bars=30):
    rows = klines(symbol, "1m", bars)
    if not rows:
        return None
    buy = sum(r["Q"] for r in rows)
    total = sum(r["q"] for r in rows)
    sell = max(0.0, total - buy)
    ratio = (buy / total) if total else 0.5
    series = [round(r["Q"] - max(0.0, r["q"] - r["Q"]), 2) for r in rows]
    sent = ("strong buying" if ratio > 0.60 else "buying" if ratio > 0.53 else
            "strong selling" if ratio < 0.40 else "selling" if ratio < 0.47 else "balanced")
    return {"symbol": symbol, "net_quote": round(buy - sell, 2), "buy_quote": round(buy, 2),
            "sell_quote": round(sell, 2), "buy_ratio": round(ratio, 4),
            "sentiment": sent, "bars": len(rows), "series": series[-40:]}


def flow_table(top=14):
    rows = []
    for x in movers(top, 0)["most_traded"]:
        f = flow(x["s"], 30)
        if f:
            f["pct"] = x["pct"]; f["qv"] = x["qv"]; rows.append(f)
    rows.sort(key=lambda r: r["net_quote"], reverse=True)
    return {"rows": rows, "ts": int(time.time() * 1000),
            "buying": sum(1 for r in rows if r["buy_ratio"] > 0.53),
            "selling": sum(1 for r in rows if r["buy_ratio"] < 0.47)}


# -------------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/klines":
                sym = q.get("symbol", ["BTCUSDT"])[0]
                iv = q.get("interval", ["1m"])[0]
                lim = int(q.get("limit", ["200"])[0])
                rows = klines(sym, iv, lim)
                return self._send(200, {"symbol": sym, "klines": rows, "source": "stream"})
            if u.path == "/movers":
                return self._send(200, movers(int(q.get("limit", ["12"])[0]),
                                              float(q.get("minq", ["0"])[0])))
            if u.path == "/flow":
                f = flow(q.get("symbol", ["BTCUSDT"])[0], int(q.get("bars", ["30"])[0]))
                return self._send(200 if f else 503, f or {"error": "warming up"})
            if u.path == "/flows":
                return self._send(200, flow_table(int(q.get("top", ["14"])[0])))
            if u.path == "/streamprices":
                with LOCK:
                    p = dict(PRICES)
                want = q.get("syms", [""])[0]
                if want:
                    keep = set(want.split(","))
                    p = {k: v for k, v in p.items() if k in keep}
                return self._send(200, {"p": p, "ts": STATS["last_ms"],
                                        "msgs": STATS["msgs"], "status": STATS["status"]})
            if u.path == "/price":
                sym = q.get("symbol", ["BTCUSDT"])[0]
                with LOCK:
                    return self._send(200, {"symbol": sym, "price": PRICES.get(sym)})
            if u.path == "/account":
                try:
                    d = rest("/api/v3/account", signed=True)
                    bal = [{"asset": b["asset"], "free": float(b["free"]), "locked": float(b["locked"])}
                           for b in d.get("balances", []) if float(b["free"]) or float(b["locked"])]
                    return self._send(200, {"canTrade": d.get("canTrade"), "balances": bal[:40],
                                            "count": len(bal)})
                except Exception as exc:
                    return self._send(502, {"error": str(exc)[:160], "limits": LIMITS.status()})
            if u.path == "/health":
                with LOCK:
                    n_k = len(KLINES)
                    n_m = len(MINI)
                    n_p = len(PRICES)
                return self._send(200, {"ok": True, "service": "market_data", "mode": "streams-only",
                                        "symbols_streamed": len(SYMBOLS), "kline_series": n_k,
                                        "mini_tickers": n_m, "prices": n_p, "stats": STATS,
                                        "limits": LIMITS.status()})
        except Exception as exc:
            return self._send(502, {"error": str(exc)[:200], "limits": LIMITS.status()})
        return self._send(404, {"error": "not found"})


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--top", type=int, default=TOP_N)
    a = ap.parse_args()

    info = LIMITS.load()                     # the one REST call we make at boot
    usdt = [s for s in info if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"]
    usdt.sort(key=lambda s: 0)
    SYMBOLS[:] = [s["symbol"] for s in usdt[:a.top]]
    if not SYMBOLS:
        SYMBOLS[:] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                      "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
    print("market_data streams-only on :%d | %d symbols | limits %s" % (
        a.port, len(SYMBOLS), LIMITS.rules), flush=True)
    threading.Thread(target=stream_loop, daemon=True).start()
    threading.Thread(target=_publish_prices, daemon=True).start()
    threading.Thread(target=seed_history, args=(list(SYMBOLS),), daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
