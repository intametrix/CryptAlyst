#!/usr/bin/env python3
"""ONE dashboard. Every engine, one page, no port hopping.

It reads each engine's state directly and renders them together:

    turtle (Go)      :8787/api/turtle + /stats
    swing            :8790/api/state
    five adaptive    :8797/api/state
    turtle x5 fam    :8798/api/state
    model aggregate  :8792/api/aggregate

Anything offline is shown as offline rather than silently dropped.

    python3 master.py --port 8799   ->  http://127.0.0.1:8799
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

SOURCES = [
    ("turtle", "Go · Donchian/2N", "#a371f7"),
    ("swing", "alerts · model gate", "#3fb950"),
    ("multimodels", "5 adaptive TA models", "#f778ba"),
    ("turtlex5", "Turtle 10s-2m", "#58a6ff"),
    ("hft", "RSI/MACD 1.5s", "#d29922"),
]


_META = {"at": 0.0, "rows": {}}


BUILD_TS = int(time.time() * 1000)
UNI800 = {"ts": 0, "rows": []}
def norm_ret(d):
    """Always derive return from equity/start_equity - book files disagree."""
    try:
        eq = d.get("equity")
        st = d.get("start_equity") or d.get("start") or 0
        if eq is not None and st:
            return round(100 * (eq / st - 1), 4)
        return d.get("return_pct")
    except Exception:
        return d.get("return_pct")


SPARK = {"ts": 0, "data": {}, "ttl": 10.0}
METRIC_CACHE = {"ts": 0, "data": {}}
METRIC_HIST = {}     # symbol -> {"price":[], "volume":[], "risk":[], "liquidity":[], "forecast":[]}
METRIC_MAX = 40


def metric_sample(n=30):
    """One sample of the five metrics per liquid symbol, appended to a ring buffer."""
    if METRIC_CACHE["data"] and (time.time() - METRIC_CACHE["ts"]) < 10:
        return METRIC_CACHE["data"]
    rows = universe800(800)[:n]
    try:
        votes = jget("http://127.0.0.1:8787/api/models/signals?limit=400", timeout=8).get("signals") or []
    except Exception:
        votes = []
    fmap = {}
    for v in votes:
        sym = v.get("symbol")
        if not sym:
            continue
        d = (v.get("direction") or "").upper()
        fmap[sym] = fmap.get(sym, 0) + (1 if ("BUY" in d or "PUMP" in d) else -1)
    def fetch(sym):
        try:
            rows = jget("http://127.0.0.1:8796/klines?symbol=%s&interval=1m&limit=60"
                        % sym, timeout=8.0).get("klines") or []
            return sym, rows or None
        except Exception:
            return sym, None

    from concurrent.futures import ThreadPoolExecutor
    syms = [r.get("s") for r in rows if r.get("s")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(pool.map(fetch, syms))

    out = {}
    for r in rows:
        sym = r.get("s")
        if not sym:
            continue
        d = fetched.get(sym)
        if not d:
            continue
        def col(row, key, idx):
            return float(row[key]) if isinstance(row, dict) else float(row[idx])
        cl = [col(x, "c", 4) for x in d]
        vol = [col(x, "v", 5) for x in d]
        if len(cl) < 20:
            continue
        trs = [max(col(d[i], "h", 2) - col(d[i], "l", 3),
                   abs(col(d[i], "h", 2) - col(d[i - 1], "c", 4)),
                   abs(col(d[i], "l", 3) - col(d[i - 1], "c", 4)))
               for i in range(1, len(d))]
        atr = sum(trs[-14:]) / max(1, len(trs[-14:]))
        risk = 100 * atr / cl[-1] if cl[-1] else 0.0
        liq = (r.get("v") or 0) / 1e6
        h = METRIC_HIST.setdefault(sym, {"price": [], "volume": [], "risk": [],
                                         "liquidity": [], "forecast": []})
        h["price"].append(round(cl[-1], 8))
        h["volume"].append(round(sum(vol[-15:]) / 15.0, 2))
        h["risk"].append(round(risk, 4))
        h["liquidity"].append(round(liq, 2))
        h["forecast"].append(fmap.get(sym, 0))
        for k in h:
            h[k] = h[k][-METRIC_MAX:]
        out[sym] = dict(h)
    METRIC_CACHE["ts"], METRIC_CACHE["data"] = time.time(), out
    return out


def sparklines(n=24, interval="1m", limit=60):
    """Short close series per liquid symbol, fetched in parallel and cached."""
    if SPARK["data"] and (time.time() - SPARK["ts"]) < SPARK.get("ttl", 10.0):
        return SPARK["data"]

    def fetch(sym):
        try:
            d = (jget("http://127.0.0.1:8796/klines?symbol=%s&interval=%s&limit=%d"
                      % (sym, interval, limit), timeout=8.0).get("klines") or [])
            if d and isinstance(d[0], dict):
                d = [[x["t"], x["o"], x["h"], x["l"], x["c"], x["v"]] for x in d]
            return sym, [float(x[4]) for x in d]
        except Exception:
            return sym, None

    rows = universe800(800)[:n]
    syms = [r.get("s") for r in rows if r.get("s")]
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for sym, series in pool.map(fetch, syms):
            if series:
                out[sym] = series
    SPARK["ts"], SPARK["data"] = time.time(), out
    return out


SYMBOLS = {"rows": [], "ts": 0}      # cache for all_symbols()


def all_symbols():
    """Every tradable symbol on spot and futures, not just USDT pairs."""
    if SYMBOLS["rows"] and (time.time() - SYMBOLS["ts"]) < 600:
        return SYMBOLS["rows"]
    seen = {}
    for base, key in (("https://api.binance.com/api/v3/exchangeInfo", "spot"),
                      ("https://fapi.binance.com/fapi/v1/exchangeInfo", "futures")):
        try:
            d = jget(base, timeout=14.0)
        except Exception:
            continue
        for x in d.get("symbols") or []:
            if x.get("status") not in ("TRADING", None):
                continue
            sym = x.get("symbol")
            if not sym:
                continue
            cur = seen.setdefault(sym, {"s": sym, "base": x.get("baseAsset"),
                                        "quote": x.get("quoteAsset"), "mkts": [], "qvol": 0})
            if key not in cur["mkts"]:
                cur["mkts"].append(key)
    try:
        for r in jget("https://api.binance.com/api/v3/ticker/24hr", timeout=14.0):
            c = seen.get(r.get("symbol"))
            if c:
                c["qvol"] = float(r.get("quoteVolume") or 0)
                c["price"] = float(r.get("lastPrice") or 0)
                c["chg"] = float(r.get("priceChangePercent") or 0)
    except Exception:
        pass
    rows = sorted(seen.values(), key=lambda x: -(x.get("qvol") or 0))
    SYMBOLS["ts"], SYMBOLS["rows"] = time.time(), rows
    return rows
TICK = {"ts": 0, "p": {}}


TRADING_STATE = {"on": False}
PROCS = {}


def _spawn(name, argv):
    import subprocess
    logf = open(os.path.join(HERE, "logs", name + ".log"), "ab")
    PROCS[name] = subprocess.Popen(argv, cwd=HERE, stdout=logf, stderr=logf,
                                   start_new_session=True)


def trading_start():
    try:
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
    except Exception:
        pass
    control_publish("start")
    TRADING_STATE["stopped"] = False
    if TRADING_STATE["on"]:
        return TRADING_STATE
    py = sys.executable or "python3"
    engines = {
        "models10": [py, "-u", "models10.py", "--start", "5000", "--top", "60",
                     "--loop", "2", "--probation", "60"],
        "crypto_pack": [py, "-u", "crypto_pack.py", "--risk", "0.5", "--start", "5000",
                        "--top", "32", "--min-liq", "150000000", "--loop", "1.0"],
        "executor": [py, "-u", "executor.py", "--risk", "0.5", "--lock", "0.50",
                     "--clock", "180", "--min-alert", "60", "--start", "5000"],
    }
    for name, argv in engines.items():
        try:
            _spawn(name, argv)
        except Exception:
            pass
    TRADING_STATE["on"] = True
    return TRADING_STATE


STOP_FLAG = os.path.join(HERE, "state", "STOP")


def control_publish(action):
    """Tell every part of the platform, not just this process."""
    try:
        from lib import bus
        b = bus.Bus()
        b.publish("system.control", {"action": action, "actor": "dashboard",
                                     "ts": int(time.time() * 1000)})
        b.close()
        return True
    except Exception:
        return False


def reset_stats(archive):
    """Clear the counters that describe trading, keeping an archive."""
    import glob
    import shutil
    cleared = 0
    for pattern in ("stats*.json", "model_stats*.json", "decisions*.json",
                    "signals*.json", "paper*.json", "equity*.json",
                    "heartbeat.json", "tape.json", "summary.json", "alerts*.json",
                    "counters.json", "pairs.json"):
        for path in glob.glob(os.path.join(HERE, "state", pattern)):
            try:
                shutil.copy2(path, os.path.join(archive, os.path.basename(path)))
                os.remove(path)
                cleared += 1
            except Exception:
                pass
    return cleared


def reset_books():
    """Empty the books and put equity back to the opening balance, keeping an
    archive of what was there."""
    import glob
    import shutil
    stamp = time.strftime("%Y%m%d_%H%M%S")
    archive = os.path.join(HERE, "state", "archive_stop_" + stamp)
    os.makedirs(archive, exist_ok=True)
    reset = 0
    # packs and the per-strategy records both hold positions and equity
    patterns = ("pack_*.json", "strategy_*.json", "edge_*.json", "slow_*.json", "alpha_*.json")
    paths = []
    for pat in patterns:
        paths.extend(glob.glob(os.path.join(HERE, "state", pat)))
    for path in paths:
        try:
            with open(path) as fh:
                book = json.load(fh)
        except Exception:
            continue
        shutil.copy2(path, os.path.join(archive, os.path.basename(path)))
        book["equity"] = book.get("start_equity") or book.get("equity") or 5000
        book["positions"] = {}
        book["trades"] = []
        book["realised"] = 0.0
        book["trade_count"] = 0
        book["closed"] = 0
        book["stopped_at"] = int(time.time() * 1000)
        with open(path, "w") as fh:
            json.dump(book, fh)
        reset += 1
    return reset, archive


def trading_stop():
    TRADING_STATE["on"] = False
    for name, pr in list(PROCS.items()):
        try:
            pr.terminate()
        except Exception:
            pass
    PROCS.clear()
    import subprocess
    # NOTE: the feed, the recorder and the explorer are deliberately absent here.
    # STOP stops trading; market data keeps running.
    for pat in ("alpha.py", "multi_models.py", "stats_models.py", "slow_edges.py",
                "sympathy_lag.py", "crypto_strategies.py",
                "models10.py", "crypto_pack.py", "executor.py",
                "alpha_rank.py", "alpha_signals.py", "skim_pump.py", "curve_rider.py",
                "hft.py", "movers.py", "grid.py", "hybrids.py",
                "flash.py", "fast_ignition.py", "swing_v2.py", "swing_turtle.py",
                "turtle_multi.py", "swing.py", "ignition.py", "momentum.py",
                "mean_revert.py", "breakout.py", "scalper.py"):
        subprocess.run(["pkill", "-f", pat], check=False)
    try:
        with open(STOP_FLAG, "w") as fh:
            json.dump({"stopped_at": int(time.time() * 1000), "actor": "dashboard"}, fh)
    except Exception:
        pass
    control_publish("stop")
    reset, archive = reset_books()
    stats = reset_stats(archive)
    TRADING_STATE["stopped"] = True
    TRADING_STATE["reset_books"] = reset
    TRADING_STATE["reset_stats"] = stats
    TRADING_STATE["archive"] = archive
    TRADING_STATE["market_data"] = "running" if _alive("bin/marketdata") else "not running"
    try:
        req = urllib.request.Request("http://127.0.0.1:8787/api/turtle?action=stop",
                                     data=b"{}", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=6).read()
    except Exception:
        pass
    return TRADING_STATE


_PROC_CACHE = {"ts": 0.0, "lines": []}


def proc_runs(pat, lines=None):
    """True when a process is *running* pat as its own script argument."""
    for ln in (lines if lines is not None else _proc_snapshot()):
        toks = ln.split()
        if any(t == pat or t.endswith("/" + pat) for t in toks):
            return True
    return False


def _proc_snapshot():
    """One `ps` for the whole request, cached for a couple of seconds: asking
    pgrep once per engine made /api/all slow enough to time out."""
    import subprocess
    if time.time() - _PROC_CACHE["ts"] < 3.0:
        return _PROC_CACHE["lines"]
    try:
        out = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, timeout=5).stdout
        lines = [ln for ln in out.splitlines() if ".py" in ln]
    except Exception:
        lines = []
    _PROC_CACHE.update({"ts": time.time(), "lines": lines})
    return lines


def live_heartbeat():
    """What is true now: books actually running, and equity straight from the pack
    files. Used whenever the stored heartbeat is stale."""
    import glob
    import subprocess
    running = 0
    for pat in ("models10.py", "crypto_pack.py", "executor.py", "grid.py", "hft.py",
                "movers.py", "hybrids.py", "alpha_rank.py", "skim_pump.py",
                "curve_rider.py", "alpha_signals.py", "supervisor.py"):
        if subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0:
            running += 1
    rows, open_pos, realised = [], 0, 0.0
    for f in sorted(glob.glob(os.path.join(HERE, "state", "pack_*.json"))):
        try:
            with open(f) as fh:
                b = json.load(fh)
        except Exception:
            continue
        start = float(b.get("start_equity") or 0)
        eq = float(b.get("equity") or 0)
        rows.append({"k": b.get("engine"), "eq": round(eq, 2),
                     "ret": round(100 * (eq / start - 1), 2) if start else 0,
                     "real": round(eq - start, 2), "open": len(b.get("positions") or []),
                     "closed": b.get("trade_count") or len(b.get("trades") or [])})
        open_pos += len(b.get("positions") or [])
        realised += eq - start
    return {"ts": int(time.time() * 1000), "beat_s": 5.0, "books": running,
            "open": open_pos, "realised": round(realised, 2), "rows": rows,
            "source": "live"}


def _alive(pattern):
    import subprocess
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def trading_loop_state():
    """Report what is actually alive, not what we asked for."""
    procs = _proc_snapshot()
    alive = {}
    for name, pat in (("models10", "models10.py"), ("crypto_pack", "crypto_pack.py"),
                      ("executor", "executor.py")):
        alive[name] = proc_runs(pat, procs)
    TRADING_STATE["alive"] = alive
    return TRADING_STATE


def metric_loop(every=15.0, n=30):
    """Sample the five metrics on a timer so sparkline history fills even with
    nobody watching, and so the cache is always warm when the UI asks."""
    import threading

    def run():
        while True:
            try:
                metric_sample(n)
            except Exception:
                pass
            time.sleep(every)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def ticker_loop():
    """Read prices from the stream cache. We never collect them a second time."""
    import threading
    def run():
        while True:
            try:
                d = jget("http://127.0.0.1:8796/streamprices", timeout=4.0)
                if isinstance(d, dict) and d.get("p"):
                    TICK["p"] = {k: float(v) for k, v in d["p"].items()}
                    TICK["ts"] = time.time()
            except Exception:
                pass
            time.sleep(1.0)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def universe800(limit=800):
    """Top N by 24h liquidity across spot AND futures (futures has far more pairs)."""
    if UNI800["rows"] and (time.time() - UNI800["ts"]) < 120:
        return UNI800["rows"]
    seen = {}
    for base in ("https://api.binance.com/api/v3/ticker/24hr",
                 "https://fapi.binance.com/fapi/v1/ticker/24hr"):
        try:
            for r in jget(base, timeout=12.0):
                sym = r.get("symbol") or ""
                if not sym.endswith("USDT"):
                    continue
                qv = float(r.get("quoteVolume") or 0)
                px = float(r.get("lastPrice") or 0)
                chg = float(r.get("priceChangePercent") or 0)
                prev = seen.get(sym)
                if not prev or qv > prev["v"]:
                    seen[sym] = {"s": sym, "v": qv, "price": px, "c": chg,
                                 "vol": None, "qvol": qv, "mcap": None}
        except Exception:
            continue
    rows = sorted(seen.values(), key=lambda x: -(x["v"] or 0))[:limit]
    UNI800["ts"], UNI800["rows"] = time.time(), rows
    return rows


def market_meta():
    """price · volatility · liquidity · volume · market cap for every pair.

    Volume/price/range come from the Go universe (free, already streaming).
    Market cap comes from CoinGecko, batched and cached for 10 minutes.
    """
    now = time.time()
    if _META["rows"] and now - _META["at"] < 600:
        return _META["rows"]
    rows = {}
    try:
        d = jget("http://127.0.0.1:8787/api/universe", timeout=8.0)
        for r in d.get("symbols") or []:
            sym = r.get("symbol") or ""
            if not sym.endswith("USDT"):
                continue
            px = float(r.get("last_price") or 0)
            hi = float(r.get("high_price") or 0)
            lo = float(r.get("low_price") or 0)
            qv = float(r.get("quote_volume_24h") or 0)
            rows[sym] = {
                "price": px,
                "vol_pct": (100.0 * (hi - lo) / px) if px else None,
                "liquidity": qv,
                "volume": float(r.get("volume_24h") or 0),
                "change": float(r.get("change_24h_pct") or 0),
                "mcap": None,
            }
    except Exception:
        pass
    # market caps from the ranked markets endpoint: one list, biggest first,
    # so a symbol collision always resolves to the major coin
    try:
        caps = {}
        for page in (1, 2):
            lst = jget("https://api.coingecko.com/api/v3/coins/markets"
                       "?vs_currency=usd&order=market_cap_desc&per_page=250"
                       "&page=%d&sparkline=false" % page, timeout=20.0)
            for c in lst:
                sym = (c.get("symbol") or "").upper()
                if sym and sym not in caps and c.get("market_cap"):
                    caps[sym] = float(c["market_cap"])
        for sym, m in rows.items():
            m["mcap"] = caps.get(sym[:-4])
    except Exception:
        pass
    _META["rows"] = rows
    _META["at"] = now
    return rows


def jget(url, timeout=3.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "master/1.0"}),
                                timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(paths, base="http://127.0.0.1"):
    out = {}
    for name, url in paths:
        try:
            out[name] = jget(base + url)
        except Exception:
            out[name] = None
    return out


class StreamHub:
    """Fan-out to browser clients, fed by lib.bus subscriptions.

    The page used to read an in-memory bus. It now subscribes to RabbitMQ via
    lib.bus and re-serves the envelopes as server-sent events.
    """

    TOPICS = ["ticks", "market.price", "intel.signal", "signals", "model.fill",
              "model.pnl", "model.decision", "intel.forecast", "intel.ta",
              "intel.flow", "system.health", "market.depth", "market.mini24h"]

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()
        self.started = False
        self.count = 0
        self.subscribed = []

    def register(self):
        q = queue.Queue(maxsize=500)
        with self.lock:
            self.clients.add(q)
        return q

    def unregister(self, q):
        with self.lock:
            self.clients.discard(q)

    def push(self, payload):
        with self.lock:
            dead = []
            for q in self.clients:
                try:
                    q.put_nowait(payload)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.clients.discard(q)
        self.count += 1

    def start(self):
        if self.started:
            return
        self.started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        from lib import bus
        while True:
            try:
                for topic in self.TOPICS:
                    def handler(env, topic=topic):
                        self.push({"topic": topic, "data": env.data})
                    bus.sub(topic, handler)
                self.subscribed = list(self.TOPICS)
                print("stream hub subscribed to %d topics via RabbitMQ"
                      % len(self.TOPICS), flush=True)
                while True:
                    time.sleep(30)
            except Exception as exc:
                print("stream hub reconnecting: %r" % (exc,), flush=True)
                time.sleep(3)


HUB = StreamHub()


COUNTERS = os.path.join(HERE, "state", "counters.json")



def safe_jget(url, default, timeout=8.0, note=""):
    """Fetch JSON, or return a valid empty shape with the reason attached."""
    try:
        return jget(url, timeout=timeout)
    except Exception as exc:
        out = dict(default) if isinstance(default, dict) else default
        if isinstance(out, dict):
            out["unavailable"] = True
            out["reason"] = note or str(exc)[:120]
        return out


def closed_high_water(live):
    """Total closed trades ever seen. Monotonic by construction.

    Books get archived and reset; the count of closed trades must not follow them
    down, so we keep a high-water mark and only ever raise it.
    """
    hw = 0
    try:
        with open(COUNTERS) as fh:
            hw = int(json.load(fh).get("closed_hw") or 0)
    except Exception:
        hw = 0
    n = max(hw, int(live or 0))
    if n != hw:
        try:
            os.makedirs(os.path.dirname(COUNTERS), exist_ok=True)
            tmp = "%s.%d.t" % (COUNTERS, os.getpid())
            with open(tmp, "w") as fh:
                json.dump({"closed_hw": n, "ts": int(time.time() * 1000)}, fh)
            os.replace(tmp, COUNTERS)
        except Exception:
            pass
    return n


def collect():
    engines = []

    # ---- turtle (Go backend) — only when its session is actually running
    try:
        t = jget("http://127.0.0.1:8787/api/turtle")
        if (t.get("state") or "") != "running":
            raise RuntimeError("turtle idle")
        s = jget("http://127.0.0.1:8787/api/turtle/stats")
        engine = {
            "key": "turtle", "label": "Go · Donchian/2N", "colour": "#a371f7", "alive": True,
            "equity": t.get("equity"), "return_pct": norm_ret(t),
            "realised": t.get("realized_pnl"), "unrealised": t.get("unrealized_pnl"),
            "closed": s.get("closed"), "wins": None,
            "win_rate": s.get("win_rate_pct"),
            "positions": [{"symbol": p.get("symbol"), "side": p.get("side", "?"),
                           "gross_pct": 0, "held_s": 0}
                          for p in (t.get("positions") or {}).values()]
            if isinstance(t.get("positions"), dict) else [],
            "recent": [], "scans": None, "settings": {}, "funnel": {},
        }
    except Exception:
        engine = {"key": "turtle", "label": "Go · Donchian/2N", "colour": "#a371f7",
                  "alive": False, "equity": None, "return_pct": None, "realised": None,
                  "unrealised": None, "closed": None, "wins": None, "win_rate": None,
                  "positions": [], "recent": []}
    try:
        gc = jget("http://127.0.0.1:8787/api/turtle/config")["config"]
        engine["settings"] = {
            "risk_pct": round(float(gc.get("risk_pct", 0)) * 100, 3),
            "max_positions": gc.get("max_units_total"),
            "s1_entry": gc.get("s1_entry"), "s1_exit": gc.get("s1_exit"),
            "s2_entry": gc.get("s2_entry"), "s2_exit": gc.get("s2_exit"),
            "stop_n": gc.get("stop_n"), "n_lookback": gc.get("n_lookback"),
            "max_units_per_direction": gc.get("max_units_per_direction"),
            "max_units_per_market": gc.get("max_units_per_market"),
            "vol_veto_pct": gc.get("vol_veto_pct"),
        }
        engine["max_pos"] = gc.get("max_units_total")
    except Exception:
        pass
    engines.append(engine)

    # ---- single-state engines
    for key, label, colour, fname in (
        ("swing", "alerts · model gate", "#3fb950", "swing_state.json"),
        ("multimodels", "5 adaptive TA models", "#f778ba", "multi_models.json"),
        ("statsmodels", "OLS · z · logistic · autocorr", "#39d353", "stats_models.json"),
        ("swing_turtle", "Donchian 15m", "#a371f7", "swing_turtle.json"),
    ):
        try:
            _p = os.path.join(HERE, "state", fname)
            if (time.time() - os.path.getmtime(_p)) > 180:
                raise IOError("stale state file")
            with open(_p) as fh:
                d = json.load(fh)
            engines.append({
                "key": key, "label": label, "colour": colour, "alive": not d.get("missing"),
                "equity": d.get("equity"), "return_pct": norm_ret(d),
                "realised": d.get("realised", (d.get("equity", 0) - d.get("start_equity", 0))
                                  if d.get("equity") is not None else None),
                "unrealised": d.get("unrealised", round(sum(
                    (p.get("notional") or 0) * p.get("gross_pct", 0) / 100
                    for p in (d.get("positions") or [])), 2)),
                "closed": d.get("trade_count"), "wins": d.get("wins"), "win_rate": None,
                "positions": d.get("positions") or [],
                "recent": [{"symbol": t.get("symbol"), "side": t.get("side"),
                            "net_pct": t.get("net_pct"), "reason": t.get("reason")}
                           for t in (d.get("trades") or [])[-5:]],
                "trades": (d.get("trades") or [])[-80:],
                "risk_usd": d.get("open_risk_usd"),
                "max_pos": d.get("max_positions"),
                "tp": d.get("tp_pct"), "sl": d.get("sl_pct"),
                "basis": d.get("basis"), "start_equity": d.get("start_equity"),
                "scans": d.get("scans"), "settings": d.get("settings") or {},
                "funnel": d.get("funnel") or {},
                "config": {k: d.get(k) for k in
                           ("lock_trade_pct", "lock_usd", "trail_ratio",
                            "trail_arm_pct", "max_hold", "cycle_seconds",
                            "model_gate", "min_liquidity", "risk_pct")},
            })
        except Exception:
            engines.append({"key": key, "label": label, "colour": colour, "alive": False,
                            "equity": None, "return_pct": None, "realised": None,
                            "unrealised": None, "closed": None, "wins": None,
                            "win_rate": None, "positions": [], "recent": []})

    # ---- the five Turtle timeframes
    try:
        d = jget("http://127.0.0.1:8798/api/state")
        for e in d.get("engines") or []:
            engines.append({
                "key": e.get("name"), "label": "Turtle " + str(e.get("interval")),
                "colour": "#58a6ff", "alive": True,
                "equity": e.get("equity"), "return_pct": norm_ret(e),
                "realised": e.get("realised"), "unrealised": e.get("unrealised"),
                "closed": e.get("closed"), "wins": e.get("wins"), "win_rate": None,
                "positions": e.get("open") or [],
                "recent": e.get("recent") or [],
                "trades": e.get("recent") or [],
                "risk_usd": None, "max_pos": None, "tp": None, "sl": None,
                "basis": "Turtle " + str(e.get("interval")), "start_equity": 2000,
                "settings": e.get("settings") or {},
                "funnel": e.get("funnel") or {},
                "scans": e.get("scans"),
                "config": {},
            })
    except Exception:
        pass

    # ---- crypto strategies (each writes state/strategy_<name>.json)
    # A pack file is a record of a book, not proof that it is trading. Only a
    # running process counts, so a stopped book does not appear on the dashboard.
    def _running(name):
        if not name:
            return False
        if name.startswith("hybrid_"):
            pat = "hybrids.py --strategy " + name.replace("hybrid_", "")
        else:
            pat = name + ".py"
        return proc_runs(pat, _proc_snapshot())

    try:
        for fn in sorted(os.listdir(os.path.join(HERE, "state"))):
            if not ((fn.startswith("strategy_") or fn.startswith("edge_") or fn.startswith("slow_") or fn.startswith("alpha_") or fn.startswith("pack_"))
                    and fn.endswith(".json")):
                continue
            fp = os.path.join(HERE, "state", fn)
            if (time.time() - os.path.getmtime(fp)) > 600:
                continue
            with open(fp) as fh:
                d = json.load(fh)
            eng_name = d.get("engine")
            # a book is alive if its process runs, or if it is still writing its
            # pack: several engines write a pack per strategy, so the file name is
            # not the process name. A stopped book stops updating.
            fresh = (time.time() - os.path.getmtime(fp)) < 90
            if not (_running(eng_name) or fresh):
                continue
            engines.append({
                "key": eng_name, "label": d.get("basis", "crypto strategy"),
                "colour": "#39d353", "alive": True,
                "equity": d.get("equity"), "return_pct": norm_ret(d),
                "realised": d.get("realised"), "unrealised": d.get("unrealised"),
                "closed": d.get("trade_count"), "wins": d.get("wins"),
                "win_rate": None, "scans": d.get("opened"),
                "settings": d.get("settings") or {}, "funnel": {},
                "positions": d.get("positions") or [],
                "trades": d.get("trades") or [],
                "recent": (d.get("trades") or [])[-5:],
                "max_pos": d.get("max_positions"), "risk_usd": d.get("open_risk_usd"),
                "tp": d.get("tp_pct"), "sl": d.get("sl_pct"),
                "basis": d.get("basis"), "start_equity": d.get("start_equity"),
            })
    except Exception:
        pass

    # ---- model aggregate (not an engine, but the signal source)
    models = None
    try:
        models = jget("http://127.0.0.1:8792/api/aggregate")
    except Exception:
        models = None

    # a book with no equity is a placeholder, not a book
    engines = [e for e in engines if (e.get("equity") or 0) > 0]

    live_closed = sum((e.get("closed") or 0) for e in engines if e.get("equity") is not None)
    return {"ts": int(time.time() * 1000), "engines": engines, "models": models,
            "totals": {"closed": closed_high_water(live_closed),
                       "closed_live": live_closed}}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<title>All engines</title><style>
 :root{--bg:#080b10;--card:#111823;--line:#1e2733;--fg:#e8eef6;--dim:#8a99ad;
       --faint:#59677a;--up:#3fb950;--down:#f85149;--mono:ui-monospace,Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.45 -apple-system,BlinkMacSystemFont,var(--mono)}
 .num{font-family:var(--mono);font-variant-numeric:tabular-nums}
 header{padding:13px 20px;background:#0b1119;border-bottom:1px solid var(--line);
        display:flex;align-items:center;gap:14px;flex-wrap:wrap}
 h1{margin:0;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
 .pill{padding:3px 10px;border-radius:999px;border:1px solid var(--line);font-size:11px;color:var(--dim)}
 .pill.ok{color:var(--up);border-color:#1d3a26;background:#0e2416}
 .pill.bad{color:var(--down);border-color:#3a1d1d;background:#240f0f}
 .spacer{flex:1}
 .totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1px;
         background:var(--line);border-bottom:1px solid var(--line)}
 .kpi{background:#0d141d;padding:10px 20px}
 .kpi .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
 .kpi .v{font-size:17px;margin-top:2px;font-family:var(--mono);font-variant-numeric:tabular-nums}
 .wrap{max-width:1560px;margin:0 auto;padding:0 20px 28px}
 .section{margin-top:22px}
 .section > h2{margin:0 0 10px;font-size:11px;letter-spacing:.16em;text-transform:uppercase;
               color:var(--dim);display:flex;align-items:center;gap:10px}
 .section > h2:after{content:"";flex:1;height:1px;background:var(--line)}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:var(--gap)}
 .card{min-height:0;max-height:520px;overflow:auto}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
       padding:14px 16px;display:flex;flex-direction:column;min-width:0}
 .card.off{opacity:.45}
 .top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
 .name{font-size:12px;letter-spacing:.1em;text-transform:uppercase;font-weight:700}
 .label{font-size:10.5px;color:var(--faint);margin-top:1px}
 .ret{font-size:21px;font-family:var(--mono);font-variant-numeric:tabular-nums;line-height:1.1}
 .card .body{margin-top:8px;flex:1;min-height:0}
 .up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--dim)}
 .rows{display:flex;gap:14px;margin-top:8px;font-size:11px;color:var(--dim);flex-wrap:wrap}
 .rows b{color:var(--fg);font-family:var(--mono);font-weight:600}
 table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px;margin-top:9px}
 th{text-align:right;color:var(--faint);font-size:9px;letter-spacing:.1em;text-transform:uppercase;
    padding-bottom:3px;font-weight:600}
 th:first-child{text-align:left}
 td{text-align:right;padding:3px 0;border-top:1px solid #151d27}
 td:first-child{text-align:left;color:#58a6ff}
 .side{font-size:9.5px;padding:0 4px;border-radius:3px}
 .side.buy{color:var(--up);background:#0e2416}
 .side.sell{color:var(--down);background:#240f0f}
 .empty{color:var(--faint);font-size:11px;margin-top:8px}
 footer{padding:0 20px 20px;color:var(--faint);font-size:11px;font-family:var(--mono)}

  /* ---------- detail sheet ---------- */
  .modal{position:fixed;inset:0;background:#04070bd9;backdrop-filter:blur(4px);
         display:none;z-index:60;padding:26px;overflow:auto}
  .modal.open{display:block}
  .sheet{max-width:1240px;margin:0 auto;border:1px solid #2a3a4a;border-radius:18px;
         background:linear-gradient(170deg,#131b26,#0d141d);overflow:hidden;
         box-shadow:0 40px 100px -40px #000}
  .modal.open .sheet{animation:rise .18s ease-out}
  @keyframes rise{from{opacity:0;transform:translateY(12px) scale(.99)}to{opacity:1;transform:none}}

  .sheet .head{position:sticky;top:0;z-index:5;padding:18px 28px 16px;
               background:linear-gradient(120deg,#111b27,#17222f);
               border-bottom:1px solid #24313e}
  .sheet .head h2{margin:0;font-size:21px;font-weight:700;letter-spacing:.02em}
  .sheet .head .label{font-size:11.5px;color:#7d8b9e}
  .sheet .head .sub{margin-top:3px;font-size:11px;color:#59677a;letter-spacing:.06em}
  .sheet .head .big{position:absolute;right:66px;top:16px;font-size:34px;line-height:1;
                    font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .close2{position:absolute;right:20px;top:16px;width:30px;height:30px;border-radius:50%;
          border:1px solid #2a3a4a;background:#0c131b;color:#8494a8;cursor:pointer;
          display:flex;align-items:center;justify-content:center;font-size:16px}
  .close2:hover{color:#e8eef6;border-color:#3d5470}

  .body2{padding:18px 28px 26px;max-height:calc(100vh - 150px);overflow:auto}

  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));gap:10px}
  .tile{background:#0f1721;border:1px solid #1d2836;border-radius:11px;padding:10px 13px}
  .tile .k{font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:#59677a}
  .tile .v{font-size:17px;font-family:var(--mono);margin-top:3px;
           font-variant-numeric:tabular-nums;color:#e8eef6}

  .expo{margin-top:12px;background:#0f1721;border:1px solid #1d2836;border-radius:11px;
        padding:11px 14px}
  .expo .lbl{display:flex;justify-content:space-between;font-size:10.5px;color:#8494a8;
             letter-spacing:.06em}
  .expo .track{height:9px;background:#18202b;border-radius:6px;margin-top:8px;overflow:hidden}
  .expo .track i{display:block;height:100%;border-radius:6px;
                 background:linear-gradient(90deg,#3fb950,#d29922 60%,#f85149)}

  .cols{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:22px;margin-top:4px}
  @media(max-width:1000px){.cols{grid-template-columns:1fr}}
  .col{min-width:0}

  .hd{display:flex;align-items:center;gap:10px;margin:20px 0 10px}
  .hd h3{margin:0;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#8494a8}
  .hd .count{font-size:10.5px;color:#59677a;background:#131c26;border:1px solid #1e2733;
             border-radius:999px;padding:1px 8px}
  .hd:after{content:"";flex:1;height:1px;background:#1e2733}

  .pgrid{display:grid;gap:9px}
  .pos{background:#0f1721;border:1px solid #1d2836;border-radius:11px;padding:10px 13px;
       display:grid;grid-template-columns:1fr auto;gap:3px 14px;align-items:center;
       transition:.12s}
  .pos:hover{border-color:#2f4457;background:#111b26}
  .pos .l1{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:13.5px}
  .pos .px{color:#59677a;font-size:10.5px;font-family:var(--mono)}
  .pos .gauge{grid-column:1/-1;height:5px;background:#18202b;border-radius:4px;position:relative;margin-top:4px}
  .pos .gauge i{position:absolute;top:0;bottom:0;left:50%;border-radius:4px}
  .pos .why{grid-column:1/-1;font-size:11px;color:#8a99ad;margin-top:3px}

  .trow{display:grid;grid-template-columns:minmax(90px,1fr) 52px minmax(70px,1.1fr) 76px 44px;
        gap:9px;align-items:center;padding:7px 11px;border-radius:8px;
        font-family:var(--mono);font-size:11.5px}
  .trow:nth-child(odd){background:#0f1721}
  .reason{font-size:10px;padding:1px 7px;border-radius:999px;background:#131c26;
          border:1px solid #1e2733;color:#8494a8;justify-self:start;
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}

  .side{font-size:9.5px;padding:1px 6px;border-radius:4px;letter-spacing:.06em}
  .side.buy{color:#3fb950;background:#0e2416;border:1px solid #1d3a26}
  .side.sell{color:#f85149;background:#240f0f;border:1px solid #3a1d1d}
  .tabs{display:flex;gap:6px}
  .tab2{padding:6px 16px;border-radius:9px;border:1px solid transparent;cursor:pointer;
        font:inherit;font-size:11.5px;letter-spacing:.08em;color:#8494a8;background:transparent}
  .tab2:hover{color:#e8eef6;background:#131c26}
  .tab2.on{color:#e8eef6;background:#16202b;border-color:#2a3a4a}
  #market{display:none}
  #market.on{display:block}
  #why{display:none}
  #why.on{display:block}
  #review{display:none}
  #review.on{display:block}
  #control{display:none}
  #control.on{display:block}
  #lb{display:none}
  #lb.on{display:block}
  #opt{display:none}
  #opt.on{display:block}
  #models{display:none}
  #models.on{display:block}
  #tape{display:none}
  #tape.on{display:block}
  #signals{display:none}
  #signals.on{display:block}
  #advisor{display:none}
  #advisor.on{display:block}
  #forecast{display:none}
  #forecast.on{display:block}
  #health{display:none}
  #books{display:none}
  #books.on{display:block}
  #health.on{display:block}
  #busview{display:none}
  #busview.on{display:block}
  #thinking{display:none}
  #thinking.on{display:block}
  #moversview{display:none}
  #moversview.on{display:block}
  #marketlive{display:none}
  #marketlive.on{display:block}
  .mlkpis{position:sticky;top:0;z-index:20;display:grid;
    grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;
    background:rgba(10,14,19,.96);backdrop-filter:blur(10px);
    border-bottom:1px solid #1b2632;padding:10px 0 12px;margin:-6px 0 12px}
  .mlkpi{background:#0f1620;border:1px solid #1b2632;border-radius:9px;padding:8px 10px}
  .mlkpi .k{font-size:9px;letter-spacing:.11em;text-transform:uppercase;color:#5f7186}
  .mlkpi .v{font-family:var(--mono);font-size:15px;font-weight:700;margin-top:2px;color:#e6edf6}
    @media(max-width:1200px){.mlkpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
  .podium{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
  .pod{background:#101821;border:1px solid #1d2836;border-radius:12px;padding:14px 16px;
       cursor:pointer;transition:transform .15s,border-color .15s;position:relative;overflow:hidden}
  .pod:hover{transform:translateY(-2px);border-color:#3d5a78}
  .pod.p1{border-color:#4a3d12;background:linear-gradient(160deg,#1a1710,#101821)}
  .pod.p2{border-color:#2b3540;background:linear-gradient(160deg,#161b21,#101821)}
  .pod.p3{border-color:#3a2a1c;background:linear-gradient(160deg,#1b1512,#101821)}
  .pmed{font-size:18px;line-height:1}
  .pname{font-family:var(--mono);font-size:13px;font-weight:700;margin-top:6px}
  .pret{font-family:var(--mono);font-size:22px;font-weight:700;margin-top:6px;letter-spacing:-.02em}
  .psub{font-size:11px;color:#7c8b9d;font-family:var(--mono);margin-top:3px}
  .lbtable{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px}
  .lbtable th.sortable{cursor:pointer;user-select:none}
  .lbtable th.sortable:hover{color:#c9d6e4}
  .lbtable th{text-align:right;color:#59677a;font-size:9px;letter-spacing:.1em;
              text-transform:uppercase;padding:0 10px 8px;white-space:nowrap;font-weight:600}
  .lbtable th:nth-child(2){text-align:left}
  .lbtable td{text-align:right;padding:10px;border-top:1px solid #171f29;white-space:nowrap}
  .lbtable td:nth-child(2){text-align:left}
  .lbtable tr.top1 td{background:#132a1c}
  .lbtable tr.top2 td{background:#16232b}
  .lbtable tr.top3 td{background:#231d16}
  .lbbar{height:6px;border-radius:3px;background:#18202b;overflow:hidden;min-width:90px}
  .lbbar i{display:block;height:100%;border-radius:3px}
  .rank{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;
        border-radius:6px;background:#131c26;color:#8494a8;font-size:11px}
  .rank.gold{background:#3d3410;color:#f0c44b}
  .rank.silver{background:#2b3238;color:#c8d3dd}
  .rank.bronze{background:#3a2a1c;color:#d79a63}
  .cscope{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .cpanel{background:#0f1721;border:1px solid #1d2836;border-radius:14px;padding:4px 18px 16px;
          margin-top:14px}
  .cfoot{display:flex;align-items:center;gap:12px;margin-top:16px;flex-wrap:wrap}
  #dash.off{display:none}
  .mhead{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:16px 0 4px}
  .mhead .sym{font-family:var(--mono);font-size:23px;font-weight:700;color:#58a6ff;letter-spacing:.02em}
  .mhead .px{font-family:var(--mono);font-size:23px;font-variant-numeric:tabular-nums}
  .mhead .chg{font-family:var(--mono);font-size:14px}
  .sympick{background:#0f1721;border:1px solid #2a3a4a;border-radius:8px;color:#e8eef6;
           padding:6px 11px;font:inherit;font-size:12px;font-family:var(--mono);width:130px}
  .chartcard{background:linear-gradient(180deg,#0f1721,#0b1219);border:1px solid #1e2733;
             border-radius:16px;padding:14px 16px 10px;margin-top:12px;
             box-shadow:0 24px 60px -40px #000}
  .chartcard svg{width:100%;height:440px;display:block;touch-action:none;
                 cursor:grab;user-select:none}
  .chartcard svg:active{cursor:grabbing}
  .mlegend{display:flex;gap:16px;flex-wrap:wrap;padding:8px 2px 4px;font-size:11px;color:#8494a8}
  .mlegend span{display:flex;align-items:center;gap:6px}
  .dot2{width:9px;height:9px;border-radius:3px;display:inline-block}
  .mk{font-size:10.5px;color:#59677a;font-family:var(--mono)}
  .chip{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;margin:0 6px 6px 0;
        border-radius:8px;border:1px solid #1e2733;
        background:linear-gradient(180deg,#111a23,#0e161e);color:#8494a8;
        font:inherit;font-size:11px;letter-spacing:.02em;cursor:pointer;
        transition:color .16s,border-color .16s,background .16s,box-shadow .2s,transform .12s}
  .chip:hover{color:#e8eef6;border-color:#35506a;transform:translateY(-1px)}
  .chip.on{color:#dbe9f7;background:linear-gradient(180deg,#182a3b,#132231);
           border-color:#3d6f96;box-shadow:0 0 0 1px #58a6ff22,0 4px 14px -8px #58a6ff88}
  .chip.on:before{content:"";width:5px;height:5px;border-radius:50%;background:#58a6ff;
                  box-shadow:0 0 6px #58a6ff}
  .chip.armed{color:#0d1117;background:linear-gradient(180deg,#f0b429,#d29922);
              border-color:#f0b429;font-weight:600}
  .params{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;
          background:#0f1721;border:1px solid #1d2836;border-radius:11px;padding:12px 14px}
  .params label{display:flex;flex-direction:column;gap:4px;font-size:10px;
                letter-spacing:.1em;text-transform:uppercase;color:#59677a}
  .params input{width:92px;background:#0a1017;color:#e8eef6;border:1px solid #24313e;
                border-radius:7px;padding:5px 8px;font-family:var(--mono);font-size:12px}
  .params input:focus{outline:none;border-color:#3d5470}
  .setpage{background:#0f1721;border:1px solid #1d2836;border-radius:12px;padding:4px 16px 14px}
  .sgroup{border-bottom:1px solid #182230;padding:13px 0}
  .sgroup:last-of-type{border-bottom:none}
  .sgt{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#58a6ff;margin-bottom:9px}
  .sfields{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:11px}
  .sf{display:flex;flex-direction:column;gap:3px}
  .sf span{font-size:10.5px;color:#8494a8;letter-spacing:.03em}
  .sf input{background:#0a1017;border:1px solid #24313e;border-radius:7px;color:#e8eef6;
            padding:6px 9px;font-family:var(--mono);font-size:12.5px}
  .sf input:focus{outline:none;border-color:#3d5470;box-shadow:0 0 0 3px #58a6ff22}
  .sf input:disabled{opacity:.35;cursor:not-allowed}
  .sf em{font-size:9.5px;color:#4c5a6c;font-style:normal;line-height:1.3}
  .sapply{display:flex;align-items:center;gap:12px;padding-top:13px}
  .applybtn{background:linear-gradient(180deg,#1f6feb,#1a5fd0);border:1px solid #2f81f7;
            color:#fff;border-radius:8px;padding:8px 18px;font:inherit;font-size:12px;
            font-weight:600;cursor:pointer}
  .applybtn:hover{filter:brightness(1.12)}
  .mlayout{display:grid;grid-template-columns:246px minmax(0,1fr);gap:14px;margin-top:12px;
           transition:grid-template-columns .25s ease}
  .mlayout.collapsed{grid-template-columns:0px minmax(0,1fr);gap:0}
  .mlayout.collapsed .mside{display:none}
  @media(max-width:900px){.mlayout{grid-template-columns:1fr}}
  .mside{background:#0d151e;border:1px solid #1b2632;border-radius:14px;padding:12px;
         display:flex;flex-direction:column;min-height:420px;max-height:560px}
  .msearch{width:100%;background:#0a1017;border:1px solid #24313e;border-radius:9px;
           color:#e8eef6;padding:9px 11px;font:inherit;font-size:12px;
           font-family:var(--mono);letter-spacing:.04em}
  .msearch:focus{outline:none;border-color:#3d5470;box-shadow:0 0 0 3px #58a6ff22}
  .mlist{overflow:auto;margin-top:9px;display:flex;flex-direction:column;gap:2px}
  .mrow{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;
        padding:7px 9px;border-radius:8px;cursor:pointer;font-family:var(--mono);font-size:12px;
        color:#9fb0c4;border:1px solid transparent}
  .mrow:hover{background:#131e29;color:#e8eef6}
  .mrow.on{background:#16232f;border-color:#31506b;color:#e8eef6}
  .mrow .n{font-size:10px;color:#59677a}
  .mrow .dots{display:flex;gap:3px}
  .sdot{width:6px;height:6px;border-radius:2px}
  .mempty{color:#4c5a6c;font-size:11px;padding:10px 4px;text-align:center}
  .ctxpill{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border-radius:999px;
           background:#12261a;border:1px solid #2f6b3f;color:#6ee787;font-size:11px;
           letter-spacing:.05em;cursor:pointer;font-family:var(--mono)}
  .ctxpill:hover{background:#16301f}
  .ctxpill b{color:#a6f0bb}
  .gctl{background:linear-gradient(180deg,#101a24,#0c141c);border:1px solid #1d2836;
        border-radius:14px;padding:14px 18px;margin-top:16px}
  .gtitle{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#58a6ff;
          display:flex;align-items:baseline;gap:12px}
  .gsub{font-size:10px;letter-spacing:.06em;color:#4c5a6c;text-transform:none}
  .gfields{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:11px;
           margin-top:12px}
  .gf{display:flex;flex-direction:column;gap:4px}
  .gf span{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#59677a}
  .gf input{background:#0a1017;border:1px solid #24313e;border-radius:7px;color:#e8eef6;
            padding:7px 9px;font-family:var(--mono);font-size:12.5px}
  .gf input:focus{outline:none;border-color:#3d5470;box-shadow:0 0 0 3px #58a6ff22}
  .grow{display:flex;align-items:center;gap:12px;margin-top:13px}
  .ghost{background:#131c26;border:1px solid #2a3a4a;color:#8494a8;border-radius:8px;
         padding:8px 15px;font:inherit;font-size:12px;cursor:pointer}
  .ghost:hover{color:#e8eef6;border-color:#3d5470}
  .gfsel{background:#0a1017;border:1px solid #24313e;border-radius:7px;color:#e8eef6;
         padding:6px 9px;font:inherit;font-size:12px;font-family:var(--mono)}
  .wide{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px;
        font-variant-numeric:tabular-nums}
  .wide th{text-align:right;color:#59677a;font-size:9px;letter-spacing:.1em;
           text-transform:uppercase;font-weight:600;padding:0 9px 6px;white-space:nowrap}
  .wide th:first-child,.wide th:nth-child(2){text-align:left}
  .wide td{text-align:right;padding:7px 9px;border-top:1px solid #171f29;white-space:nowrap}
  .wide td:first-child{text-align:left;color:#58a6ff;font-weight:600}
  .wide td.why2{text-align:left;color:#7d8b9e;font-size:10.5px;white-space:normal;
                max-width:340px;line-height:1.35}
  .wide tbody tr:nth-child(even){background:#0e1620}
  .wide tbody tr:hover{background:#131e29}
  .mini{color:#59677a;font-size:10px}

  /* ---------- card hover: breathing green glow ---------- */
  .card{cursor:pointer;
        transition:border-color .5s ease, box-shadow .5s ease, transform .2s ease}
  .card:hover{border-color:#3fb950 !important;transform:translateY(-2px);
              animation:breathe 2.6s ease-in-out infinite}
  @keyframes breathe{
    0%   {box-shadow:0 0 0 1px #3fb95044, 0 0 12px -8px #3fb95055}
    45%  {box-shadow:0 0 0 1px #3fb950bb, 0 0 34px -2px #3fb950dd}
    100% {box-shadow:0 0 0 1px #3fb95044, 0 0 12px -8px #3fb95055}
  }
  .ctxpill{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border-radius:999px;
           background:#12261a;border:1px solid #2f6b3f;color:#6ee787;font-size:11px;
           letter-spacing:.05em;cursor:pointer;font-family:var(--mono)}
  .ctxpill:hover{background:#16301f}
  .ctxpill b{color:#a6f0bb}

  /* ---------- clickable tickers ---------- */
  .tick{display:inline-block;cursor:pointer;padding:1px 7px;margin:-1px -7px;
        border-radius:6px;border:1px solid transparent;
        text-decoration:underline dotted;text-underline-offset:2px;
        transition:background .2s ease,border-color .2s ease,color .2s ease,
                   box-shadow .25s ease,text-decoration-color .2s ease}
  .tick:hover{background:#12222f;border-color:#3d5470;color:#9ecbff;
              text-decoration-color:transparent;
              box-shadow:0 0 0 1px #58a6ff33, 0 0 16px -6px #58a6ffbb}
  .tick:active{background:#16303f;border-color:#58a6ff}
  .tick:hover::after{content:" ↗";opacity:.7;font-size:.85em}
  .trl .tick,.wide .tick{font-weight:600}

  .panel{background:#0f1721;border:1px solid #1d2836;border-radius:12px;padding:14px 16px}
  .dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
  .dna{background:#0f1721;border:1px solid #1d2836;border-radius:12px;padding:12px 14px}
  .dhead{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:12.5px}
  .dfacts{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:10.5px;color:#8494a8;
          font-family:var(--mono)}
  .dfacts b{color:#e8eef6}
  .dwhy{margin-top:7px;font-size:11px;color:#8a99ad}
  .attr{margin-top:9px;display:flex;flex-direction:column;gap:5px}
  .arow{display:grid;grid-template-columns:130px 1fr 48px;gap:8px;align-items:center;
        font-family:var(--mono);font-size:10.5px;color:#8494a8}
  .abar{height:7px;background:#18202b;border-radius:4px;overflow:hidden}
  .abar i{display:block;height:100%;border-radius:4px}
  .aval{text-align:right}
  .rrow{display:grid;grid-template-columns:110px 1fr;gap:10px;align-items:center;margin-bottom:4px}
  .rlab{font-family:var(--mono);font-size:10.5px;color:#8494a8;text-align:right}
  .rcells{display:grid;grid-auto-flow:column;gap:2px;height:16px}
  .rcells i{display:block;height:16px;border-radius:2px;min-width:3px}
  .skrow{display:grid;grid-template-columns:110px 1fr 80px 90px;gap:10px;align-items:center;
         margin-bottom:7px;font-family:var(--mono);font-size:11px}
  .sklab{color:#8494a8;text-align:right}
  .skbar{height:9px;background:#18202b;border-radius:5px;overflow:hidden}
  .skbar i{display:block;height:100%;border-radius:5px}
  .skval{text-align:right}
  .skmeta{color:#59677a;font-size:10px;text-align:right}
  .fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
  .fcard{min-width:0}
  .fhead{font-size:11px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px;
         font-weight:700}

  .rcard{background:#0f1721;border:1px solid #1d2836;border-radius:14px;padding:16px 18px;
         margin-top:16px}
  .rchead{display:flex;align-items:baseline;gap:12px;font-size:13px;font-weight:700;
          letter-spacing:.08em;text-transform:uppercase}
  .score{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;
         margin:12px 0}
  .sbox{background:#0b131b;border:1px solid #1a2531;border-radius:10px;padding:9px 12px}
  .sbox b{display:block;font-family:var(--mono);font-size:17px;color:#e8eef6}
  .sbox span{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#59677a}
  .rcols{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}
  .ccol{min-width:0}
  .ctitle{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#58a6ff;
          margin-bottom:8px}
  .wgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:11px;
         margin-top:8px}

  #market{position:relative}
  .popover{display:none;position:absolute;right:0;top:52px;z-index:45;width:min(700px,96vw);
           background:linear-gradient(180deg,#101923,#0b1219);
           border:1px solid #22303d;border-radius:16px;
           box-shadow:0 30px 70px -30px #000,0 0 0 1px #ffffff08 inset}
  .popover.open{display:block;animation:rise .16s ease-out}
  .phead{display:flex;align-items:center;gap:10px;padding:13px 18px 11px;
         border-bottom:1px solid #1b2836}
  .phead b{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#8494a8}
  .pbody{padding:14px 18px 6px}
  .pfoot{padding:10px 18px 14px;border-top:1px solid #1b2836;font-size:10.5px;
         color:#4c5a6c;line-height:1.7}
  .pfoot b{color:#7d8b9e;font-weight:600}
  .popover.open{display:block}
  .pgrid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
  .pgrp{min-width:0}
  .prow{display:grid;grid-template-columns:96px 1fr;gap:14px;align-items:start;
        padding:10px 0;border-bottom:1px dashed #17222e}
  .prow:last-child{border-bottom:none}
  .plab{font-size:9.5px;letter-spacing:.15em;text-transform:uppercase;color:#59677a;
        padding-top:7px}
  .pctl{min-width:0}
  .pbody{max-height:min(70vh,560px);overflow:auto}
  .pglabel{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:#59677a;
           margin-bottom:7px}

  #chartmodal{position:fixed;inset:0;background:#04070bd9;backdrop-filter:blur(4px);
              display:none;z-index:70;padding:18px;overflow:auto}
  #chartmodal.open{display:block}
  .cmsheet{max-width:1500px;margin:0 auto;background:#0b1119;border:1px solid #24313e;
           border-radius:14px;overflow:hidden;box-shadow:0 40px 100px -40px #000}
  .cmhead{display:flex;align-items:center;gap:12px;padding:9px 14px;background:#101923;
          border-bottom:1px solid #1b2836}
  .cmhead b{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#8494a8}
  :root{--gap:14px;--pad:14px 16px;--r:10px}
  html,body{overflow-x:hidden;max-width:100%}
  .wrap{padding:18px 22px 40px;max-width:1680px;overflow-x:hidden}
  .panel{overflow-x:auto}
  .panel{background:#0f1620;border:1px solid #1b2632;border-radius:var(--r);padding:var(--pad);margin-bottom:var(--gap)}
  .mhead{display:flex;align-items:center;gap:10px;margin:-2px 0 10px}
  .mhead .sym{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#8fa3b8;font-weight:700}
  .ctitle{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#6b7c90;margin-bottom:8px;font-weight:600}
  .dashgrid{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--gap);align-items:start;margin-bottom:var(--gap)}
  @media(max-width:1200px){.dashgrid{grid-template-columns:minmax(0,1fr)}}
  .totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:var(--gap)}
  .kpi{background:#0f1620;border:1px solid #1b2632;border-radius:var(--r);padding:12px 14px}
  .kpi .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#5f7186}
  .kpi .v{font-family:var(--mono);font-size:19px;font-weight:700;margin-top:4px;color:#e6edf6}
  .wide{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px}
  .wide th{text-align:right;font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#59677a;padding:0 8px 8px;font-weight:600;white-space:nowrap}
  .wide td{padding:6px 8px;border-top:1px solid #16202b;vertical-align:middle}
  .wide tbody tr:hover{background:#131d28}
  .lbbar{height:5px;border-radius:3px;background:#16202b;overflow:hidden;display:block}
  .lbbar i{display:block;height:100%}
.mblock{display:flex;gap:4px;align-items:flex-end;overflow:hidden;width:186px}
.mcell{display:flex;flex-direction:column;align-items:center;gap:1px;width:34px;flex:0 0 34px}
.mlab{font-size:6.5px;color:#3d4d5f;letter-spacing:.06em}
.advrow{display:grid;grid-template-columns:82px 186px minmax(0,1fr) 44px;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid #131c26}
.advrow>span.reason{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#7c8b9d}
.advrow>span.score{text-align:right}
.taperow{display:grid;grid-template-columns:54px 58px 64px 46px 96px minmax(0,1fr);gap:6px;align-items:center;padding:3px 0;border-bottom:1px solid #131c26}
.taperow>span.reason{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#7c8b9d}
@media(max-width:1400px){.advrow{grid-template-columns:80px minmax(0,1fr) 50px}.advrow .mblock{display:none}.taperow{grid-template-columns:52px 56px minmax(0,1fr) 46px 76px}}
@keyframes tickflash{0%{background:#123a24;box-shadow:inset 3px 0 0 #3fb950}70%{background:#0f2418}100%{background:transparent;box-shadow:none}}
@keyframes tickdown{0%{background:#3a1616;box-shadow:inset 3px 0 0 #f85149}70%{background:#241010}100%{background:transparent;box-shadow:none}}
tr.row-up{animation:tickflash 1.4s ease-out}
tr.row-down{animation:tickdown 1.4s ease-out}
.prevpx{display:block;font-size:9.5px;color:#3d4d5f}
.frow{display:grid;grid-template-columns:92px 46px 96px 46px 62px 74px minmax(120px,1fr);gap:8px;align-items:center;
      padding:5px 0;border-bottom:1px solid #131c26;font-family:var(--mono);font-size:11.5px}
.frow .agents{display:flex;gap:3px}
.frow .agents i{width:9px;height:9px;border-radius:2px;display:block}
.facc{display:grid;grid-template-columns:120px 130px 1fr;gap:10px;align-items:center;
      padding:3px 0;font-family:var(--mono);font-size:11.5px;color:#9fb0c4}
#chartbox{position:fixed;inset:0;background:#04070be6;backdrop-filter:blur(4px);
  display:none;z-index:120;padding:18px;overflow:auto}
#chartbox.open{display:block}
#chartbox .sheet{max-width:1400px;margin:0 auto;background:#0b1119;border:1px solid #24313e;
  border-radius:12px;padding:16px}
table th.sortable{cursor:pointer;user-select:none;position:relative}
table th.sortable:hover{color:#c9d6e3}
table th.sort-asc:after{content:" \25B2";font-size:8px;color:#58a6ff}
table th.sort-desc:after{content:" \25BC";font-size:8px;color:#58a6ff}
.pbar{height:8px;background:#16202b;border-radius:4px;overflow:hidden;min-width:110px}
.pbar i{display:block;height:100%;transition:width .3s ease}
.extrow{display:grid;grid-template-columns:96px 200px 62px 62px 56px 62px 62px 58px;gap:10px;
  align-items:center;font-family:var(--mono);font-size:11px;padding:3px 0;border-bottom:1px solid #131c26}
.extrow b{cursor:pointer;color:#c9d6e3}
.ebar{height:5px;background:#16202b;border-radius:3px;overflow:hidden;display:flex}
.ebar i{display:block;height:100%}
.flowrow{display:grid;grid-template-columns:96px 240px 86px 56px 130px;gap:10px;align-items:center;
  font-family:var(--mono);font-size:11px;padding:3px 0;border-bottom:1px solid #131c26}
.flowrow b{cursor:pointer;color:#c9d6e3}
.fbar{height:5px;background:#16202b;border-radius:3px;overflow:hidden;display:flex}
.fbar i{display:block;height:100%}
.mvwrap{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:8px 0 14px}
@media(max-width:1200px){.mvwrap{grid-template-columns:minmax(0,1fr)}}
.mvcard{background:#0f1620;border:1px solid #1b2632;border-radius:10px;padding:10px 12px}
.mvcard h4{margin:0 0 8px;font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#6b7c90}
.mvrow{display:grid;grid-template-columns:86px 1fr 74px 62px;gap:8px;align-items:center;
  font-family:var(--mono);font-size:11px;padding:3px 0;border-bottom:1px solid #131c26}
.mvrow b{cursor:pointer;color:#c9d6e3}
.mvbar{height:4px;background:#16202b;border-radius:2px;overflow:hidden}
.mvbar i{display:block;height:100%}
.mlscroll{max-height:calc(100vh - 300px);overflow:auto;border-radius:8px}
.mltable{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px}
.mltable thead th{position:sticky;top:0;z-index:6;background:#0b1016;text-align:right;
  font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#59677a;font-weight:600;
  padding:7px 10px;border-bottom:1px solid #1b2632;white-space:nowrap}
.mltable thead th:first-child{text-align:left}
.mltable td{padding:5px 10px;border-bottom:1px solid #121a24;vertical-align:middle}
.mltable tbody tr:nth-child(odd){background:#0a0f16}
.mltable th:nth-child(1),.mltable td:nth-child(1){width:112px}
.mltable th:nth-child(2),.mltable td:nth-child(2){width:118px}
.mltable th:nth-child(3),.mltable td:nth-child(3){width:124px}
.mltable th:nth-child(4),.mltable td:nth-child(4){width:86px}
.mltable th:nth-child(5),.mltable td:nth-child(5){width:118px}
.mltable th:nth-child(6),.mltable td:nth-child(6){width:92px}
.mltable th:nth-child(7),.mltable td:nth-child(7){width:84px}
.mltable tbody tr:hover{background:#131d28}
.mltable .sym{font-weight:700;color:#c9d6e3;cursor:pointer;letter-spacing:.02em}
.mltable .px{text-align:right;font-weight:600;color:#e6edf6;position:relative;
  font-variant-numeric:tabular-nums;
  transition:background-color .85s cubic-bezier(.22,.61,.36,1),color .85s ease-out;
  border-radius:3px;padding-left:4px;padding-right:4px}
.mltable .chg{text-align:right;font-weight:600}
.mltable .liq{text-align:right;color:#7c8b9d}
.mltable .vol{text-align:right;color:#58a6ff}
.mltable .age{text-align:right;font-size:10px;color:#59677a}
.mltable .spark{width:96px}
.px.up-tint{background-color:#123a24;color:#7ee787}
.px.down-tint{background-color:#3a1616;color:#ff9a92}
.liqbar{height:3px;background:#16202b;border-radius:2px;margin-top:3px;overflow:hidden}
.liqbar i{display:block;height:100%;background:#3d6a94}
#rail{position:fixed;left:0;top:0;bottom:0;width:186px;background:linear-gradient(180deg,#0c1219,#0a0e13);
 border-right:1px solid #1c2836;display:flex;flex-direction:column;z-index:50}
#rail-btn{}
.rlogo{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;font-family:var(--mono);font-size:13px;letter-spacing:.16em;
 text-transform:uppercase;color:#8fa3b8;border-bottom:1px solid #16202b}
.rlogo span{color:#3fb950}
#rail nav{flex:1;padding:10px 8px;display:flex;flex-direction:column;gap:2px;overflow:auto}
.nav{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;cursor:pointer;
 color:#7c8b9d;font-size:12.5px;font-family:var(--mono);transition:.12s;user-select:none}
.nav i{font-style:normal;width:14px;text-align:center;color:#4d5d70}
.nav:hover{background:#131d28;color:#c9d6e3}
.nav.on{background:#16232f;color:#e6edf6;box-shadow:inset 2px 0 0 #3fb950}
.nav.on i{color:#3fb950}
.rfoot{padding:12px;border-top:1px solid #16202b;display:flex;justify-content:center}
#rail.collapsed{width:56px}
#rail.collapsed .rlogo b,#rail.collapsed .nav span,#rail.collapsed .rfoot{display:none}
#rail.collapsed .nav{justify-content:center;padding:10px 0}
#rail.collapsed .rlogo{justify-content:center;padding:14px 0}
#railbtn{background:none;border:1px solid #1c2836;color:#8fa3b8;border-radius:6px;
 width:24px;height:24px;cursor:pointer;font-size:12px;line-height:1;padding:0}
#railbtn:hover{border-color:#3d6a94;color:#c9d6e3}
body.rail-min header,body.rail-min .wrap{margin-left:56px}
#ovr,#stale,#err{margin-left:186px;padding-left:14px}
body.rail-min #ovr,body.rail-min #stale,body.rail-min #err{margin-left:56px}
header{margin-left:186px}
.wrap{margin-left:186px}
@media(max-width:900px){#rail{width:56px}#rail .rlogo b,#rail .nav span,#rail .rfoot{display:none}
 #rail .nav{justify-content:center}#rail .rlogo{justify-content:center}
 header,.wrap{margin-left:56px !important}}
</style>
<script>__UI_LIB__</script>
<script>
/* EARLY TAB FALLBACK — runs before the main script so a later failure can never
   make the sidebar unclickable. The full showTab replaces this on load. */
(function(){
  window.__earlyErr = null;
  window.addEventListener("error", function(e){
    var b=document.getElementById("err");
    if(b){ b.style.display="block"; b.textContent="JS error: "+e.message+" @"+e.lineno; }
    window.__earlyErr = e.message;
  });
  var PANELS=["dash","market","why","review","control","lb","opt","models","tape","signals","advisor","forecast","thinking","busview","moversview","marketlive","health"];
  window.showTab = window.showTab || function(which){
    PANELS.forEach(function(k){
      var el=document.getElementById(k); if(!el) return;
      if(k==="dash"){ el.classList.toggle("off", which!=="dash"); }
      else { el.classList.toggle("on", which===k); }
      var nv=document.getElementById("nav-"+k); if(nv) nv.classList.toggle("on", which===k);
    });
    var body=document.getElementById(which+"body");
    if(body && !body.innerHTML.trim()){
      body.innerHTML='<div class="panel"><div class="empty">panel switched, but the renderer did not run — check the red error bar above.</div></div>';
    }
  };
})();
</script></head><body>
<aside id="rail">
  <div class="rlogo"><b>Crypt<span>Alyst</span></b>
  <button id="railbtn" onclick="toggleRail()" title="collapse / expand">☰</button></div>
  <nav>
    <a onclick="showTab('dash')" id="nav-dash" class="nav on"><i>▦</i><span>Dashboard</span></a>
    <a onclick="showTab('thinking')" id="nav-thinking" class="nav"><i>◍</i><span>Thinking</span></a>
    <a onclick="showTab('busview')" id="nav-busview" class="nav"><i>⇄</i><span>Message bus</span></a>
    <a onclick="showTab('tape')" id="nav-tape" class="nav"><i>≡</i><span>Tape</span></a>
    <a onclick="showTab('signals')" id="nav-signals" class="nav"><i>◎</i><span>Signals</span></a>
    <a onclick="showTab('advisor')" id="nav-advisor" class="nav"><i>◉</i><span>Advisor</span></a>
    <a onclick="showTab('forecast')" id="nav-forecast" class="nav"><i>◈</i><span>Forecast</span></a>
    <a onclick="showTab('moversview')" id="nav-moversview" class="nav"><i>↕</i><span>Movers</span></a>
    <a onclick="showTab('marketlive')" id="nav-marketlive" class="nav"><i>◇</i><span>Market live</span></a>
    <a onclick="showTab('market')" id="nav-market" class="nav"><i>◧</i><span>Chart</span></a>
    <a onclick="showTab('lb')" id="nav-lb" class="nav"><i>▲</i><span>Leaderboard</span></a>
    <a onclick="showTab('models')" id="nav-models" class="nav"><i>◈</i><span>Models</span></a>
    <a onclick="showTab('opt')" id="nav-opt" class="nav"><i>⚙</i><span>Optimiser</span></a>
    <a onclick="showTab('why')" id="nav-why" class="nav"><i>?</i><span>Why</span></a>
    <a onclick="showTab('review')" id="nav-review" class="nav"><i>◫</i><span>Review</span></a>
    <a onclick="showTab('control')" id="nav-control" class="nav"><i>⏻</i><span>TA Analysis</span></a>
    <a onclick="showTab('books')" id="nav-books" class="nav"><i>▤</i><span>Books</span></a>
    <a onclick="showTab('health')" id="nav-health" class="nav"><i>✚</i><span>Health</span></a>
  </nav>
  <div class="rfoot" style="flex-direction:column;gap:6px">
    <span id="rail-live" class="pill ok">live</span>
    <span id="rail-books" class="pill">0 books</span>
    <span id="rail-trade" class="pill">idle</span>
  </div>
</aside>
<header><h1>All engines</h1>
  <div class="tabs" style="display:none">
    <button class="tab2 on" id="tab-dash" onclick="showTab('dash')">Dashboard</button>
    <button class="tab2" id="tab-market" onclick="showTab('market')">Market chart</button>
    <button class="tab2" id="tab-why" onclick="showTab('why')">Why</button>
    <button class="tab2" id="tab-review" onclick="showTab('review')">Review</button>
    <button class="tab2" id="tab-control" onclick="showTab('control')">TA Analysis</button>
    <button class="tab2" id="tab-lb" onclick="showTab('lb')">Leaderboard</button>
    <button class="tab2" id="tab-opt" onclick="showTab('opt')">Optimiser</button>
    <button class="tab2" id="tab-models" onclick="showTab('models')">Models</button>
    <button class="tab2" id="tab-tape" onclick="showTab('tape')">Tape</button>
  </div>
  <span class="pill ok" id="live">—</span>
  <span class="pill" id="hz" title="service health">health —</span>
  <span class="pill" id="beat" title="platform heartbeat">beat —</span>
  <span class="pill ok" id="toteq" title="total equity across every book">total —</span>
  <button id="tradebtn" class="tbtn" onclick="toggleTrading()">▶ start trading</button>
  <span class="pill" id="modelsbadge">—</span>
  <span class="spacer"></span>
  <span class="pill" id="clock">—</span>
</header>
<div id="ovr" style="display:none;background:#4a1111;border-bottom:1px solid #f85149;
  color:#ffb3ad;padding:8px 16px;font-size:12px;font-family:ui-monospace,monospace;
  gap:12px;align-items:center">OVERRIDE ACTIVE — every risk, cost and forecast gate is bypassed.
  This is crazy trading. <button onclick="setOpt('override_crazy',false)" style="background:#2a0f0f;
  border:1px solid #f85149;color:#ffd0cc;border-radius:6px;padding:3px 10px;cursor:pointer">stop override</button></div>
<div id="stale" style="display:none;background:#1d2a3a;border-bottom:1px solid #3d6a94;
     color:#9fc7ef;padding:8px 16px;font-size:12px;font-family:ui-monospace,monospace;
     gap:12px;align-items:center">A newer version of this dashboard is running.
     <button onclick="location.reload(true)" style="background:#16232f;border:1px solid #3d6a94;
     color:#cfe4f7;border-radius:6px;padding:3px 10px;cursor:pointer">reload</button></div>
<div id="err" style="display:none;background:#3a1d1d;border-bottom:1px solid #f85149;
     color:#ffb3ad;padding:8px 16px;font-size:12px;font-family:ui-monospace,monospace"></div>
<div class="wrap">
  <div class="totals" id="totals" style="margin:0 -20px 0"></div>
  <div id="dash">
    <div id="attrib" class="panel" style="padding:10px 14px;margin-bottom:0"></div>
    <div class="dashgrid">
    <div class="panel" style="margin-bottom:0">
      <div class="mhead"><span class="sym">advisor</span>
        <span class="mk">what the agent is flagging now</span>
        <span style="flex:1"></span>
        <button class="tbtn" onclick="showTab('advisor')">open advisor</button></div>
      <div id="dashadv" style="font-family:var(--mono);font-size:11.5px"></div>
    </div>

    </div>
    <div id="sections"></div>
  </div>
  <div id="chartmodal" onclick="if(event.target===this)closeChartModal()">
    <div class="cmsheet">
      <div class="cmhead"><b>chart</b><span class="mk" id="cmtitle"></span>
        <span style="flex:1"></span>
        <button class="ghost" onclick="closeChartModal()">close</button></div>
      <iframe id="chartpop" style="width:100%;height:calc(90vh - 46px);border:0;
              border-radius:0 0 14px 14px;display:block;background:#0b0f14"></iframe>
    </div>
  </div>
  <div id="market">
    <iframe id="chartframe" src="/chart"
            style="width:100%;height:calc(100vh - 130px);min-height:520px;border:0;
                   border-radius:12px;display:block;background:#0b0f14"></iframe>
  </div>
  <div id="books">
    <div class="mhead"><span class="sym">Books</span>
      <span class="mk">every model's own account · click a book to open it</span>
      <span style="flex:1"></span><span class="mk" id="booksnote">—</span></div>
    <div id="bookshead"></div>
    <div id="booksgrid"></div>
  </div>
  <div id="health">
    <div class="mhead"><span class="sym">Dashboard health</span>
      <span class="mk">every view and the data it needs</span>
      <span style="flex:1"></span><button class="tbtn" onclick="renderHealth()">recheck</button></div>
    <div class="panel"><div id="healthbody"><div class="hint">checking…</div></div></div>
  </div>
  <div id="busview">
    <div class="mhead"><span class="sym">Message bus</span>
      <span class="mk">RabbitMQ — queues, rates, consumers</span>
      <span style="flex:1"></span>
      <a href="http://127.0.0.1:15672" target="_blank" class="tbtn" style="text-decoration:none">open management UI</a></div>
    <div id="busbody"><div class="hint" style="padding:14px">reading broker…</div></div>
  </div>
  <div id="thinking">
    <div class="mhead"><span class="sym">Thinking</span>
      <span class="mk">what the platform sees, decides, and refuses</span>
      <span style="flex:1"></span><span class="mk" id="thn">—</span></div>
    <div id="thbody"><div class="hint" style="padding:14px">reading the system…</div></div>
  </div>
  <div id="moversview">
    <div class="mhead"><span class="sym">Movers</span>
      <span class="mk">biggest increase · biggest decrease · most traded · money flow</span>
      <span style="flex:1"></span><span class="mk" id="mvn">—</span></div>
    <div id="movers" class="mvwrap"></div>
    <div id="flowmap" class="panel" style="padding:10px 14px;margin-top:12px"></div>
  </div>
  <div id="marketlive">
    <div class="mhead"><span class="sym">Live market</span>
      <span class="mk">deepest books first · ticking every second</span>
      <span style="flex:1"></span>
      <span class="pill" id="mlage">—</span>
      <span class="mk" id="mlcount"></span></div>
    <div class="panel" style="padding:0">
      <div id="mlbody"><div class="hint" style="padding:14px">loading market…</div></div>
    </div>
  </div>
  <div id="forecast">
    <div class="mhead"><span class="sym">Forecast supervisor</span>
      <span class="mk">seven agents blended by measured accuracy</span>
      <span style="flex:1"></span><span class="mk" id="fcnote">—</span></div>
    <div class="panel" style="margin-bottom:12px">
      <div class="ctitle">agent accuracy (learned from closed trades)</div>
      <div id="fcstate"></div>
    </div>
    <div class="panel">
      <div class="ctitle">live forecasts for held and flagged symbols</div>
      <div id="fcbody"><div class="hint">loading…</div></div>
    </div>
  </div>
  <div id="advisor">
    <div class="mhead"><span class="sym">Agent Advisor</span>
      <span class="mk">live reasoning of the market information agent</span>
      <span style="flex:1"></span><span class="mk" id="advnote">—</span></div>
    <div class="panel" style="margin-bottom:12px">
      <div class="ctitle">thinking</div>
      <div id="advagents" style="margin:10px 0"></div>
      <div id="advthoughts" style="max-height:260px;overflow:auto"></div>
    </div>
    <div class="panel">
      <div class="ctitle">board the models are given</div>
      <div id="advboard"></div>
    </div>
  </div>
  <div id="signals">
    <div class="mhead"><span class="sym">Live signals</span>
      <span class="mk">most liquid first · alerts · model votes</span>
      <span style="flex:1"></span><span class="mk" id="signote">—</span></div>
    <div class="panel" style="margin-bottom:12px">
      <div class="ctitle">market agent · opportunity board <span class="mk" id="oppn"></span></div>
      <div id="oppbox"><div class="hint">loading…</div></div>
    </div>
    <div class="panel" style="margin-bottom:12px">
      <div class="ctitle">awaiting 10s confirmation</div>
      <div id="pendingbox"><div class="hint">loading…</div></div>
    </div>
    <div id="signalsbody"></div>
  </div>
  <div id="tape">
    <div class="mhead"><span class="sym">Fill tape</span>
      <span class="mk">every entry and exit, newest first</span>
      <span style="flex:1"></span>
      <select id="tfilt" onchange="renderTape()" style="background:#0d141c;color:#c9d6e3;border:1px solid #1c2836;border-radius:6px;padding:4px 8px;font:inherit;font-size:11px">
        <option value="all">all engines</option>
      </select>
      <select id="tkind" onchange="renderTape()" style="background:#0d141c;color:#c9d6e3;border:1px solid #1c2836;border-radius:6px;padding:4px 8px;font:inherit;font-size:11px">
        <option value="all">entries+exits</option>
        <option value="open">entries only</option>
        <option value="close">exits only</option>
      </select>
      <input id="tsearch" placeholder="search symbol, engine, reason…" oninput="renderTape()"
        style="background:#0d141c;color:#c9d6e3;border:1px solid #1c2836;border-radius:6px;
        padding:4px 10px;font:inherit;font-size:11px;min-width:210px">
      <span class="mk" id="tapenf"></span></div>
    <div id="tapebody"></div>
  </div>
  <div id="models">
    <div id="revcount" class="panel" style="padding:9px 14px;margin-bottom:10px"></div>
    <div class="mhead"><span class="sym">All models</span>
      <span class="mk">every constituent model, not just the books</span>
      <span style="flex:1"></span><span class="mk" id="mn"></span></div>
    <div id="modelsbody"></div>
  </div>
  <div id="opt">
    <div class="mhead"><span class="sym">Optimiser</span>
      <span class="mk">measures outcomes, adjusts bounded parameters</span>
      <span style="flex:1"></span><span class="mk" id="optn"></span></div>
    <div id="optbody"></div>
  </div>
  <div id="lb">
    <div class="mhead"><span class="sym">Leaderboard</span>
      <span class="mk">ranked by realised P&amp;L</span>
      <span style="flex:1"></span><span class="mk" id="lbn"></span></div>
    <div id="lbbody"></div>
  </div>
  <div id="control">
<div id="optpanel" class="panel" style="margin-bottom:12px">
    <div class="ctitle">Trading options — live, no restart needed</div>
    <div id="optsbody" style="font-family:var(--mono);font-size:12px"></div>
    <div class="mhead"><span class="sym">TA Analysis</span>
      <span class="mk">one panel · every property · every model</span>
      <span style="flex:1"></span><span class="mk" id="cstat"></span></div>
    <div class="cscope" id="cscope"></div>
    <div class="gctl">
      <div class="gtitle">Global controls
        <span class="gsub">one click sets every model</span></div>
      <div class="gfields">
        <label class="gf"><span>risk / trade %</span>
          <input id="g-risk" type="number" step="0.1" min="0.05" max="15" placeholder="—"></label>
        <label class="gf"><span>take profit %</span>
          <input id="g-tp" type="number" step="0.1" min="0.2" max="10" placeholder="—"></label>
        <label class="gf"><span>stop %</span>
          <input id="g-sl" type="number" step="0.1" min="0.2" max="8" placeholder="—"></label>
        <label class="gf"><span>max positions</span>
          <input id="g-max" type="number" step="1" min="1" max="40" placeholder="—"></label>
        <label class="gf"><span>book risk ceiling %</span>
          <input id="g-ceil" type="number" step="1" min="5" max="100" placeholder="—"></label>
        <label class="gf"><span>scan cadence s</span>
          <input id="g-scan" type="number" step="0.5" min="0.5" max="600" placeholder="—"></label>
        <label class="gf"><span>trail ratio</span>
          <input id="g-trail" type="number" step="0.05" min="0" max="1" placeholder="—"></label>
        <label class="gf"><span>max hold s</span>
          <input id="g-hold" type="number" step="10" min="0" max="86400" placeholder="—"></label>
        <label class="gf"><span>min liquidity $</span>
          <input id="g-liq" type="number" step="1000000" min="0" max="5000000000" placeholder="—"></label>
        <label class="gf"><span>hold (turtles) s</span>
          <input id="g-hs" type="number" step="10" min="5" max="3600" placeholder="—"></label>
      </div>
      <div class="grow" style="margin-top:10px">
        <span class="gsub" style="margin-right:4px">chart default</span>
        <select id="g-iv" class="gfsel" onchange="setDefaultIv(this.value)">
          <option value="1m">1m</option><option value="5m" selected>5m</option>
          <option value="15m">15m</option><option value="1h">1h</option>
          <option value="4h">4h</option><option value="1d">1d</option>
        </select>
        <span class="mk">applies to every chart you open</span>
      </div>
      <div class="grow">
        <button class="applybtn" onclick="applyAll()">Apply to all models</button>
        <button class="ghost" onclick="forceAll()">Force rescan all</button>
        <span class="mk" id="gstat"></span>
      </div>
    </div>
    <div id="cbody"></div>
  </div>
  <div id="review">
    <div class="mhead"><span class="sym">Model review</span>
      <span class="mk">process, not just P&amp;L</span>
      <span style="flex:1"></span><span class="mk" id="rnote"></span></div>
    <div id="rbody"></div>
  </div>
  <div id="why">
    <div class="mhead">
      <span class="sym" id="wsym">—</span>
      <span class="mk">decision explainability</span>
      <span style="flex:1"></span>
      <span class="mk" id="wnote"></span>
    </div>
    <div id="wbody"></div>
    <div class="section">
      <h2>Signal source — model aggregate</h2>
      <div class="card" id="modelspanel" style="max-height:none">
        <div class="empty">loading…</div>
      </div>
    </div>
  </div>
</div>
<div id="chartbox" onclick="if(event.target===this)closeChart()">
  <div class="sheet">
    <span class="close" onclick="closeChart()">×</span>
    <h2 id="charttitle" style="color:#58a6ff">—</h2>
    <div class="label" id="chartsub">live market</div>
    <div class="ivs" id="ivs"></div>
    <div id="chartctl" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0 8px">
      <button class="tbtn" onclick="zoomStep(0.6)" title="zoom in (+)">zoom +</button>
      <button class="tbtn" onclick="zoomStep(1.7)" title="zoom out (-)">zoom −</button>
      <button class="tbtn" onclick="resetView()" title="reset zoom (0)">reset</button>
      <button class="tbtn" onclick="resetY()" title="reset price axis (y)">reset price</button>
      <span class="mk" style="margin-left:6px">drag to pan · shift+drag to select a range · wheel zooms · double-click resets</span>
    </div>
    <svg id="chartsvg" viewBox="0 0 1000 360" preserveAspectRatio="none"
         style="width:100%;height:360px;display:block;touch-action:none;cursor:crosshair"></svg>
    <div class="label" id="chartfoot" style="margin-top:8px">—</div>
  </div>
</div>
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="sheet" id="sheet"></div>
</div>
</div>
<footer id="foot">—</footer>
<script>
const $=i=>document.getElementById(i);
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const pct=v=>(v==null?"—":(v>=0?"+":"")+Number(v).toFixed(3)+"%");
const money=v=>(v==null?"—":Number(v).toLocaleString(undefined,
  {minimumFractionDigits:2,maximumFractionDigits:2}));
const cls=v=>(v==null?"flat":(v>0?"up":v<0?"down":"flat"));
let LAST=null;
const PREV={};
let MKT=new Map();
let TICKERS=[];


/* ============ tabs ============ */
function renderControl(){
  var ctl=(LAST||[]);
  if(!$("cbody")) return;
  $("cbody").innerHTML = ctl.map(function(e){
    var st=e.settings||{};
    var keys=Object.keys(st);
    if(!keys.length) return "";
    return '<div class="panel" style="margin-top:12px">'+
      '<div class="rchead"><span style="color:'+e.colour+'">'+esc(e.key)+'</span>'+
      '<span style="margin-left:6px">'+engSpark(e.key,64,15)+'</span>'+
      '<span class="mk" style="margin-left:8px">'+engSpark(e.key,70,15)+'</span>'+
      '<label style="margin-left:auto;display:flex;align-items:center;gap:7px;font-size:11px;color:#8494a8;cursor:pointer">'+
        '<input type="checkbox" '+(st.enabled===0?'':'checked')+
        ' onchange="setTrading(\''+esc(e.key)+'\',this.checked)" style="width:14px;height:14px;accent-color:#3fb950">'+
        'trading</label></div>'+
      '<div class="setpage" style="margin-top:8px"><div class="sgroup"><div class="sfields">'+
      keys.map(function(k){
        return '<label class="sf"><span>'+esc(k.replace(/_/g," "))+'</span>'+
               '<input id="ctl-'+esc(e.key)+'-'+esc(k)+'" value="'+esc(st[k])+'"></label>';
      }).join("")+
      '</div></div><div class="sapply">'+
      '<button class="applybtn" onclick="applyEngineSettings(\''+esc(e.key)+'\')">apply</button>'+
      '<span class="mk" id="cstat-'+esc(e.key)+'"></span></div></div></div>';
  }).join("") || '<div class="panel"><div class="empty">no engines reporting settings</div></div>';
}
async function setTrading(key, on){
  try{
    var r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({enabled: on?1:0})});
    var d=await r.json();
    var st=$("cstat-"+key);
    if(st) st.textContent = r.ok ? (on?"▶ trading enabled":"⏸ entries paused") : ("rejected: "+(d.error||r.status));
    tick();
  }catch(e){}
}
async function applyEngineSettings(key){
  var e=(LAST||[]).find(function(x){ return x.key===key; });
  if(!e) return;
  var body={};
  Object.keys(e.settings||{}).forEach(function(k){
    var el=$("ctl-"+key+"-"+k);
    if(el && el.value!=="" && !isNaN(parseFloat(el.value))) body[k]=parseFloat(el.value);
  });
  var st=$("cstat-"+key);
  try{
    var r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    var d=await r.json();
    if(st) st.textContent = r.ok ? ("✓ applied "+d.applied_count+" rules") : ("rejected: "+(d.error||r.status));
  }catch(err){ if(st) st.textContent="failed"; }
}
var RAIL_MIN = (localStorage.getItem("railMin")==="1");
function applyRail(){
  var r=document.getElementById("rail"), b=document.getElementById("railbtn");
  if(!r) return;
  r.classList.toggle("collapsed",RAIL_MIN);
  document.body.classList.toggle("rail-min",RAIL_MIN);
  if(b) b.textContent=RAIL_MIN?"\u25b8":"\u2630";
}
function toggleRail(){
  RAIL_MIN=!RAIL_MIN;
  localStorage.setItem("railMin",RAIL_MIN?"1":"0");
  applyRail();
}
applyRail();
async function renderBus(){
  var box=$("busbody"); if(!box) return;
  var d={};
  try{ d=await (await fetch("/api/bus",{cache:"no-store"})).json(); }catch(e){
    box.innerHTML=UI.panel("message bus", '<div class="empty">broker unreachable</div>'); return; }
  if(d.error){ box.innerHTML=UI.panel("message bus", '<div class="empty">'+UI.esc(d.error)+'</div>'); return; }

  var t=d.totals||{};
  var qs=(d.queues||[]).filter(function(q){ return q.name.indexOf("stomp-")!==0; });
  function sum(f){ return qs.reduce(function(a,q){ return a+(f(q)||0); },0); }
  var inRate=sum(function(q){ return q.publish_rate; });
  var outRate=sum(function(q){ return q.deliver_rate; });
  var backlog=sum(function(q){ return q.messages; });
  var stalled=qs.filter(function(q){ return !q.consumers; });
  var legacy=qs.filter(function(q){ return q.name.indexOf("q.")===0 ? false : true; });

  var stats=[UI.stat("broker","RabbitMQ "+UI.esc(d.version||""),"#a371f7"),
             UI.stat("queues", qs.length),
             UI.stat("in/s", inRate.toFixed(1), inRate?"#3fb950":"#f85149"),
             UI.stat("out/s", outRate.toFixed(1), outRate?"#3fb950":"#f85149"),
             UI.stat("backlog", backlog, backlog>1000?"#f85149":"#9fb0c4"),
             UI.stat("unconsumed", stalled.length, stalled.length?"#f85149":"#3fb950")].join("");

  var rows=qs.slice().sort(function(a,b){ return (b.publish_rate||0)-(a.publish_rate||0); }).map(function(q){
    var pin=q.publish_rate||0, pout=q.deliver_rate||0;
    var fam=q.name.indexOf("q.")===0?"topic":"legacy";
    return [UI.esc(q.name), fam, (q.messages||0),
            pin?pin.toFixed(2):"—", pout?pout.toFixed(2):"—",
            q.consumers?UI.pill(String(q.consumers),"ok"):UI.pill("none","bad")];
  });

  box.innerHTML = UI.panel("broker", '<div style="display:flex;gap:26px;flex-wrap:wrap">'+stats+'</div>',
      {note:"management :15672 · in/s is publish, out/s is delivery"})+
    '<div style="margin-top:12px">'+UI.panel("queues",
      UI.table({key:"bus-queues",
        cols:[{label:"queue",align:"left"},{label:"family",align:"left"},{label:"backlog"},
              {label:"in/s"},{label:"out/s"},{label:"consumers",align:"left"}],
        rows:rows}),
      {note:"backlog 0 with in/s≈out/s means the queue is being drained as fast as it fills"})+'</div>'+
    UI.panel("topics", (function(){
      var tp=[];
      return UI.table({key:"bus-topics",
        cols:[{label:"topic",align:"left"},{label:"queue",align:"left"},{label:"in/s"},{label:"out/s"}],
        rows:qs.filter(function(q){return q.name.indexOf("q.")===0;}).map(function(q){
          return [UI.esc(q.name.slice(2)), UI.esc(q.name),
                  (q.publish_rate||0).toFixed(2), (q.deliver_rate||0).toFixed(2)];
        })});
    })(), {note:legacy.length+" legacy queue"+(legacy.length===1?"":"s")+" also drained"});
}
function normalisePanels(){
  /* any panel stranded inside a collapsed container is hoisted to .wrap */
  var wrap=document.querySelector(".wrap"); if(!wrap) return;
  ["dash","thinking","moversview","marketlive","forecast","advisor","signals","tape",
   "models","opt","lb","control","review","why","health","thinking","busview"].forEach(function(id){
    var e=document.getElementById(id);
    if(e && e.parentElement && e.parentElement!==wrap){
      wrap.appendChild(e);
    }
  });
}
function showTab(which){
  try{ normalisePanels(); }catch(e){}
  try{ var cb=$("chartbox"); if(cb) cb.classList.remove("open"); }catch(e){}
  try{ window.scrollTo(0,0); document.documentElement.scrollLeft=0; document.body.scrollLeft=0; }catch(e){}
  ["dash","market","why","review","control","lb","opt","models","tape","signals","advisor",
   "forecast","thinking","busview","moversview","marketlive","books","health"].forEach(function(k){
    var el=$(k); if(!el) return;
    if(k==="dash"){
      el.classList.toggle("off", which!=="dash");
    } else {
      el.classList.toggle("on", which===k);
    }
    // belt and braces: inline display wins over any leftover class
    el.style.display = (k===which) ? "block" : "none";
    el.scrollLeft = 0;
    var b=$("tab-"+k); if(b) b.classList.toggle("on", which===k);
    var nv=$("nav-"+k); if(nv) nv.classList.toggle("on", which===k);
  });
  try{ if(which==="why") renderWhy(); }catch(e){ tabErr("why",e); }
  try{ if(which==="review") renderReview(); }catch(e){ tabErr("review",e); }
  try{ if(which==="control") renderControl(); }catch(e){ tabErr("control",e); }
  try{ if(which==="lb") renderLeaderboard(); }catch(e){ tabErr("lb",e); }
  try{ if(which==="opt") renderOptimiser(); }catch(e){ tabErr("opt",e); }
  try{ if(which==="models"){ renderReviewCountdown(); renderModels(); setTimeout(function(){
        var el=$("modelsbody");
        if(el && !el.innerHTML.replace(/\s/g,"")){
          el.innerHTML='<div class="panel"><div class="empty">Models view returned nothing — '+
            'click Health to check the data feed, or reload the page.</div></div>';
        }
      },5000); } }catch(e){ tabErr("models",e); }
  try{ if(which==="tape") renderTape(); }catch(e){ tabErr("tape",e); }
  try{ if(which==="signals"){ renderSignals(); renderPending(); renderOpps(); } }catch(e){ tabErr("signals",e); }
  try{ if(which==="advisor"){ renderAdvisor(); renderAgentPanel(); } }catch(e){ tabErr("advisor",e); }
  try{ if(which==="forecast") renderForecast(); }catch(e){ tabErr("forecast",e); }
  try{ if(which==="busview") renderBus(); }catch(e){ tabErr("busview",e); }
  try{ if(which==="thinking") renderThinking(); }catch(e){ tabErr("thinking",e); }
  try{ if(which==="moversview"){ renderMovers(); renderFlow(); } }catch(e){ tabErr("moversview",e); }
  try{ if(which==="marketlive"){ renderMarketLive(); } mlStream(); }catch(e){ tabErr("marketlive",e); }
  try{ if(which==="books") renderBooks(); }catch(e){ tabErr("books",e); }
  try{ if(which==="health") renderHealth(); }catch(e){ tabErr("health",e); }
}


/* ============ market chart ============ */
const MIV=["1m","5m","15m","1h","4h","1d"];
/* the chart lives in its own page (/chart); these keep the shell intact */
let M={iv:"5m", engine:null, context:null};
let LASTROWS=[], GEO=null;
function openChart(sym, engine){
  var u="/chart?sym="+encodeURIComponent(sym||"BTCUSDT")+"&context=1"+
        (engine?("&engine="+encodeURIComponent(engine)):"");
  var pop=$("chartpop");
  if(pop) pop.src=u;
  var title=$("cmtitle");
  if(title) title.textContent=(sym||"")+(engine?(" · "+engine+" highlighted · all signals"):"");
  var m=$("chartmodal");
  if(m) m.classList.add("open");
}
function tabErr(tab, e){
  var b=$("err");
  if(b){ b.style.display="block"; b.textContent=tab+" tab error: "+(e&&e.message||e); }
  var host=$(tab==="why"?"wbody":tab==="review"?"rbody":"cbody");
  if(host) host.innerHTML='<div class="panel"><div class="empty">'+tab+' failed to render: '+
    esc(String(e&&e.message||e))+'</div></div>';
}
function closeChartModal(){
  var m=$("chartmodal"); if(m) m.classList.remove("open");
}
function restoreSide(){ } function restoreIv(){} function restoreOv(){} function restoreEngine(){}
function refreshCtlBtn(){ } function ctlSummary(){ return ''; }
function toggleSide(){ /* handled inside the chart page */ }
function toggleMeasure(){} function renderEngines(){} function renderMivs(){}
function renderOverlays(){} function renderTickerList(){ /* the ticker list lives in the chart page now */ } function setDefaultIv(){}
function toggleChartCtl(){ var b=$("chartctl"); if(b) b.style.display = (b.style.display==="none"?"flex":"none"); }
function chartGestures(){ return "drag to pan · shift+drag to select a range · wheel to zoom · +/- keys · double-click to reset"; }

/* ---- chart state + renderer ------------------------------------------- */
if (typeof VIEW === "undefined") { window.VIEW = {s:0, c:120, y:null}; }
function chartRows(){ return (typeof LASTROWS!=="undefined" && LASTROWS) ? LASTROWS : []; }

function resetView(){
  var rows=chartRows();
  VIEW.s = Math.max(0, rows.length-120);
  VIEW.c = Math.min(120, rows.length||120);
  VIEW.y = null;
  drawMarket();
}
function resetY(){ VIEW.y=null; drawMarket(); }
function zoomStep(factor){ zoomAt(600, (VIEW.c||120)*factor); }

function drawMarket(){
  var svg=$("chartsvg"); if(!svg) return;
  var rows=chartRows();
  if(!rows.length){ svg.innerHTML='<text x="20" y="40" fill="#59677a">no candles yet</text>'; return; }
  var W=1000,H=360,PL=8,PR=64,PT=10,PB=30;
  var n=rows.length, c=Math.max(10, Math.min(VIEW.c||120, n));
  var s=Math.max(0, Math.min(n-c, VIEW.s||0));
  var view=rows.slice(s, s+c);
  var lo=Infinity, hi=-Infinity, vmax=0;
  view.forEach(function(r){ lo=Math.min(lo,r.l); hi=Math.max(hi,r.h); vmax=Math.max(vmax,r.v||0); });
  if(VIEW.y){ lo=VIEW.y.lo; hi=VIEW.y.hi; }
  var pad=(hi-lo)*0.06 || 0.001; lo-=pad; hi+=pad;
  var plotW=W-PL-PR, plotH=H-PT-PB-56, volH=48;
  var bw=plotW/c;
  var y=function(p){ return PT + (hi-p)/(hi-lo)*plotH; };
  var volBase=PT+plotH+volH;
  var g='';
  // grid
  for(var i=0;i<=4;i++){
    var py=PT+plotH*i/4, price=hi-(hi-lo)*i/4;
    g+='<line x1="'+PL+'" y1="'+py+'" x2="'+(PL+plotW)+'" y2="'+py+'" stroke="#16202b"/>';
    g+='<text x="'+(PL+plotW+6)+'" y="'+(py+3)+'" fill="#59677a" font-size="10">'+price.toPrecision(6)+'</text>';
  }
  // candles + volume
  view.forEach(function(r,i){
    var x=PL+i*bw+bw*0.5, up=r.c>=r.o;
    var col=up?"#3fb950":"#f85149";
    g+='<line x1="'+x.toFixed(1)+'" y1="'+y(r.h).toFixed(1)+'" x2="'+x.toFixed(1)+'" y2="'+y(r.l).toFixed(1)+'" stroke="'+col+'" stroke-width="1"/>';
    var top=y(Math.max(r.o,r.c)), bot=y(Math.min(r.o,r.c));
    g+='<rect x="'+(x-bw*0.32).toFixed(1)+'" y="'+top.toFixed(1)+'" width="'+Math.max(1,bw*0.64).toFixed(1)+
       '" height="'+Math.max(1,bot-top).toFixed(1)+'" fill="'+col+'"/>';
    var vh=vmax? (r.v/vmax)*volH : 0;
    g+='<rect x="'+(x-bw*0.32).toFixed(1)+'" y="'+(volBase-vh).toFixed(1)+'" width="'+Math.max(1,bw*0.64).toFixed(1)+
       '" height="'+vh.toFixed(1)+'" fill="'+(up?"#1d3b2a":"#3b1d1d")+'"/>';
  });
  // time axis
  var ticks=Math.min(6, view.length);
  for(var t=0;t<ticks;t++){
    var idx=Math.floor(t*(view.length-1)/Math.max(1,ticks-1));
    var d=new Date(view[idx].t);
    var tx=PL+ (idx*bw) + bw*0.5;
    g+='<text x="'+tx.toFixed(0)+'" y="'+(H-10)+'" fill="#59677a" font-size="9" text-anchor="middle">'+
       d.toTimeString().slice(0,5)+'</text>';
  }
  svg.innerHTML=g;
  GEO={vs:s, vc:c, PL:PL, PR:PR, PT:PT, PB:PB, W:W, H:H, LO:lo, HI:hi, plotW:plotW, plotH:plotH};
}
function allLayers(){} function setLayerScope(){}


/* ============ per-engine controls ============ */
async function forceScan(key){
  try{
    const map={turtle:"turtle",swing:"swing",multimodels:"multimodels"};
    await fetch("/api/force?engine="+encodeURIComponent(map[key]||"all"),{method:"POST"});
    const c=document.querySelector(`.card[onclick*="${key}"]`);
    if(c){c.classList.add("scanning");setTimeout(()=>c.classList.remove("scanning"),1200);}
  }catch(e){}
}
const SPEC={
  risk:{t:"Risk",f:[
    ["risk_pct","Risk per trade %",0.1,0.05,15,"how much equity each trade can lose"],
    ["max_open_risk_pct","Book risk ceiling %",1,5,100,"total risk allowed across all open trades"],
    ["max_positions","Max open positions",1,1,40,"hard cap on concurrent trades"],
    ["conviction_cap","Conviction cap",0.1,0.5,5,"max size multiplier from signal strength"],
  ]},
  exit:{t:"Exits",f:[
    ["tp_pct","Take profit %",0.1,0.2,10,"profit target — close here"],
    ["sl_pct","Stop loss %",0.1,0.2,8,"hard stop per trade"],
    ["trail_ratio","Trail distance",0.05,0,1,"give back this fraction of profit"],
    ["trail_arm_pct","Trail arms at %",0.1,0,10,"profit needed before trailing starts"],
    ["lock_trade_pct","Per-trade lock %",0.05,0,5,"bank a trade at this profit"],
    ["lock_profit_pct","Book lock %",0.1,0,10,"bank the whole book at this profit"],
    ["lock_usd","Book lock $",1,0,100000,"bank the book once up this many dollars"],
    ["max_hold","Max hold (sec)",5,0,86400,"force close after this long; 0 = no clock"],
  ]},
  entry:{t:"Entry rules",f:[
    ["min_confidence_pct","Min confidence %",1,0,100,"reject alerts below this score"],
    ["min_agree","Models that must agree",1,1,5,"votes required to open a trade"],
    ["scan_every","Scan cadence (sec)",0.5,0.5,600,"how often the model looks for setups"],
  ]},
  turtle:{t:"Donchian rules",f:[
    ["s1_entry","S1 channel in",1,5,120,"bars in the first Donchian entry"],
    ["s1_exit","S1 channel out",1,2,120,"bars in the exit channel"],
    ["s2_entry","S2 add channel",1,5,200,"bars for the second entry"],
    ["s2_exit","S2 exit channel",1,2,200,"bars for the second exit"],
    ["n_lookback","ATR lookback",1,5,100,"bars in the volatility measure"],
    ["stop_n","Stop = N x ATR",0.1,0.5,6,"stop distance in ATRs"],
    ["max_units_total","Max units",1,1,40,"total concurrent units"],
    ["max_units_per_direction","Max per side",1,1,20,"limit on one direction"],
    ["max_units_per_market","Max per market",1,1,10,"limit per market"],
    ["vol_veto_pct","Vol veto %",1,0,200,"blanket volatility veto level"],
  ]},
  sig:{t:"Signal shaping",f:[
    ["vol_mult","Volume multiple",0.1,0,10,"volume vs median required to qualify"],
    ["tolerance","Channel tolerance",0.001,0,0.05,"how near the level counts as a break"],
    ["min_stop_frac","Min stop distance",0.0005,0.0005,0.05,"floor on stop distance for sizing"],
  ]},
};
function settingsGroups(e){
  const have=e.settings||{};
  const out=[];
  for(const k of ["risk","turtle","exit","entry","sig"]){
    const g=SPEC[k];
    const f=g.f.filter(x=>have[x[0]]!==undefined);
    if(f.length) out.push({title:g.t, fields:f.map(x=>({key:x[0],label:x[1],
      step:x[2],min:x[3],max:x[4],hint:x[5]}))});
  }
  return out;
}
async function applyAll(){
  const g=id=>{const el=document.getElementById(id);return el&&el.value!==""?parseFloat(el.value):null;};
  const body={risk_pct:g("g-risk"),tp_pct:g("g-tp"),sl_pct:g("g-sl"),
              max_positions:g("g-max"),max_open_risk_pct:g("g-ceil"),
              scan_every:g("g-scan"),trail_ratio:g("g-trail"),max_hold:g("g-hold"),
              min_liquidity:g("g-liq"),hold_sec:g("g-hs")};
  const st=$("gstat"); st.textContent="applying…";
  try{
    const r=await fetch("/api/control-all",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok){ st.textContent="rejected: "+(d.error||r.status); return; }
    const ok=Object.values(d.applied||{}).filter(Boolean).length;
    st.textContent=`✓ ${d.rules} rules → ${ok} control files${d.go_turtle?" · Go turtle ✓":" · Go turtle ✗"}`;
    setTimeout(()=>{ st.textContent=""; }, 6000);
  }catch(e){ st.textContent="failed"; }
}
async function forceAll(){
  try{ await fetch("/api/force?engine=all",{method:"POST"});
       const st=$("gstat"); st.textContent="✓ forced rescan on all models";
       setTimeout(()=>{st.textContent="";},4000);}catch(e){}
}
async function applySettings(key){
  const g=id=>{const el=document.getElementById("set-"+id);
               return el&&el.value!==""?parseFloat(el.value):null;};
  const body={};
  for(const k of ["s1_entry","s1_exit","s2_entry","s2_exit","n_lookback","stop_n",
                  "max_units_total","max_units_per_direction","max_units_per_market",
                  "vol_veto_pct",
                  "risk_pct","max_open_risk_pct","max_positions","conviction_cap",
                  "tp_pct","sl_pct","trail_ratio","trail_arm_pct","lock_trade_pct",
                  "lock_profit_pct","lock_usd","max_hold","min_confidence_pct",
                  "min_agree","scan_every","vol_mult","tolerance","min_stop_frac"])
    body[k]=g(k);
  const st=document.getElementById("sstat-"+key);
  if(st) st.textContent="applying…";
  try{
    const r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(st) st.textContent=r.ok?("✓ applied "+(d.applied_count||"")+" rules — next scan")
                              :("rejected: "+(d.error||r.status));
  }catch(e){ if(st) st.textContent="failed"; }
}
async function applyParams(key){
  const g=id=>{const el=document.getElementById(id);return el&&el.value!==""?parseFloat(el.value):null;};
  const body={risk_pct:g("p-risk"),tp_pct:g("p-tp"),sl_pct:g("p-sl"),
              max_positions:g("p-max"),max_open_risk_pct:g("p-ceil")};
  const st=document.getElementById("pstat-"+key);
  if(st) st.textContent="applying…";
  try{
    const r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(st) st.textContent = r.ok ? "✓ applied — next scan" : ("rejected: "+(d.error||r.status));
  }catch(e){ if(st) st.textContent="failed"; }
}


/* ============ ticker routing ============ */
const G={pts:new Map(), s0:0, c0:0, x0:0, pinch:0};
function svgLayer(id){
  const svg=$("msvg"); if(!svg) return null;
  let g=svg.querySelector("#"+id);
  if(!g){ g=document.createElementNS("http://www.w3.org/2000/svg","g"); g.id=id;
          svg.appendChild(g); }
  return g;
}
function localPoint(ev){
  const svg=$("msvg"); const r=svg.getBoundingClientRect();
  return {x:(ev.clientX-r.left)/r.width*1200, y:(ev.clientY-r.top)/r.height*460};
}
function startBand(ev){
  const g=svgLayer("sel"); const p=localPoint(ev);
  G.band={x0:p.x, x1:p.x, y0:p.y, y1:p.y};
  g.setAttribute("opacity","1");
  g.innerHTML=`<rect id="selrect" x="${p.x}" y="${p.y}" width="0" height="0"
      fill="#58a6ff18" stroke="#58a6ff88" stroke-dasharray="3 3"/>`;
}
function updateBand(ev){
  const g=svgLayer("sel"); if(!g||!G.band) return;
  const p=localPoint(ev); G.band.x1=p.x; G.band.y1=p.y;
  const x=Math.min(G.band.x0,p.x), w=Math.abs(p.x-G.band.x0);
  const y=Math.min(G.band.y0,p.y), hh=Math.abs(p.y-G.band.y0);
  const rect=g.querySelector("#selrect");
  if(rect){ rect.setAttribute("x",x.toFixed(1)); rect.setAttribute("width",w.toFixed(1));
            rect.setAttribute("y",y.toFixed(1)); rect.setAttribute("height",hh.toFixed(1)); }
}
function finishMeasure(ev){
  if(!G.band||!GEO||!LASTROWS.length){ clearBand(); return; }
  const {PL,PR,W,PT,PB,H,LO,HI,vs,vc}=GEO;
  const conv=yy=>HI-(HI-LO)*Math.max(0,Math.min(1,(yy-PT)/(H-PT-PB)));
  const iOf=xx=>vs+(xx-PL)/(W-PL-PR)*(vc-1);
  const i0=Math.round(iOf(G.band.x0)), i1=Math.round(iOf(G.band.x1));
  const p0=conv(G.band.y0), p1=conv(G.band.y1);
  clearBand();
  if(Math.abs(G.band.x1-G.band.x0)<6 && Math.abs(G.band.y1-G.band.y0)<6){ MEASURE=null; }
  else MEASURE={i0,i1,p0,p1};
  drawMarket();
}
function clearBand(){ const g=svgLayer("sel"); if(g){ g.innerHTML=""; g.setAttribute("opacity","0"); } G.band=null; }
function finishBand(ev){
  if(!G.band||!LASTROWS.length){ clearBand(); return; }
  const {PL,PR,W}=GEO||{PL:70,PR:86,W:1200};
  const a=Math.min(G.band.x0,G.band.x1), b=Math.max(G.band.x0,G.band.x1);
  const width=b-a;
  const ay=Math.min(G.band.y0,G.band.y1), by=Math.max(G.band.y0,G.band.y1);
  const height=by-ay;
  const y0px=G.band.y0, y1px=G.band.y1;
  clearBand();
  if(width<8 && height<8) return;         // a click, not a selection
  // vertical selection -> price zoom
  if(height>=8 && GEO){
    const {PT,PB,H,LO,HI}=GEO;
    const p=yy=>HI-(HI-LO)*Math.max(0,Math.min(1,(yy-PT)/(H-PT-PB)));
    const pHi=p(ay), pLo=p(by);
    if(pHi>pLo) VIEW.y={lo:pLo, hi:pHi};
  }
  if(width<8){ drawMarket(); return; }
  const n=LASTROWS.length, vc=VIEW.c||n, vs=VIEW.s||0;
  const i0=vs+(a-PL)/(W-PL-PR)*(vc-1);
  const i1=vs+(b-PL)/(W-PL-PR)*(vc-1);
  const nc=Math.max(20,Math.min(n,Math.round(i1-i0)+1));
  VIEW.c=nc;
  VIEW.s=Math.max(0,Math.min(n-nc,Math.round(i0)));
  drawMarket();
}
function chartGestures(){
  const svg=$("msvg"); if(!svg||svg.dataset.gest) return;
  svg.dataset.gest="1";
  const pt=e=>({x:e.clientX,y:e.clientY});
  const dist=()=>{const a=[...G.pts.values()];
    return a.length<2?0:Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y);};

  svg.addEventListener("pointerdown",e=>{
    svg.setPointerCapture(e.pointerId);
    G.pts.set(e.pointerId,pt(e));
    if(G.pts.size===1){
      G.x0=e.clientX; G.y0=e.clientY; G.s0=VIEW.s; G.c0=VIEW.c||0;
      G.mode = M.measure ? "measure" : (e.shiftKey ? "pan" : "select");
      if(G.mode==="select"||G.mode==="measure") startBand(e);
    }
    if(G.pts.size===2){ G.mode="pinch"; G.pinch=dist(); G.c0=VIEW.c||0; G.s0=VIEW.s; clearBand(); }
  });

  svg.addEventListener("pointermove",e=>{
    if(G.pts.size===0) updateCross(e);
    if(!G.pts.has(e.pointerId)) return;
    G.pts.set(e.pointerId,pt(e));
    const n=(LASTROWS||[]).length||120, W=1200, PL=70, PR=86;
    if(G.pts.size>=2 && G.pinch>0){
      const ratio=dist()/G.pinch;
      if(ratio>0){
        const pts=[...G.pts.values()];
        const mid=(pts[0].x+pts[1].x)/2;
        zoomAt(viewBoxX(mid), (G.c0||n)/ratio);
      }
      return;
    }
    if(G.mode==="measure"){ updateBand(e); return; }
    if(G.mode==="select"){ updateBand(e); return; }
    const dx=e.clientX-G.x0;
    if(Math.abs(dx)<1) return;
    const per=W-PL-PR;
    const shift=Math.round(-dx/per*(VIEW.c||n));
    const ns=Math.max(0,Math.min(n-(VIEW.c||n), G.s0+shift));
    if(ns!==VIEW.s){ VIEW.s=ns; drawMarket(); }
  });

  const end=e=>{
    if(G.mode==="measure") finishMeasure(e);
    else if(G.mode==="select") finishBand(e);
    G.pts.delete(e.pointerId);
    if(G.pts.size<2) G.pinch=0;
    if(G.pts.size===0){ G.s0=VIEW.s; G.x0=0; }
  };
  svg.addEventListener("pointerup",end);
  svg.addEventListener("pointercancel",end);
  svg.addEventListener("pointerleave",e=>{
    end(e);
    const g=$("xhair"); if(g) g.setAttribute("opacity","0");
  });

  svg.addEventListener("wheel",e=>{
    e.preventDefault();
    const n=(LASTROWS||[]).length||120, PL=70, PR=86, W=1200;

    // pinch on a trackpad arrives as ctrl+wheel -> zoom, anchored at cursor
    if(e.ctrlKey || e.metaKey){
      const f=Math.pow(1.0025, e.deltaY);
      zoomAt(viewBoxX(e.clientX), (VIEW.c||n)*f);
      return;
    }
    // shift+wheel -> scale the price axis about the cursor
    if(e.shiftKey){ zoomYAt(localPoint(e).y, e.deltaY>0?1.1:0.91); return; }

    // a trackpad reports small pixel deltas (and often a deltaX);
    // a mouse wheel reports large discrete steps -> zoom
    const trackpad = (e.deltaMode===0 && Math.abs(e.deltaY)<50);
    if(trackpad){
      const vc=VIEW.c||n;
      const perBar=(W-PL-PR)/Math.max(1,vc-1);
      const shift=Math.round((e.deltaX||0)/perBar + (e.deltaY||0)/perBar);
      const ns=Math.max(0,Math.min(n-vc,(VIEW.s||0)+shift));
      if(ns!==VIEW.s){ VIEW.s=ns; drawMarket(); }
      return;
    }
    zoomAt(viewBoxX(e.clientX), (VIEW.c||n)*(e.deltaY>0?1.12:0.89));
  },{passive:false});

  svg.addEventListener("dblclick",()=>resetView());
}

function viewBoxX(clientX){
  const svg=$("msvg"); if(!svg) return 0;
  const rect=svg.getBoundingClientRect();
  return (clientX-rect.left)/rect.width*1200;
}
// Zoom keeping the bar under `vx` pinned to the same pixel.
function zoomYAt(vy, factor){
  if(!GEO) return;
  const {PT,PB,H,LO,HI}=GEO;
  const f=Math.max(0,Math.min(1,(vy-PT)/(H-PT-PB)));
  const at=HI-(HI-LO)*f;                 // price under the cursor
  const span=(HI-LO)*factor;
  const nlo=at-span*(1-f), nhi=at+span*f;
  if(!(nhi>nlo)) return;
  VIEW.y={lo:nlo, hi:nhi};
  drawMarket();
}
function zoomAt(vx, newCount){
  const n=(LASTROWS||[]).length||120, PL=70, PR=86, W=1200;
  const nc=Math.max(20, Math.min(n, Math.round(newCount)));
  const f=Math.max(0, Math.min(1, (vx-PL)/(W-PL-PR)));
  const a=(VIEW.s||0) + f*((VIEW.c||n)-1);   // bar under the cursor
  VIEW.c=nc;
  VIEW.s=Math.max(0, Math.min(n-nc, a - f*(nc-1)));  // keep it there
  drawMarket();
}
function hhmm(t){
  const d=new Date(t);
  return d.toLocaleDateString(undefined,{month:"short",day:"2-digit"})+" "+
         d.toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",hour12:false});
}
function updateCross(ev){
  const svg=$("msvg"), g=$("xhair");
  if(!svg||!g||!GEO||!LASTROWS.length) return;
  const rect=svg.getBoundingClientRect();
  const vx=(ev.clientX-rect.left)/rect.width*GEO.W;
  const vy=(ev.clientY-rect.top)/rect.height*GEO.H;
  const {vs,vc,PL,PR,PT,PB,W,H,LO,HI}=GEO;
  if(vx<PL||vx>W-PR||vy<PT||vy>H-PB){ g.setAttribute("opacity","0"); return; }
  const i=Math.max(vs,Math.min(vs+vc-1,
        vs+Math.round((vx-PL)/(W-PL-PR)*(vc-1))));
  const c=LASTROWS[i]; if(!c) return;
  const price=LO+(HI-LO)*(1-(vy-PT)/(H-PT-PB));
  const chg=100*(c.c/c.o-1);
  const up=c.c>=c.o;
  g.setAttribute("opacity","1");
  g.innerHTML=`
    <line x1="${vx.toFixed(1)}" y1="${PT}" x2="${vx.toFixed(1)}" y2="${H-PB}"
          stroke="#4a5c72" stroke-width="1" stroke-dasharray="3 3"/>
    <line x1="${PL}" y1="${vy.toFixed(1)}" x2="${W-PR}" y2="${vy.toFixed(1)}"
          stroke="#4a5c72" stroke-width="1" stroke-dasharray="3 3"/>
    <rect x="${W-PR+3}" y="${(vy-9).toFixed(1)}" width="80" height="18" rx="4"
          fill="#1b2b3d" stroke="#2f4a66"/>
    <text x="${W-PR+43}" y="${(vy+4).toFixed(1)}" fill="#9ecbff" font-size="10.5"
          text-anchor="middle" font-family="ui-monospace,monospace">${price.toPrecision(7)}</text>
    <rect x="${(Math.min(vx,W-PR-74)).toFixed(1)}" y="${H-PB+5}" width="70" height="16" rx="4"
          fill="#1b2b3d" stroke="#2f4a66"/>
    <text x="${(Math.min(vx,W-PR-74)+35).toFixed(1)}" y="${H-PB+16}" fill="#9ecbff"
          font-size="9.5" text-anchor="middle"
          font-family="ui-monospace,monospace">${new Date(c.t).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",hour12:false})}</text>
    <g>
      <rect x="${(vx>W/2?PL+6:W-PR-236).toFixed(1)}" y="${PT+6}" width="230" height="86" rx="8"
            fill="#0b131bee" stroke="#24313e"/>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+24}" fill="#8494a8" font-size="10"
            font-family="ui-monospace,monospace">${hhmm(c.t)}</text>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+42}" fill="#e8eef6" font-size="11"
            font-family="ui-monospace,monospace">
        O ${c.o.toPrecision(7)}   H ${c.h.toPrecision(7)}</text>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+58}" fill="#e8eef6" font-size="11"
            font-family="ui-monospace,monospace">
        L ${c.l.toPrecision(7)}   C ${c.c.toPrecision(7)}</text>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+76}" fill="${up?'#3fb950':'#f85149'}"
            font-size="11" font-family="ui-monospace,monospace">
        ${up?"+":""}${chg.toFixed(2)}%   vol ${c.v>=1000?(c.v/1000).toFixed(1)+"k":c.v.toFixed(1)}</text>
    </g>`;
}





var OPTMODELS=["donchian55","orb","boll_squeeze","rsi2","vwap_revert","macd_trend","vol_break","ema_pullback","atr_channel","funding_fade"];
async function renderOptimiser(){
  var box=$("optbody"); if(!box) return;
  var jobs={};
  try{ jobs=(await (await fetch("/api/opt/jobs",{cache:"no-store"})).json()).jobs||{}; }catch(e){}
  var running=Object.keys(jobs).filter(function(k){return jobs[k].status==="running"||jobs[k].status==="queued";}).length;
  if($("optn")) $("optn").textContent=Object.keys(jobs).length+" backtests · "+running+" running";
  var rows=OPTMODELS.map(function(m){
    var j=jobs[m]||{}, pct=j.progress||0, st=j.status||"idle";
    var res=(j.result||{}).best;
    var bar='<div class="pbar"><i style="width:'+pct+'%;background:'+
      (st==="done"?"#3fb950":st==="failed"?"#f85149":st==="running"?"#d29922":"#2a3644")+'"></i></div>';
    var verdict=res?('<span class="'+(res.avg_net>=0.10?"up":"down")+'">'+res.avg_net.toFixed(3)+
      '% avg · '+res.trades+' trades · '+res.win_pct.toFixed(0)+'% win</span>'):'<span style="color:#59677a">not calculated</span>';
    var bestp=res?('lock '+res.params.lock+'% · stop '+res.params.stop+'% · clock '+res.params.clock+'s'):'—';
    var btn=(st==="running"||st==="queued")?('<span class="mk">'+pct+'% '+(j.note||"")+'</span>'):
      '<button class="tbtn" onclick="optRun(\''+m+'\')">Calculate</button>';
    var apply=res?' <button class="tbtn" onclick="optApply(\''+m+'\')">apply</button>':'';
    return '<tr><td style="font-weight:600">'+m+'</td><td>'+bar+'</td>'+
      '<td class="mini">'+st+'</td><td>'+verdict+'</td><td class="mini">'+bestp+'</td>'+
      '<td style="text-align:right">'+btn+apply+'</td></tr>';
  }).join("");
  box.innerHTML='<div class="panel"><table class="wide"><thead><tr><th>model</th><th>progress</th><th>status</th>'+
    '<th>best backtest</th><th>winning parameters</th><th></th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}
async function optRun(m){
  try{ await fetch("/api/opt/run?model="+encodeURIComponent(m),{method:"POST"}); }catch(e){}
  renderOptimiser();
  if(!window.__optTimer) window.__optTimer=setInterval(function(){
    var el=document.getElementById("opt");
    if(el && el.classList.contains("on")) renderOptimiser();
  },1200);
}
async function optApply(m){
  try{ await fetch("/api/opt/apply?model="+encodeURIComponent(m),{method:"POST"}); }catch(e){}
  renderOptimiser();
}


async function renderBooks(){
  var head=$("bookshead"), grid=$("booksgrid"), note=$("booksnote");
  if(!grid) return;
  var d={};
  try{ d=await (await fetch("/api/books",{cache:"no-store"})).json(); }catch(e){}
  var books=d.books||[];
  if(!books.length){ grid.innerHTML='<div class="panel"><div class="empty">no books yet</div></div>'; return; }
  if(note) note.textContent=books.length+" books · "+d.open+" open positions · "+d.closed+" closed trades";

  var active=books.filter(function(b){ return b.trades>0 || b.open>0; }).length;
  var flat=books.length-active;
  head.innerHTML='<div class="panel" style="padding:12px 14px;margin-bottom:12px">'+
    '<div style="display:flex;gap:26px;flex-wrap:wrap;align-items:center">'+
    UI.stat("combined equity","$"+Number(d.equity||0).toLocaleString(undefined,{maximumFractionDigits:0}))+
    UI.stat("return",(d.return_pct>=0?"+":"")+Number(d.return_pct||0).toFixed(3)+"%", (d.return_pct>=0?"#3fb950":"#f85149"))+
    UI.stat("books", books.length)+
    UI.stat("books trading", active, "#58a6ff")+
    UI.stat("books idle", flat, "#7c8b9d")+
    UI.stat("open positions", d.open||0)+
    UI.stat("closed trades", d.closed||0)+
    '</div>'+
    '<div class="mk" style="margin-top:6px">'+books.length+' books each start at $'+
      Number(books[0].start||5000).toLocaleString()+' · '+(d.open||0)+
      ' positions open across them · '+(d.closed||0)+' trades closed in total</div>'+
    '</div>';

  books.sort(function(a,b){ return b.return_pct-a.return_pct; });
  grid.innerHTML='<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px">'+
    books.map(function(b){
      var col=b.return_pct>0?"#3fb950":b.return_pct<0?"#f85149":"#7c8b9d";
      var curve=b.curve||[];
      var spark="";
      if(curve.length>1){
        var lo=Math.min.apply(null,curve.concat([0])), hi=Math.max.apply(null,curve.concat([0]));
        var rng=(hi-lo)||1;
        spark='<svg width="100%" height="34" viewBox="0 0 200 34" preserveAspectRatio="none" style="display:block">'+
          '<line x1="0" y1="'+(34-(0-lo)/rng*34).toFixed(1)+'" x2="200" y2="'+(34-(0-lo)/rng*34).toFixed(1)+'" stroke="#22303e" stroke-dasharray="2 3"/>'+
          '<polyline fill="none" stroke="'+col+'" stroke-width="1.6" points="'+
            curve.map(function(v,i){ return (i/(curve.length-1)*200).toFixed(1)+","+(34-(v-lo)/rng*34).toFixed(1); }).join(" ")+'"/>'+
          '</svg>';
      }
      var pos=(b.positions||[]).slice(0,3).map(function(p){
        return '<div style="display:flex;gap:6px;font-family:var(--mono);font-size:10.5px;color:#8095ab">'+
          '<b style="color:'+(p.side==="buy"?"#3fb950":"#f85149")+'">'+esc((p.side||"").toUpperCase())+'</b>'+
          '<span>'+esc(p.symbol)+'</span><span style="flex:1"></span>'+
          '<span>$'+Number(p.notional||0).toFixed(0)+'</span></div>';
      }).join("");
      var more=b.open-(b.positions||[]).length;
      return '<div class="panel" style="padding:12px 14px;cursor:pointer" onclick="openBook(\''+esc(b.engine)+'\')">'+
        '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px">'+
          '<b style="font-size:13px">'+esc(b.engine)+'</b>'+
          '<span class="mk">'+b.trades+' closed · '+b.open+' open</span>'+
          '<span style="flex:1"></span>'+
          '<b style="color:'+col+';font-size:14px">'+(b.return_pct>0?"+":"")+Number(b.return_pct).toFixed(2)+'%</b>'+
        '</div>'+
        spark+
        '<div style="display:flex;gap:12px;margin-top:6px;font-family:var(--mono);font-size:11px">'+
          '<span style="color:#9fb0c4">$'+Number(b.equity).toLocaleString(undefined,{maximumFractionDigits:0})+'</span>'+
          '<span style="color:#7c8b9d">win '+Number(b.win_pct).toFixed(0)+'%</span>'+
          '<span style="color:'+(b.avg_net>=0?"#3fb950":"#f85149")+'">avg '+Number(b.avg_net).toFixed(3)+'%</span>'+
        '</div>'+
        (pos?'<div style="margin-top:8px;border-top:1px solid #111a24;padding-top:6px">'+pos+
             (more>0?'<div class="mk">+' + more + ' more</div>':'')+'</div>':'')+
      '</div>';
    }).join("")+'</div>';
}

async function openBook(engine){
  var d={};
  try{ d=await (await fetch("/api/books",{cache:"no-store"})).json(); }catch(e){}
  var b=(d.books||[]).find(function(x){ return x.engine===engine; });
  if(!b) return;
  var col=b.return_pct>=0?"#3fb950":"#f85149";
  var pos=(b.positions||[]).map(function(p){
    return UI.table({key:"bk-pos",cols:[{label:"symbol",align:"left"},{label:"side"},{label:"entry"},{label:"notional"}],
      rows:[]}); }).join("");
  var posTable=(b.positions||[]).length? UI.table({key:"bkpos",
      cols:[{label:"symbol",align:"left"},{label:"side"},{label:"entry",align:"right"},{label:"notional",align:"right"}],
      rows:(b.positions||[]).map(function(p){
        return [esc(p.symbol), '<span style="color:'+(p.side==="buy"?"#3fb950":"#f85149")+'">'+esc((p.side||"").toUpperCase())+'</span>',
                p.entry==null?"—":String(p.entry), "$"+Number(p.notional||0).toFixed(0)]; })}) :
    '<div class="mk">flat — no open positions</div>';
  var tTable=(b.recent||[]).length? UI.table({key:"bktrades",
      cols:[{label:"symbol",align:"left"},{label:"side"},{label:"net %",align:"right"},{label:"reason",align:"left"},{label:"held",align:"right"}],
      rows:(b.recent||[]).map(function(t){
        var c=(t.net_pct||0)>=0?"#3fb950":"#f85149";
        return [esc(t.symbol), esc((t.side||"").toUpperCase()),
                '<span style="color:'+c+'">'+((t.net_pct||0)>0?"+":"")+Number(t.net_pct||0).toFixed(3)+'</span>',
                esc(t.reason||"—"), Math.round(t.held_s||0)+"s"]; })}) :
    '<div class="mk">no closes yet</div>';
  $("sheet").innerHTML='<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'+
    '<h3 style="margin:0">'+esc(engine)+'</h3>'+
    '<b style="color:'+col+'">'+(b.return_pct>0?"+":"")+Number(b.return_pct).toFixed(2)+'%</b>'+
    '<span class="mk">$'+Number(b.equity).toLocaleString(undefined,{maximumFractionDigits:0})+' of $'+Number(b.start).toLocaleString()+'</span>'+
    '<span style="flex:1"></span><button class="tbtn" onclick="closeModal()">close</button></div>'+
    UI.panel("book", '<div style="display:flex;gap:26px">'+
      UI.stat("closed", b.trades)+UI.stat("open", b.open)+
      UI.stat("win rate", Number(b.win_pct).toFixed(0)+"%", b.win_pct>=50?"#3fb950":"#f85149")+
      UI.stat("avg net", (b.avg_net>0?"+":"")+Number(b.avg_net).toFixed(3)+"%", b.avg_net>=0?"#3fb950":"#f85149")+
      '</div>')+
    UI.panel("open positions", posTable)+
    UI.panel("recent closes", tTable);
  $("modal").classList.add("open");
}

async function renderHealth(){
  var el=$("healthbody"); if(!el) return;
  el.innerHTML='<div class="hint">checking every view…</div>';
  try{
    var d=await (await fetch("/api/selfcheck",{cache:"no-store"})).json();
    el.innerHTML=(d.checks||[]).map(function(c){
      return '<div style="display:grid;grid-template-columns:150px 70px 1fr;gap:10px;'+
        'align-items:center;padding:5px 0;border-bottom:1px solid #131c26;font-family:var(--mono);font-size:11.5px">'+
        '<b>'+esc(c.view)+'</b>'+
        '<span class="pill '+(c.ok?'ok':'bad')+'">'+(c.ok?'ok':'failing')+'</span>'+
        '<span style="color:#7c8b9d">'+esc(c.detail)+'</span></div>';
    }).join("");
  }catch(e){ el.innerHTML='<div class="hint">self-check unavailable</div>'; }
}
async function renderOptions(){
  var el=$("optsbody"); if(!el) return;
  var o={};
  try{ o=await (await fetch("/api/options",{cache:"no-store"})).json(); }catch(e){}
  var rows=[
    ["override_crazy","BYPASS EVERY GATE — crazy trading","#f85149"],
    ["allow_counter_trend","trade against the BTC 4h trend",o.allow_counter_trend],
    ["forecast_gate","consult the forecast supervisor",o.forecast_gate],
  ];
  var nums=[["forecast_min","min forecast probability",o.forecast_min,0.05],
            ["max_positions","max positions per model",o.max_positions,1],
            ["clock_s","max hold (seconds)",o.clock_s,30],
            ["lock_pct","take profit (%)",o.lock_pct,0.05]];
  var html=rows.map(function(r){
    var danger=r[0]==="override_crazy";
    return '<label style="display:flex;gap:10px;align-items:center;padding:5px 0;'+
      'border-bottom:1px solid '+(danger?'#f8514933':'#131c26')+'">'+
      '<input type="checkbox" '+(r[2]?'checked':'')+' onchange="setOpt(\''+r[0]+'\',this.checked)">'+
      '<b style="width:190px;color:'+(danger?'#f85149':'inherit')+'">'+r[0]+'</b>'+
      '<span style="color:'+(danger?'#ffb3ad':'#7c8b9d')+'">'+r[1]+'</span></label>';
  }).join("");
  html+=nums.map(function(r){
    return '<label style="display:flex;gap:10px;align-items:center;padding:5px 0;border-bottom:1px solid #131c26">'+
      '<b style="width:190px">'+r[0]+'</b>'+
      '<input type="number" step="'+r[3]+'" value="'+r[2]+'" style="width:90px;background:#0b1119;'+
      'border:1px solid #24313e;color:#e6edf6;border-radius:5px;padding:3px 6px" '+
      'onchange="setOpt(\''+r[0]+'\',parseFloat(this.value))">'+
      '<span style="color:#7c8b9d">'+r[1]+'</span></label>';
  }).join("");
  el.innerHTML=html;
}
async function setOpt(k,v){
  try{ await fetch("/api/options?"+encodeURIComponent(k)+"="+encodeURIComponent(v),{cache:"no-store"}); }catch(e){}
  renderReviewCountdown(); setInterval(renderReviewCountdown,1000);
normalisePanels();
renderThinking();
renderOptions();
}
async function renderThinking(){
  var box=$("thbody"); if(!box) return;
  var d={};
  try{ d=await (await fetch("/api/thinking",{cache:"no-store"})).json(); }catch(e){
    box.innerHTML='<div class="panel"><div class="empty">thinking endpoint unavailable</div></div>'; return; }
  try{
  var src=d.sources||{}, b=d.books||{}, blocks=d.blocks||{}, dec=d.decisions||[];
  var opts=d.options||{};
  if($("thn")) $("thn").textContent=(b.count||0)+" books · "+(b.open||0)+" open · "+(b.closed||0)+" closed";

  var card=function(title,accent,body){
    return '<div class="panel" style="padding:12px 14px"><div style="font-size:10px;letter-spacing:.13em;'+
      'text-transform:uppercase;color:'+accent+';margin-bottom:8px">'+title+'</div>'+body+'</div>';
  };
  var kv=function(k,v,c){ return '<div style="display:flex;justify-content:space-between;padding:3px 0;'+
    'border-bottom:1px solid #131c26;font-family:var(--mono);font-size:11.5px">'+
    '<span style="color:#7c8b9d">'+k+'</span><b style="color:'+(c||"#e6edf6")+'">'+v+'</b></div>'; };

  // what it sees
  var md=src.market_data||{};
  var sees=kv("stream status", esc(md.status||"—"), "#3fb950")+
    kv("messages received", (md.msgs||0).toLocaleString())+
    kv("candle series", md.series||0)+
    kv("tickers tracked", md.tickers||0)+
    kv("prices live", md.prices||0)+
    kv("rate-limit violations", md.violations||0, (md.violations?"#f85149":"#3fb950"))+
    kv("alerts seen", ((src.signal_bus||{}).cursor||0));

  // what it refuses
  var blockRows=Object.keys(blocks).sort(function(a,b){return blocks[b]-blocks[a];}).slice(0,7)
    .map(function(k){ return kv(esc(k), blocks[k], "#f85149"); }).join("") ||
    '<div class="mk">no refusals recorded</div>';

  // what it likes
  var likes=(d.signals||[]).map(function(s){
    var _fc=Number(s.flow_conf)||0;
    var c=_fc>=0?"#3fb950":"#f85149";
    return '<div style="display:grid;grid-template-columns:82px 1fr 56px;gap:8px;align-items:center;'+
      'font-family:var(--mono);font-size:11px;padding:3px 0;border-bottom:1px solid #131c26">'+
      '<b style="cursor:pointer" onclick="drillChart(\''+esc(s.symbol)+'\',null)">'+esc(s.symbol)+'</b>'+
      '<span style="height:5px;background:#16202b;border-radius:3px;overflow:hidden;display:flex">'+
      '<i style="width:'+Math.min(100,Math.abs(_fc))+'%;background:'+c+';'+
      (_fc<0?'margin-left:auto':'')+'"></i></span>'+
      '<span style="text-align:right;color:'+c+'">'+((Number(s.flow_conf)||0)>0?'+':'')+Number(s.flow_conf||0).toFixed(0)+'</span></div>';
  }).join("") || '<div class="mk">signals warming up</div>';

  // the narrative
  var story=dec.slice(0,18).map(function(x){
    var ok=x.allowed;
    return '<div style="display:grid;grid-template-columns:64px 74px 1fr;gap:8px;font-family:var(--mono);'+
      'font-size:11px;padding:2px 0;border-bottom:1px solid #10171f">'+
      '<span style="color:#59677a">'+new Date(x.ts).toTimeString().slice(0,8)+'</span>'+
      '<span style="color:'+(ok?"#3fb950":"#f85149")+'">'+(ok?"ALLOW":"BLOCK")+'</span>'+
      '<span style="color:#9fb0c4">'+esc(x.symbol)+' '+esc((x.side||"").toUpperCase())+' — '+esc(x.reason)+'</span></div>';
  }).join("") || '<div class="mk">no decisions yet</div>';

  var mode=(opts.override_crazy?'<span style="color:#f85149">OVERRIDE — all gates bypassed</span>':
    'normal — gates active');

  box.innerHTML='<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px">'+
    card("what it sees","#58a6ff",sees)+
    card("what it refuses","#f85149",blockRows)+
    card("where flow is strongest","#3fb950",likes)+
    '</div>'+
    '<div style="display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:12px;margin-top:12px">'+
    card("decision stream","#a371f7",story)+
    card("operating mode","#d29922",
      kv("mode","",'')&&kv("gates",mode)+ kv("forecast gate",opts.forecast_gate?"on":"off")+
      kv("counter-trend",opts.allow_counter_trend?"allowed":"blocked")+
      kv("min expected move",(opts.min_expected_move||0)+"%")+
      kv("max positions",opts.max_positions||0)+
      kv("clock",(opts.clock_s||0)+"s")+
      kv("books",(b.count||0)+" · "+(b.open||0)+" open")+
      kv("equity","$"+Math.round(b.equity||0).toLocaleString()))+
    '</div>';
  }catch(err){ box.innerHTML='<div class="panel"><div class="empty">thinking error: '+
    String(err && err.message || err).replace(/</g,"&lt;")+'</div></div>'; }
}
async function renderReviewCountdown(){
  var box=$("revcount"); if(!box) return;
  var d={};
  try{ d=await (await fetch("/api/review_state",{cache:"no-store"})).json(); }catch(e){
    box.innerHTML='<span class="mk">portfolio reviewer not running</span>'; return; }
  var every=(d.every_s||10)*1000, ts=d.ts||Date.now();
  var left=Math.max(0, every-(Date.now()-ts));
  var pct=100*left/every;
  var books=d.books||[], stopped=d.stopped||[];
  var atRisk=books.filter(function(b){ return (b.trades>=3 && b.return_pct<=-1.5) || (b.trades>=3 && (b.recent_sum||0)<0); })
                  .sort(function(a,b){ return (a.return_pct||0)-(b.return_pct||0); }).slice(0,5);
  var chips=atRisk.map(function(b){
    return '<span style="margin-right:10px;color:'+((b.return_pct||0)<0?'#f85149':'#d29922')+'">'+
      esc(b.engine)+' '+(b.return_pct||0).toFixed(2)+'%</span>'; }).join("") || '<span class="mk">nobody at risk</span>';
  box.innerHTML='<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">'+
    '<span style="font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#6b7c90">'+
      'portfolio review</span>'+
    '<b style="font-family:var(--mono);font-size:15px;color:'+(left<5000?"#d29922":"#3fb950")+'">'+
      Math.ceil(left/1000)+'s</b>'+
    '<span style="width:120px;height:6px;background:#16202b;border-radius:3px;overflow:hidden">'+
      '<i style="display:block;height:100%;width:'+pct.toFixed(0)+'%;background:#3d6a94"></i></span>'+
    '<span class="mk">stops a book at −2% or a 5-loss streak</span>'+
    '<span style="flex:1"></span>'+
    '<span class="mk">at risk:</span>'+chips+
    '<span class="mk">stopped: '+(stopped.length?esc(stopped.join(", ")):"none")+'</span></div>';
}
async function openModel(key){
  var d={};
  try{ d=await (await fetch("/api/model?engine="+encodeURIComponent(key),{cache:"no-store"})).json(); }catch(e){}
  var m=d.meta||{}, b=d.book||{}, log=d.log||[];
  var col=(b.return_pct||0)>=0?"#3fb950":"#f85149";
  var math=(m.math||[]).map(function(line){
    return '<div style="font-family:var(--mono);font-size:10.5px;color:#c9d6e3;background:#0b1119;'+
      'border-left:2px solid #24313e;padding:3px 8px;margin-bottom:3px;white-space:pre-wrap">'+esc(line)+'</div>';
  }).join("") || '<div class="mk">no formulas registered for this engine</div>';
  var params=Object.keys(m.params||{}).map(function(k){
    return UI.kv(k, String(m.params[k]));
  }).join("") || '<div class="mk">no parameters recorded</div>';
  var pos=(b.positions||[]).length? UI.table({key:"mdl-pos",
      cols:[{label:"symbol",align:"left"},{label:"side"},{label:"entry",align:"right"},{label:"notional",align:"right"}],
      rows:(b.positions||[]).slice(0,12).map(function(q){
        return [esc(q.symbol), '<span style="color:'+(q.side==="buy"?"#3fb950":"#f85149")+'">'+
                esc((q.side||"").toUpperCase())+'</span>',
                q.entry==null?"—":String(q.entry), "$"+Number(q.notional||0).toFixed(0)];})}) :
    '<div class="mk">flat</div>';
  var tr=(b.trades||[]).slice().reverse().slice(0,12).map(function(t){
    var c=(t.net_pct||0)>=0?"#3fb950":"#f85149";
    return [esc(t.symbol), esc((t.side||"").toUpperCase()),
            '<span style="color:'+c+'">'+((t.net_pct||0)>0?"+":"")+Number(t.net_pct||0).toFixed(3)+'</span>',
            esc(t.reason||"—"), Math.round(t.held_s||0)+"s"];
  });
  var trades=tr.length? UI.table({key:"mdl-tr",
      cols:[{label:"symbol",align:"left"},{label:"side"},{label:"net %",align:"right"},
            {label:"reason",align:"left"},{label:"held",align:"right"}], rows:tr}) :
    '<div class="mk">no closed trades yet</div>';
  var logHtml=log.length? '<div style="font-family:var(--mono);font-size:10px;line-height:1.5;'+
      'background:#080d13;border:1px solid #16202b;border-radius:6px;padding:8px;max-height:320px;overflow:auto">'+
      log.map(function(l){
        var c = /CLOSE/.test(l) ? "#3fb950" : /OPEN/.test(l) ? "#58a6ff" :
                /error|Error|Traceback/.test(l) ? "#f85149" : "#7c8b9d";
        return '<div style="color:'+c+';white-space:pre-wrap">'+esc(l)+'</div>';
      }).join("")+'</div>' : '<div class="mk">no log file yet</div>';
  var stats=[UI.stat("equity","$"+Number(b.equity||0).toFixed(0)),
             UI.stat("return",(b.return_pct>0?"+":"")+Number(b.return_pct||0).toFixed(2)+"%", col),
             UI.stat("closed", b.closed||0),
             UI.stat("open",(b.positions||[]).length)].join("");
  $("sheet").innerHTML='<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'+
    '<h3 style="margin:0">'+esc(m.name||key)+'</h3>'+
    '<span class="pill">'+esc(m.family||"book")+'</span>'+
    '<span style="flex:1"></span><button class="tbtn" onclick="closeModal()">close</button></div>'+
    '<div class="panel" style="padding:12px 14px;margin-bottom:10px"><div style="display:flex;gap:26px;flex-wrap:wrap">'+
      stats+'</div><div style="margin-top:8px;color:#c9d6e3;font-size:11.5px;line-height:1.6">'+
      esc(m.summary||"")+'</div></div>'+
    UI.panel("how it works — the maths in the code", math, {note:"each line is a rule the engine evaluates"})+
    UI.panel("parameters in force", '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">'+
      params+'</div>')+
    UI.panel("open positions", pos)+
    UI.panel("recent closes", trades)+
    UI.panel("log", logHtml, {note:"tail of the engine's own log file"});
  $("modal").classList.add("open");
}

async function renderModels(){
  var box=$("modelsbody"), note=$("mn");
  if(!box) return;
  box.innerHTML='<div class="hint" style="padding:8px 0">loading models…</div>';
  var d1={}, alive={}, disabled=[];
  try{ d1=await (await fetch("/api/all",{cache:"no-store"})).json(); }catch(e){}
  try{ var dj=await (await fetch("/api/model_control",{cache:"no-store"})).json(); disabled=dj.disabled||[]; }catch(e){}
  var books=(d1.engines||[]).filter(function(e){return e.equity!=null;});
  if(note) note.textContent=books.length+" books · "+disabled.length+" stopped";
  if(!books.length){ box.innerHTML='<div class="panel"><div class="empty">no books reporting</div></div>'; return; }
  var rows=books.map(function(e){
    var tr=e.trades||[], n=tr.length;
    var wins=tr.filter(function(t){return (t.net_pct||0)>0;}).length;
    var wr=n?(100*wins/n).toFixed(0)+"%":"—";
    var off=disabled.indexOf(e.key)>=0;
    var ret=e.return_pct||0;
    return '<tr class="sigrow" style="cursor:pointer" onclick="openModel(\''+esc(e.key)+'\')">'+
      '<td style="color:'+(e.colour||'#c9d6e3')+';font-weight:700">'+esc(e.key)+'</td>'+
      '<td>'+engSpark(e.key,70,15)+'</td>'+
      '<td style="text-align:right">$'+money(e.equity)+'</td>'+
      '<td class="'+(ret>=0?'up':'down')+'" style="text-align:right">'+(ret>=0?'+':'')+ret.toFixed(2)+'%</td>'+
      '<td class="'+((e.realised||0)>=0?'up':'down')+'" style="text-align:right">'+
        ((e.realised||0)>=0?'+':'')+money(e.realised)+'</td>'+
      '<td style="text-align:right">'+n+'</td>'+
      '<td style="text-align:right">'+wr+'</td>'+
      '<td style="text-align:right">'+((e.positions||[]).length)+'</td>'+
      '<td style="text-align:right">'+(off?'<span class="pill bad">stopped</span>':
        '<span class="pill ok">running</span>')+'</td>'+
      '<td style="text-align:right"><button class="tbtn" onclick="event.stopPropagation();toggleModel(\''+
        esc(e.key)+'\','+(off?'true':'false')+')">'+(off?'enable':'disable')+'</button></td>'+
      '</tr>';
  }).join("");
  box.innerHTML='<table class="wide"><thead><tr><th>model</th><th>curve</th>'+
    '<th style="text-align:right">equity</th><th style="text-align:right">return</th>'+
    '<th style="text-align:right">realised</th><th style="text-align:right">trades</th>'+
    '<th style="text-align:right">win</th><th style="text-align:right">open</th>'+
    '<th style="text-align:right">status</th><th></th></tr></thead><tbody>'+rows+'</tbody></table>';
}
async function toggleModel(name, enable){
  var act=enable?"start":"stop";
  try{ await fetch("/api/model_control?model="+encodeURIComponent(name)+"&action="+act,{method:"POST"}); }catch(e){}
  renderModels();
}
/* ================= LEADERBOARD ================= */
var HIST={};          // engine -> [equity samples]
var LBSORT={key:"realised", dir:-1};   // default: best realised P&L first
function lbSort(k){
  if(LBSORT.key===k){ LBSORT.dir*=-1; }
  else { LBSORT.key=k; LBSORT.dir=(k==="engine"||k==="key")?1:-1; }
  renderLeaderboard();
}
function renderAttrib(es){
  var box=$("attrib"); if(!box) return;
  var all=[];
  es.forEach(function(e){ (e.trades||[]).forEach(function(t){ t._e=e.key; all.push(t); }); });
  if(!all.length){ box.innerHTML='<div class="mk">no closed trades yet — attribution appears with the first close</div>'; return; }
  var longs=all.filter(function(t){return (t.side||"").toLowerCase()==="buy";});
  var shorts=all.filter(function(t){return (t.side||"").toLowerCase()==="sell";});
  var avg=function(a){ return a.length? a.reduce(function(x,t){return x+(t.net_pct||0);},0)/a.length : 0; };
  var sum=function(a){ return a.reduce(function(x,t){return x+(t.net_pct||0);},0); };
  var wins=all.filter(function(t){return (t.net_pct||0)>0;}).length;
  var gross=all.reduce(function(x,t){return x+(t.gross_pct||0);},0)/all.length;
  var net=sum(all)/all.length;
  var fee=gross-net;
  var holds=all.reduce(function(x,t){return x+(t.held_s||0);},0)/all.length;
  var cell=function(k,v,c,sub){ return '<div style="min-width:118px"><div style="font-size:9px;letter-spacing:.11em;'+
    'text-transform:uppercase;color:#5f7186">'+k+'</div><div style="font-family:var(--mono);'+
    'font-size:14px;font-weight:700;color:'+(c||"#e6edf6")+'">'+v+'</div>'+
    (sub?'<div style="font-size:9.5px;color:#59677a">'+sub+'</div>':'')+'</div>'; };
  var c=function(v){ return v>=0?"#3fb950":"#f85149"; };
  box.innerHTML='<div style="display:flex;gap:22px;flex-wrap:wrap;align-items:flex-start">'+
    '<div style="font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#6b7c90;'+
    'writing-mode:vertical-rl;transform:rotate(180deg)">attribution</div>'+
    cell("closed",all.length,"#e6edf6",wins+" wins")+
    cell("win rate",(100*wins/all.length).toFixed(0)+"%",c(wins/all.length-0.5))+
    cell("avg gross",(gross>=0?"+":"")+gross.toFixed(3)+"%",c(gross))+
    cell("avg net",(net>=0?"+":"")+net.toFixed(3)+"%",c(net),"fee "+fee.toFixed(3)+"%")+
    cell("longs",longs.length+" · "+(avg(longs)>=0?"+":"")+avg(longs).toFixed(3)+"%",c(avg(longs))) +
    cell("shorts",shorts.length+" · "+(avg(shorts)>=0?"+":"")+avg(shorts).toFixed(3)+"%",c(avg(shorts)))+
    cell("avg hold",Math.round(holds)+"s","#e6edf6")+
    '</div>';
}
function histPush(es){
  es.forEach(function(e){
    if(!e||e.equity==null) return;
    var h=HIST[e.key]||(HIST[e.key]=[]);
    h.push(e.equity);
    if(h.length>90) h.shift();
  });
}
function spark(h, w, hh){
  if(!h||h.length<2) return '<span class="mk">—</span>';
  var lo=Math.min.apply(null,h), hi=Math.max.apply(null,h);
  var rng=(hi-lo)||1;
  var pts=h.map(function(v,i){
    var x=(i/(h.length-1))*w;
    var y=hh-2-((v-lo)/rng)*(hh-4);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  var up=h[h.length-1]>=h[0];
  return '<svg width="'+w+'" height="'+hh+'" style="display:block">'+
    '<polyline points="'+pts+'" fill="none" stroke="'+(up?'#3fb950':'#f85149')+
    '" stroke-width="1.4"/></svg>';
}
function lbRow(e, rank, best, worst){
  var pos=e.positions||[], tr=e.trades||[];
  var wins=tr.filter(function(t){ return (t.net_pct||0)>0; }).length;
  var wr=tr.length?Math.round(100*wins/tr.length):null;
  var avg=tr.length?(tr.reduce(function(a,t){ return a+(t.net_pct||0); },0)/tr.length):null;
  var ret=e.return_pct||0;
  var span=Math.max(1,Math.abs(best)+Math.abs(worst));
  var pctW=Math.min(100,Math.abs(ret)/span*100);
  var medal = rank===1?'gold':rank===2?'silver':rank===3?'bronze':'';
  var h=HIST[e.key]||[];
  return '<tr class="'+(rank<=3?('top'+rank):'')+'" style="cursor:pointer" '+
    'onclick="drillEngine(\''+esc(e.key)+'\')" title="open '+esc(e.key)+'">'+
    '<td><span class="rank '+medal+'">'+rank+'</span></td>'+
    '<td style="color:'+e.colour+';font-weight:700">'+esc(e.key)+
      '<div class="mk" style="font-weight:400">'+esc(e.label||'')+'</div></td>'+
    '<td>$'+money(e.equity)+'<div class="mk" style="font-weight:400">'+
      (TOTEQ?(100*e.equity/TOTEQ).toFixed(1)+'% of book':'')+'</div></td>'+
    '<td class="'+(ret>=0?'up':'down')+'"><b>'+(ret>=0?'+':'')+ret.toFixed(2)+'%</b>'+
      '<div class="lbbar" style="margin-top:4px"><i style="width:'+pctW.toFixed(0)+'%;background:'+
      (ret>=0?'#3fb950':'#f85149')+'"></i></div></td>'+
    '<td class="'+((e.realised||0)>=0?'up':'down')+'">'+((e.realised||0)>=0?'+':'')+money(e.realised)+'</td>'+
    '<td class="'+((e.unrealised||0)>=0?'up':'down')+'">'+((e.unrealised||0)>=0?'+':'')+money(e.unrealised)+'</td>'+
    '<td>'+tr.length+'</td>'+
    '<td>'+(wr==null?'—':wr+'%')+'</td>'+
    '<td class="'+(avg!=null&&avg>=0?'up':'down')+'">'+(avg==null?'—':(avg>=0?'+':'')+avg.toFixed(3)+'%')+'</td>'+
    '<td>'+pos.length+'</td>'+
    '<td>'+spark(h,90,20)+'</td></tr>';
}
function podium(es){
  var top=es.slice(0,3);
  var medal=["\u{1F947}","\u{1F948}","\u{1F949}"];
  return '<div class="podium">'+top.map(function(e,i){
    var ret=e.return_pct||0;
    var col=ret>=0?"#3fb950":"#f85149";
    return '<div class="pod p'+(i+1)+'" onclick="drillEngine(\''+esc(e.key)+'\')">'+
      '<div class="pmed">'+medal[i]+'</div>'+
      '<div class="pname" style="color:'+e.colour+'">'+esc(e.key)+'</div>'+
      '<div class="pret" style="color:'+col+'">'+(ret>=0?"+":"")+ret.toFixed(2)+'%</div>'+
      '<div class="psub">equity $'+money(e.equity)+'</div>'+
      '<div class="psub">realised <b style="color:'+col+'">'+
        ((e.realised||0)>=0?"+":"")+'$'+money(e.realised)+'</b></div>'+
      '<div class="psub">'+(e.closed||0)+' closed · '+((e.positions||[]).length)+' open</div>'+
      '</div>';
  }).join("")+'</div>';
}
function renderLeaderboard(){
  var es=(LAST||[]).filter(function(e){ return e.equity!=null; });
  histPush(es);
  var S=LBSORT, d=S.dir;
  var val=function(e){
    var tr=e.trades||[];
    if(S.key==="key") return e.key;
    if(S.key==="win") return tr.length?100*tr.filter(function(t){return (t.net_pct||0)>0;}).length/tr.length:-1;
    if(S.key==="avgnet") return tr.length?tr.reduce(function(a,t){return a+(t.net_pct||0);},0)/tr.length:-999;
    if(S.key==="open") return (e.positions||[]).length;
    if(S.key==="closed") return tr.length;
    return e[S.key]==null?-999:e[S.key];
  };
  TOTEQ=es.reduce(function(a,e){return a+(e.equity||0);},0);
  es.sort(function(a,b){
    var x=val(a), y=val(b);
    if(typeof x==="string") return d*x.localeCompare(y);
    return d*(x-y);
  });
  var best=Math.max.apply(null, es.map(function(e){ return Math.abs(e.return_pct||0); }).concat([1]));
  var worst=-best;
  var box=$("lbbody"), note=$("lbn");
  if(!box) return;
  if(!es.length){ box.innerHTML='<div class="panel"><div class="empty">no engines reporting</div></div>'; return; }
  var tot=es.reduce(function(a,e){ return a+e.equity; },0);
  var base=es.reduce(function(a,e){return a+(e.start_equity||2000);},0)||1;
  if(note) note.textContent={
    value:es.length+" engines · combined $"+money(tot)+" ("+((100*(tot/base-1))>=0?'+':'')+
          (100*(tot/base-1)).toFixed(2)+"%)"
  }.value;
  var arrow=function(k){ return LBSORT.key===k ? (LBSORT.dir<0?" \u25bc":" \u25b2") : ""; };
  var th=function(k,label){ return '<th class="sortable" onclick="lbSort(\''+k+'\')">'+
    label+arrow(k)+'</th>'; };
  box.innerHTML=podium(es)+'<table class="lbtable"><thead><tr>'+
    '<th>#</th>'+th("key","engine")+th("realised","realised")+th("equity","equity")+
    th("return","return")+th("unrealised","unrealised")+th("closed","closed")+
    th("win","win")+th("avgnet","avg net")+th("open","open")+
    '<th>equity curve</th></tr></thead><tbody>'+
    es.map(function(e,i){ return lbRow(e,i+1,best,worst); }).join('')+
    '</tbody></table>';
}

/* ================= MODEL REVIEW ================= */
let PATHS={};
function pctOr(v){ return v==null?"—":(v>=0?"+":"")+Number(v).toFixed(3)+"%"; }

function classify(t){
  const net=t.net_pct||0, peak=t.peak_pct||0, r=(t.reason||"");
  if(Math.abs(net+0.2)<0.06) return "FEE";
  if(r.startsWith("clock")||r.startsWith("time")) return "TIMEOUT";
  if(peak>=0.3 && net<=0) return "LEAKED";
  if(peak>0 && net<=-0.5) return "REVERSED";
  if(peak<=0.05) return "BAD_ENTRY";
  return "OTHER";
}
function scorecard(ts){
  const n=ts.length;
  const wins=ts.filter(t=>(t.net_pct||0)>0).length;
  const gross=ts.reduce((a,t)=>a+(t.gross_pct||0),0)/Math.max(1,n);
  const net=ts.reduce((a,t)=>a+(t.net_pct||0),0)/Math.max(1,n);
  const fee=gross!==0?Math.max(0,Math.min(1,1-(net/gross))):(net<0?1:0);
  const peaks=ts.filter(t=>(t.peak_pct||0)>0.02);
  const capture=peaks.length?peaks.reduce((a,t)=>a+((t.net_pct||0)/(t.peak_pct||1)),0)/peaks.length:null;
  const mfe=ts.reduce((a,t)=>a+(t.peak_pct||0),0)/Math.max(1,n);
  const held=ts.map(t=>t.held_s||0).sort((a,b)=>a-b);
  return {n, wr:n?100*wins/n:0, capture, mfe, fee:100*fee,
          held:held.length?held[Math.floor(held.length/2)]:0};
}
function quadrant(ts){
  const q={earned:0,leaked:0,bad:0,luck:0};
  ts.forEach(t=>{const peak=t.peak_pct||0, net=t.net_pct||0;
    if(peak>0.05&&net>0) q.earned++;
    else if(peak>0.05) q.leaked++;
    else if(net<0) q.bad++; else q.luck++;});
  return q;
}
function qbar(label,v,total,colour){
  const w=total?Math.min(100,(v/total)*100):0;
  return `<div class="skrow"><span class="sklab">${label}</span>
    <span class="skbar"><i style="width:${w.toFixed(0)}%;background:${colour}"></i></span>
    <span class="skval">${v}</span><span class="skmeta">${total?Math.round(100*v/total):0}%</span></div>`;
}
function scatter(ts, eng){
  if(ts.length<2) return '<div class="empty">need more trades</div>';
  const W=360,H=210,L=44,B=26,T=10,R=10;
  const peaks=ts.map(t=>Math.max(0.02,t.peak_pct||0));
  const nets=ts.map(t=>t.net_pct||0);
  const xmax=Math.max(0.5,Math.max(...peaks)*1.1);
  const nmin=Math.min(-0.4,Math.min(...nets)*1.15);
  const nmax=Math.max(0.4,Math.max(...nets)*1.15);
  const X=v=>L+(W-L-R)*Math.min(1,v/xmax);
  const Y=v=>H-B-(H-T-B)*((v-nmin)/(nmax-nmin));
  const zy=Y(0), tx=X(0.05);
  let g=`<rect x="${L}" y="${T}" width="${tx-L}" height="${zy-T}" fill="#3fb95010"/>
    <rect x="${tx}" y="${zy}" width="${W-R-tx}" height="${H-B-zy}" fill="#d2992220"/>
    <rect x="${L}" y="${zy}" width="${tx-L}" height="${H-B-zy}" fill="#f8514914"/>
    <line x1="${L}" y1="${zy.toFixed(1)}" x2="${W-R}" y2="${zy.toFixed(1)}" stroke="#2a3a4a"/>
    <line x1="${tx.toFixed(1)}" y1="${T}" x2="${tx.toFixed(1)}" y2="${H-B}" stroke="#2a3a4a"
          stroke-dasharray="3 3"/>`;
  ts.forEach(t=>{
    const k=classify(t);
    const c=k==="LEAKED"?"#d29922":k==="BAD_ENTRY"?"#f85149":k==="FEE"?"#58a6ff":"#3fb950";
    const r=Math.max(3,Math.min(9,Math.sqrt((t.notional||1000)/900)));
    g+=`<circle cx="${X(Math.max(0.02,t.peak_pct||0)).toFixed(1)}" cy="${Y(t.net_pct||0).toFixed(1)}"
          r="${r.toFixed(1)}" fill="${c}" opacity=".72" stroke="${c}"
          style="cursor:pointer" onclick="openChart('${esc(t.symbol)}','${esc(eng||"")}')">
          <title>${esc(t.symbol)} · peak ${pctOr(t.peak_pct)} · net ${pctOr(t.net_pct)} · ${esc(t.reason||"")} — click for the chart</title></circle>`;
  });
  g+=`<text x="${L}" y="${H-8}" fill="#4c5a6c" font-size="9">MFE →</text>
      <text x="4" y="${T+8}" fill="#4c5a6c" font-size="9">net</text>
      <text x="${((L+tx)/2).toFixed(0)}" y="${T+14}" fill="#3fb95088" font-size="8" text-anchor="middle">LUCK</text>
      <text x="${((tx+W-R)/2).toFixed(0)}" y="${H-B-6}" fill="#d29922aa" font-size="8" text-anchor="middle">LEAKED</text>
      <text x="${((L+tx)/2).toFixed(0)}" y="${H-B-6}" fill="#f8514988" font-size="8" text-anchor="middle">BAD ENTRY</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:210px">${g}</svg>`;
}
function shapeSVG(pts, entry, exit, peak){
  if(!pts||pts.length<3){
    if(entry==null||exit==null) return '<div class="mk">—</div>';
    var pk=entry*(1+(peak||0)/100);
    pts=[entry, pk, exit];          // entry -> best moment -> exit
  }
  const W=120,H=34, lo=Math.min(...pts), hi=Math.max(...pts);
  const y=v=>H-4-(H-8)*((v-lo)/((hi-lo)||1));
  const x=i=>2+(W-4)*(i/(pts.length-1));
  const pth=pts.map((v,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up=exit>=entry;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:118px;height:34px;display:block">
    <path d="${pth}" fill="none" stroke="${up?'#3fb950':'#f85149'}" stroke-width="1.2"/>
    <circle cx="2" cy="${y(entry).toFixed(1)}" r="2" fill="#58a6ff"/>
    <circle cx="${W-2}" cy="${y(exit).toFixed(1)}" r="2" fill="#e8eef6"/></svg>`;
}
function conditional(ts, get, bands){
  bands.forEach(b=>{b.n=0;b.w=0;});
  ts.forEach(t=>{const v=get(t); if(v==null) return;
    for(const b of bands){ if(v>=b.lo&&v<b.hi){ b.n++; if((t.net_pct||0)>0) b.w++; break; } }});
  if(!bands.some(b=>b.n)) return "";
  return `<div class="ccol"><div class="ctitle">by ${bands[0].what}</div>
    ${bands.map(b=>`<div class="skrow"><span class="sklab">${b.label}</span>
      <span class="skbar"><i style="width:${b.n?Math.round(100*b.w/b.n):0}%;background:#3fb950"></i></span>
      <span class="skval">${b.n?Math.round(100*b.w/b.n):0}%</span>
      <span class="skmeta">n=${b.n}</span></div>`).join("")}</div>`;
}
function reviewCard(e){
  const ts=e.trades||[];
  if(!ts.length) return "";
  const sc=scorecard(ts), q=quadrant(ts);
  const mix={}; ts.forEach(t=>{const k=classify(t); mix[k]=(mix[k]||0)+1;});
  const rows=Object.keys(mix).sort((a,b)=>mix[b]-mix[a]);
  const worst=ts.slice().sort((a,b)=>(a.pnl_usd||0)-(b.pnl_usd||0)).slice(0,10);
  const conf=conditional(ts,t=>{const g=t.decision&&t.decision.gates; return g&&g.conf!=null?g.conf:null;},
    [{label:"<65%",lo:0,hi:0.65,what:"confidence"},{label:"65–80%",lo:0.65,hi:0.8,what:"confidence"},
     {label:"80–90%",lo:0.8,hi:0.9,what:"confidence"},{label:"90%+",lo:0.9,hi:2,what:"confidence"}]);
  const agr=conditional(ts,t=>{const v=t.decision&&t.decision.votes;
    return v?Object.values(v).filter(x=>x).length:null;},
    [{label:"1 model",lo:1,hi:1.5,what:"agreement"},{label:"2",lo:1.5,hi:2.5,what:"agreement"},
     {label:"3+",lo:2.5,hi:9,what:"agreement"}]);
  const vol=conditional(ts,t=>{const f=t.decision&&t.decision.features;
    return f&&f.atr_pct!=null?f.atr_pct:null;},
    [{label:"low <3%",lo:0,hi:3,what:"volatility"},{label:"mid",lo:3,hi:6,what:"volatility"},
     {label:"high 6%+",lo:6,hi:999,what:"volatility"}]);
  return `<div class="rcard">
    <div class="rchead"><span style="color:${e.colour}">${esc(e.key)}</span>
      <span style="margin-left:8px">${engSpark(e.key,72,16)}</span>
      <span class="mk">${ts.length} closed</span>
      <span class="${(e.return_pct||0)>=0?'up':'down'}" style="margin-left:auto;font-family:var(--mono)">
        ${pctOr(e.return_pct)}</span></div>
    <div class="score">
      <div class="sbox"><b>${sc.wr.toFixed(0)}%</b><span>hit rate</span></div>
      <div class="sbox"><b>${sc.capture==null?"—":sc.capture.toFixed(2)}</b><span>capture</span></div>
      <div class="sbox"><b>${sc.mfe.toFixed(2)}%</b><span>avg MFE</span></div>
      <div class="sbox"><b>${sc.fee.toFixed(0)}%</b><span>fee share</span></div>
      <div class="sbox"><b>${Math.round(sc.held)}s</b><span>hold</span></div>
    </div>
    <div class="rcols">
      <div class="ccol"><div class="ctitle">MFE vs captured — where it leaks</div>${scatter(ts,e.key)}</div>
      <div class="ccol"><div class="ctitle">decision quadrant</div>
        ${qbar("earned",q.earned,ts.length,"#3fb950")}
        ${qbar("leaked",q.leaked,ts.length,"#d29922")}
        ${qbar("bad entry",q.bad,ts.length,"#f85149")}
        ${qbar("luck",q.luck,ts.length,"#58a6ff")}
        <div class="ctitle" style="margin-top:12px">error mix</div>
        ${rows.map(k=>qbar(k,mix[k],ts.length,
          k==="FEE"?"#58a6ff":k==="LEAKED"?"#d29922":k==="BAD_ENTRY"?"#f85149":"#8494a8")).join("")}
      </div>
      ${conf||agr||vol?`<div class="ccol">${conf}${agr}${vol}</div>`:""}
    </div>
    <div class="ctitle" style="margin-top:14px">worst 10 — real path, entry ● → exit ●</div>
    <div class="wgrid">${worst.map(t=>`
      <div class="dna">
        <div class="dhead"><span class="tick" data-drill-sym=${esc(t.symbol)} data-sym="${esc(t.symbol)}" data-eng="${esc(e.key)}">${esc(t.symbol)}</span>
          <span class="side ${t.side==='buy'?'buy':'sell'}">${esc((t.side||'?').toUpperCase())}</span>
          <span class="mk">${esc(t.reason||"")}</span>
          <span class="${(t.net_pct||0)>0?'up':'down'}" style="margin-left:auto;font-family:var(--mono)">
            ${pctOr(t.net_pct)}</span></div>
        <div style="margin-top:6px">${shapeSVG(PATHS[t.symbol+"|"+(t.entry_ms||0)], t.entry, t.exit, t.peak_pct)}</div>
        <div class="dfacts"><span>peak <b>${pctOr(t.peak_pct)}</b></span>
          <span>held <b>${Math.round(t.held_s||0)}s</b></span>
          <span>${(t.pnl_usd||0)>=0?'+':''}$${money(t.pnl_usd||0)}</span></div>
        ${t.why?`<div class="dwhy">${esc(t.why)}</div>`:""}
        ${attribBars(t.decision)}
      </div>`).join("")}</div>
  </div>`;
}
async function loadPaths(ts){
  await Promise.all(ts.slice(0,10).filter(t=>t.entry_ms).map(async t=>{
    const k=t.symbol+"|"+(t.entry_ms||0);
    if(PATHS[k]) return;
    try{
      const d=await (await fetch(`/api/path?symbol=${t.symbol}&from=${t.entry_ms||0}&to=${t.exit_ms||Date.now()}`,{cache:"no-store"})).json();
      PATHS[k]=d.points||[];
    }catch(e){ PATHS[k]=[]; }
  }));
}
async function renderReview(){
  await ensureBooks();
  var all=tradesAll();
  var box=$("rbody");
  if(!box) return;
  if(!all.length){
    box.innerHTML=UI.panel("trade review",
      '<div class="empty">No closed trades yet — this fills in as positions close.</div>');
    return;
  }
  var worst=all.slice().sort(function(a,b){return (a.pnl_usd||0)-(b.pnl_usd||0);}).slice(0,12);
  var byReason={};
  all.forEach(function(t){ var r=t.reason||"unknown";
    byReason[r]=byReason[r]||{n:0,sum:0,wins:0};
    byReason[r].n++; byReason[r].sum+=(t.net_pct||0);
    if((t.net_pct||0)>0) byReason[r].wins++; });
  var wins=all.filter(function(t){return (t.net_pct||0)>0;}).length;
  var gross=all.reduce(function(a,t){return a+(t.gross_pct||0);},0)/all.length;
  var net=all.reduce(function(a,t){return a+(t.net_pct||0);},0)/all.length;

  var stats=[
    UI.stat("closed", all.length),
    UI.stat("win rate", (100*wins/all.length).toFixed(0)+"%", UI.colour(wins/all.length-0.5)),
    UI.stat("avg gross", UI.pct(gross,3), UI.colour(gross)),
    UI.stat("avg net", UI.pct(net,3), UI.colour(net)),
    UI.stat("avg hold", Math.round(all.reduce(function(a,t){return a+(t.held_s||0);},0)/all.length)+"s")
  ].join("");

  var reasonTbl=UI.table({key:"review-reason", cols:[
      {label:"exit reason",align:"left"},{label:"trades"},{label:"avg net %"},{label:"wins %"}],
    rows:Object.keys(byReason).sort(function(a,b){return byReason[b].n-byReason[a].n;}).map(function(r){
      var v=byReason[r];
      return [UI.esc(r), v.n, UI.pct(v.sum/v.n,3), (100*v.wins/v.n).toFixed(0)+"%"]; })});

  var worstTbl=UI.table({key:"review-worst", cols:[
      {label:"symbol",align:"left"},{label:"side",align:"left"},{label:"net %"},
      {label:"gross %"},{label:"held"},{label:"reason",align:"left"},{label:"engine",align:"left"}],
    rows:worst.map(function(t){ return [
      '<b class="tick" style="cursor:pointer" onclick="drillChart(\''+UI.esc(t.symbol)+'\',null)">'+UI.esc(t.symbol)+'</b>',
      UI.esc((t.side||"").toUpperCase()),
      UI.pct(t.net_pct,3), UI.pct(t.gross_pct,3),
      Math.round(t.held_s||0)+"s", UI.esc(t.reason||"—"), UI.esc(t.engine||"—")]; })});

  box.innerHTML=UI.panel("trade quality", '<div style="display:flex;gap:26px;flex-wrap:wrap">'+stats+'</div>',
      {note:all.length+" closed across "+new Set(all.map(function(t){return t.engine;})).size+" models"})+
    '<div style="display:grid;grid-template-columns:320px minmax(0,1fr);gap:12px;margin-top:12px">'+
      UI.panel("by exit reason", reasonTbl)+
      UI.panel("worst trades", worstTbl, {note:"click a symbol for its chart"})+
    '</div>';
}

function tradesAll(){
  const out=[];
  (LAST||[]).forEach(e=>(e.trades||[]).forEach(t=>out.push({...t, engine:e.key, colour:e.colour})));
  return out.sort((a,b)=>(a.exit_ms||0)-(b.exit_ms||0));
}
function pctOr(v){ return v==null?"—":(v>=0?"+":"")+Number(v).toFixed(3)+"%"; }

function attribBars(d){
  if(!d||!d.features) return '<div class="empty">no structured decision recorded</div>';
  const f=d.features, g=d.gates||{};
  const rows=[];
  const sc=(f.score!=null?Math.abs(f.score)/100:0);
  if(f.score!=null) rows.push(["alert score "+Math.round(f.score), sc, f.score>0?1:-1]);
  if(f.taker_buy_ratio!=null) rows.push(["taker buy "+(100*f.taker_buy_ratio).toFixed(0)+"%",
      Math.abs(f.taker_buy_ratio-0.5)*2, f.taker_buy_ratio>0.5?1:-1]);
  if(f.range_position!=null) rows.push(["range pos "+(100*f.range_position).toFixed(0)+"%",
      Math.abs(f.range_position-0.5)*2, f.range_position>0.5?1:-1]);
  if(f.burst_multiple!=null) rows.push(["flow burst "+Number(f.burst_multiple).toFixed(1)+"x",
      Math.min(1,Number(f.burst_multiple)/20), 1]);
  if(d.votes){ const n=Object.values(d.votes).filter(v=>v).length;
    rows.push(["model votes "+n, Math.min(1,n/5), n>=2?1:-1]); }
  if(g.liq_floor&&g.liquidity) rows.push(["liquidity vs floor",
      Math.min(1,g.liquidity/g.liq_floor/10), g.liquidity>=g.liq_floor?1:-1]);
  if(d.pcal!=null) rows.push(["calibrated p "+(100*d.pcal).toFixed(0)+"%", d.pcal, d.pcal>0.2?1:-1]);
  if(!rows.length) return '<div class="empty">no attributable features</div>';
  return `<div class="attr">${rows.map(([lab,v,sign])=>`
    <div class="arow"><span class="alab">${esc(lab)}</span>
      <span class="abar"><i style="width:${(v*100).toFixed(0)}%;background:${sign>0?'#3fb950':'#f85149'}"></i></span>
      <span class="aval ${sign>0?'up':'down'}">${(sign>0?'+':'−')}${(v).toFixed(2)}</span></div>`).join("")}</div>`;
}

function funnelView(){
  const es=(LAST||[]).filter(e=>e.funnel&&Object.keys(e.funnel).length);
  if(!es.length) return '<div class="empty">no gate counters published yet</div>';
  const STEPS=[["alerts","alerts received"],["liquidity","liquidity floor"],
               ["technical","technical confirm"],["model","model gate"],
               ["confidence","confidence floor"],["opened","opened"]];
  return es.map(e=>{
    const f=e.funnel;
    const total=Math.max(f.alerts||f.scanned||f.candidates||1,1);
    const rows=STEPS.filter(([k])=>f[k]!==undefined).map(([k,lab])=>{
      const v=f[k]||0, w=Math.min(100,(v/total)*100);
      return `<div class="skrow"><span class="sklab">${lab}</span>
        <span class="skbar"><i style="width:${w.toFixed(0)}%;background:${
          k==="opened"?"#3fb950":"#58a6ff"}"></i></span>
        <span class="skval">${v}</span>
        <span class="skmeta">${k==="opened"?"traded":"rejected here"}</span></div>`;
    }).join("");
    return `<div class="fcard"><div class="fhead" style="color:${e.colour}">${esc(e.key)}</div>${rows}</div>`;
  }).join("");
}
function sankey(trades){
  const by={};
  trades.forEach(t=>{ const k=t.reason||"?";
    by[k]=by[k]||{n:0,pnl:0,wins:0};
    by[k].n++; by[k].pnl+=(t.pnl_usd||0); if((t.net_pct||0)>0) by[k].wins++; });
  const keys=Object.keys(by).sort((a,b)=>by[a].pnl-by[b].pnl);
  if(!keys.length) return '<div class="empty">no closed trades yet</div>';
  const max=Math.max(...keys.map(k=>Math.abs(by[k].pnl)))||1;
  return `<div class="sk">${keys.map(k=>{const v=by[k]; const w=Math.abs(v.pnl)/max*100;
    return `<div class="skrow"><span class="sklab">${esc(k)}</span>
      <span class="skbar"><i style="width:${w.toFixed(0)}%;background:${v.pnl>=0?'#3fb950':'#f85149'}"></i></span>
      <span class="skval ${v.pnl>=0?'up':'down'}">${v.pnl>=0?'+':''}$${money(v.pnl)}</span>
      <span class="skmeta">${v.n}× · ${Math.round(100*v.wins/v.n)}% win</span></div>`;}).join("")}</div>`;
}

function calibration(trades){
  const pts=trades.filter(t=>t.decision&&t.decision.pcal!=null);
  if(pts.length<3) return '<div class="empty">need a few trades with calibrated probability</div>';
  const W=520,H=220,P=34;
  const px=p=>P+(W-2*P)*Math.min(1,Math.max(0,p));
  const py=w=>H-P-(H-2*P)*Math.min(1,Math.max(0,w));
  let g=`<line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="#24313e"/>
         <line x1="${P}" y1="${P}" x2="${P}" y2="${H-P}" stroke="#24313e"/>
         <line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${P}" stroke="#2f4a66" stroke-dasharray="4 4"/>`;
  pts.forEach(t=>{
    const x=px(t.decision.pcal), y=py((t.net_pct||0)>0?1:0);
    g+=`<circle cx="${x.toFixed(1)}" cy="${(y+(Math.random()*10-5)).toFixed(1)}" r="4"
          fill="${(t.net_pct||0)>0?'#3fb950':'#f85149'}" opacity=".85">
          <title>${esc(t.engine)} ${esc(t.symbol)} p=${(100*t.decision.pcal).toFixed(0)}% net ${pctOr(t.net_pct)}</title></circle>`;
  });
  g+=`<text x="${P}" y="${H-10}" fill="#59677a" font-size="10">predicted probability →</text>
      <text x="${P-6}" y="${P+4}" fill="#59677a" font-size="10" text-anchor="end">win</text>
      <text x="${P-6}" y="${H-P}" fill="#59677a" font-size="10" text-anchor="end">loss</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:560px;height:220px">${g}</svg>
          <div class="mk">${pts.length} trades with a calibrated probability · dashed line = perfect calibration</div>`;
}

function ribbon(){
  const sym=($("wsym").textContent||"").toUpperCase();
  const al=(RIBBON||{}).models||[]; const nl=(RIBBON||{}).alerts||[];
  if(!al.length&&!nl.length) return '<div class="empty">no signals recorded for this symbol</div>';
  const lo=Math.min(...[...al.map(x=>x.at_ms||0),...nl.map(x=>x.ts_ms||0)].filter(Boolean));
  const hi=Math.max(...[...al.map(x=>x.at_ms||0),...nl.map(x=>x.ts_ms||0)].filter(Boolean));
  const BINS=36, span=Math.max(1,hi-lo);
  const models=[...new Set(al.map(x=>x.model))];
  const cell=(v)=>v>0?'#3fb950':v<0?'#f85149':'#18202b';
  let rows=models.map(m=>{
    let cells="";
    for(let b=0;b<BINS;b++){
      const t0=lo+span*b/BINS, t1=lo+span*(b+1)/BINS;
      const hit=al.filter(x=>x.model===m&&x.at_ms>=t0&&x.at_ms<t1);
      const v=hit.reduce((a,x)=>a+((x.direction||"").includes("BUY")||(x.direction||"").includes("PUMP")?1:-1),0);
      cells+=`<i style="background:${cell(v)}" title="${esc(m)} ${new Date(t0).toLocaleTimeString()}"></i>`;
    }
    return `<div class="rrow"><span class="rlab">${esc(m)}</span><span class="rcells">${cells}</span></div>`;
  }).join("");
  let acells="";
  for(let b=0;b<BINS;b++){
    const t0=lo+span*b/BINS, t1=lo+span*(b+1)/BINS;
    const hit=nl.filter(x=>x.ts_ms>=t0&&x.ts_ms<t1);
    const v=hit.reduce((a,x)=>a+(x.kind==="PUMP"?1:-1),0);
    acells+=`<i style="background:${hit.length?cell(v):'#121a24'}" title="${hit.length} alert(s)"></i>`;
  }
  return `<div class="rrow"><span class="rlab">alerts</span><span class="rcells">${acells}</span></div>${rows}
    <div class="mk">each column is a time bucket · green = long vote, red = short, dark = silent</div>`;
}

function dnaCards(trades, sym){
  const mine=trades.filter(t=>sym?t.symbol===sym:true).slice(-8).reverse();
  if(!mine.length) return '<div class="empty">no trades to explain yet</div>';
  return mine.map(t=>`
    <div class="dna">
      <div class="dhead">
        <span class="tick" data-drill-sym=${esc(t.symbol)} data-sym="${esc(t.symbol)}">${esc(t.symbol)}</span>
        <span class="side ${t.side==='buy'?'buy':'sell'}">${esc((t.side||'?').toUpperCase())}</span>
        <span style="color:${t.colour};font-size:10.5px">${esc(t.engine)}</span>
        <span class="${ (t.net_pct||0)>0?'up':'down'}" style="margin-left:auto;font-family:var(--mono)">
          ${pctOr(t.net_pct)} · ${t.pnl_usd>=0?'+':''}$${money(t.pnl_usd||0)}</span>
      </div>
      <div class="dfacts">
        <span>entry <b>${t.entry==null?"—":Number(t.entry).toPrecision(8)}</b></span>
        <span>exit <b>${t.exit==null?"—":Number(t.exit).toPrecision(8)}</b></span>
        <span>peak <b>${pctOr(t.peak_pct)}</b></span>
        <span>held <b>${Math.round(t.held_s||0)}s</b></span>
        <span>closed by <b>${esc(t.reason||"—")}</b></span>
      </div>
      ${t.why?`<div class="dwhy">${esc(t.why)}</div>`:""}
      ${attribBars(t.decision)}
    </div>`).join("");
}

async function ensureBooks(){
  if(LAST && LAST.length) return LAST;
  try{
    var d=await (await fetch("/api/all",{cache:"no-store"})).json();
    LAST=(d.engines||[]).filter(function(e){return e.equity!=null;});
  }catch(e){ LAST=LAST||[]; }
  return LAST;
}

async function renderWhy(){
  var sym=(window.SELECTED_SYM||"BTCUSDT").toUpperCase();
  if(typeof $("wsym")!=="undefined" && $("wsym")) $("wsym").textContent=sym;
  await ensureBooks();
  var trades=tradesAll();
  var sig={};
  try{ sig=await (await fetch("/api/signals?symbol="+sym,{cache:"no-store"})).json(); }catch(e){}
  RIBBON=sig||{};
  var alerts=(sig.alerts||[]).slice(0,10);
  var ext={};
  try{
    var e=await (await fetch("/api/signals_ext",{cache:"no-store"})).json();
    (e.rows||[]).forEach(function(r){ ext[r.symbol]=r; });
  }catch(err){}
  var x=ext[sym]||{};

  if($("wnote")) $("wnote").textContent=trades.length+" closed trades · "+alerts.length+" alerts for "+sym;

  var decided=UI.panel("the decision for "+sym, [
    UI.kv("price vs SMA50", x.px_vs_sma50==null?"—":UI.pct(x.px_vs_sma50,2), UI.colour(x.px_vs_sma50)),
    UI.kv("price vs SMA200", x.px_vs_sma200==null?"—":UI.pct(x.px_vs_sma200,2), UI.colour(x.px_vs_sma200)),
    UI.kv("book imbalance", x.book_imbalance==null?"—":x.book_imbalance.toFixed(2), UI.colour(x.book_imbalance)),
    UI.kv("taker buy share", x.buy_ratio==null?"—":(100*x.buy_ratio).toFixed(1)+"%", UI.colour((x.buy_ratio||0)-0.5)),
    UI.kv("open interest 1h", x.oi_change_1h_pct==null?"—":UI.pct(x.oi_change_1h_pct,2), UI.colour(x.oi_change_1h_pct)),
    UI.kv("top trader vs retail", x.smart_vs_dumb==null?"—":x.smart_vs_dumb.toFixed(2)+"x", UI.colour((x.smart_vs_dumb||1)-1)),
    UI.kv("funding", x.funding_pct==null?"—":(100*x.funding_pct).toFixed(3)+"%", "#9fb0c4"),
    UI.kv("spread", x.spread_bps==null?"—":x.spread_bps.toFixed(1)+"bp", "#9fb0c4"),
    UI.kv("$5k slippage", x.slippage_bps_5k==null?"—":x.slippage_bps_5k.toFixed(1)+"bp", "#9fb0c4"),
    UI.kv("flow confidence", x.flow_conf==null?"—":(x.flow_conf>0?"+":"")+x.flow_conf.toFixed(0), UI.colour(x.flow_conf)),
    UI.kv("composite score", x.score==null?"—":(x.score>0?"+":"")+x.score.toFixed(1), UI.colour(x.score))
  ].join(""), {note:"live signal fields"});

  var alertsTbl=UI.table({key:"why-alerts", cols:[
      {label:"time",align:"left"},{label:"kind",align:"left"},{label:"detector",align:"left"},
      {label:"score"},{label:"move %"}],
    rows:alerts.map(function(a){ return [
      UI.time(a.ts_ms), UI.esc(a.kind||""), UI.esc(a.detector||""),
      a.score==null?"—":Number(a.score).toFixed(0),
      a.move_pct==null?"—":UI.pct(a.move_pct,2)]; })});

  var funnel=UI.panel("where candidates die", [
    UI.kv("alerts seen for this symbol", alerts.length),
    UI.kv("trades taken (all models)", trades.length),
    UI.kv("closed with a recorded decision", trades.filter(function(t){return t.decision;}).length),
    UI.kv("blocked by risk governor",
      (function(){ try{ return (window.__blocks||0); }catch(e){ return 0; } })())
  ].join(""), {note:"counts from live books"});

  $("wbody").innerHTML =
    '<div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px">'+
      decided+funnel+'</div>'+
    '<div style="margin-top:12px">'+UI.panel("alert ribbon — "+sym, alertsTbl, {note:"newest first"})+'</div>'+
    '<div style="margin-top:12px">'+UI.panel("trade path", symbolSpark(sym,120,26)||"", {note:"1m candles from the stream"})+'</div>';
}

function chartKeyHandler(e){
  var box=$("chartbox");
  if(!box || !box.classList.contains("open")) return;
  if(e.key==="+"||e.key==="="){ zoomStep(0.7); e.preventDefault(); }
  else if(e.key==="-"){ zoomStep(1.4); e.preventDefault(); }
  else if(e.key==="0"){ resetView(); e.preventDefault(); }
  else if(e.key==="y"||e.key==="Y"){ resetY(); e.preventDefault(); }
  else if(e.key==="Escape"){ closeChart(); }
}
document.addEventListener("keydown", chartKeyHandler);

function closeChart(){ const b=$("chartbox"); if(b) b.classList.remove("open"); }

function tickerMap(){
  const map=new Map();
  (LAST||[]).forEach(e=>{
    const add=(sym,kind)=>{
      if(!sym) return;
      if(!map.has(sym)) map.set(sym,{fills:0,engines:new Map()});
      const m=map.get(sym);
      m.fills++;
      m.engines.set(e.key,(m.engines.get(e.key)||0)+1);
    };
    (e.trades||[]).forEach(t=>add(t.symbol));
    (e.positions||[]).forEach(p=>add(p.symbol));
  });
  return map;
}

async function zoomToSignal(sym){
  let ts=null;
  try{
    const d=await (await fetch(`/api/signals?symbol=${sym}`,{cache:"no-store"})).json();
    const t=[...(d.alerts||[]).map(a=>a.ts_ms),...(d.models||[]).map(m=>m.at_ms)]
              .filter(Boolean);
    if(t.length) ts=Math.max(...t);
  }catch(e){}
  PENDING_TS=ts;
  openChart(sym, null);
}
function renderTickerList(){
  const map=tickerMap();
  const q=($("msearch")?.value||"").trim().toUpperCase();

  // union: everything the Go backend tracks, plus anything already traded
  const vol=new Map(TICKERS.map(t=>[t.s,t.v]));
  let syms=new Set(TICKERS.map(t=>t.s));
  map.forEach((_,k)=>syms.add(k));
  syms=[...syms];
  if(q) syms=syms.filter(s=>s.includes(q));
  syms.sort((a,b)=>{
    const fa=map.get(a)?.fills||0, fb=map.get(b)?.fills||0;
    if(fb!==fa) return fb-fa;
    return (vol.get(b)||0)-(vol.get(a)||0);
  });
  const shown=syms.slice(0,140);
  if(!shown.length){
    $("mlist").innerHTML=`<div class="mempty">no symbol matches “${esc(q)}”</div>`;
    return;
  }
  const cur=($("msym").value||"").toUpperCase();
  $("mlist").innerHTML=shown.map(sym=>{
    const m=map.get(sym);
    const dots=m?[...m.engines.keys()].slice(0,5).map(k=>{
      const col=(LAST||[]).find(e=>e.key===k)?.colour||"#8494a8";
      return `<i class="sdot" style="background:${col}"></i>`;
    }).join(""):"";
    const v=vol.get(sym);
    return `<div class="mrow ${sym===cur?'on':''}" onclick="selectTicker('${esc(sym)}')">
      <span class="tick" data-drill-sym=${esc(sym)} data-sym="${esc(sym)}">${esc(sym)}</span>
      <span class="dots">${dots||`<span class="n">${v?("$"+(v/1e6).toFixed(0)+"M"):""}</span>`}</span>
      <span class="n">${m?m.fills:""}</span></div>`;
  }).join("") + (syms.length>shown.length
    ? `<div class="mempty">+${syms.length-shown.length} more — refine the search</div>` : "");
}

function toggleSide(){
  const lay=document.querySelector(".mlayout");
  const btn=$("sidetoggle");
  lay.classList.toggle("collapsed");
  const off=lay.classList.contains("collapsed");
  btn.textContent = off ? "▶ tickers" : "◀ tickers";
  try{ localStorage.setItem("sideCollapsed", off?"1":"0"); }catch(e){}
  if(!off){ renderTickerList(); }
  drawMarket();
}
function restoreSide(){
  let off=false;
  try{ off=localStorage.getItem("sideCollapsed")==="1"; }catch(e){}
  if(off){
    document.querySelector(".mlayout").classList.add("collapsed");
    const b=$("sidetoggle"); if(b) b.textContent="▶ tickers";
  }
}
function clearContext(){ M.context=null; drawMarket(); }
function selectTicker(sym){ if(sym) openChart(sym, null); }


window.addEventListener("error", function(e){
  var b=$("err");
  if(b){ b.style.display="block"; b.textContent="JS error: "+(e.message||"")+" @ line "+(e.lineno||"?"); }
});

async function health(){
  var el=$("hz"); if(!el) return;
  try{
    var r=await fetch("/healthz",{cache:"no-store"});
    var d=await r.json();
    var c=d.checks||{};
    var bad=Object.keys(c).filter(function(k){ return !c[k].ok; });
    el.textContent = bad.length ? ("health \u2717 "+bad.join(",")) : "health \u2713";
    el.className = "pill " + (bad.length ? "bad" : "ok");
  }catch(e){
    el.textContent="health \u2717"; el.className="pill bad";
  }
}

async function toggleTrading(){
  var b=$("tradebtn"); if(!b) return;
  var on=/start/.test(b.textContent);
  b.disabled=true; b.textContent=on?"starting…":"stopping…";
  try{
    await fetch("/api/trading?action="+(on?"start":"stop"),{method:"POST"});
  }catch(e){}
  setTimeout(function(){ b.disabled=false; refreshTrading(); }, on?2500:1200);
}
async function refreshTrading(){
  var b=$("tradebtn"); if(!b) return;
  try{
    var d=await (await fetch("/api/trading",{cache:"no-store"})).json();
    var alive=d.alive||{};
    var n=Object.keys(alive).filter(function(k){return alive[k];}).length;
    b.textContent=(n>0?"⏸ stop trading":"▶ start trading")+(n?" · "+n+" running":"");
    b.className="tbtn "+(n>0?"on":"");
  }catch(e){ b.textContent="▶ start trading"; }
}
var TAPEDATA=[], TAPESORT={k:"t", dir:-1}, TOTEQ=0;
function tapeSort(col){
  if(TAPESORT.k===col) TAPESORT.dir*=-1;
  else { TAPESORT.k=col; TAPESORT.dir=(col==="t"||col==="why")?-1:1; }
  renderTape();
}
function paintHealth(h){
  var chip=$("hz");
  if(chip){
    var ok=h.up===h.total;
    chip.textContent="health "+(ok?"✓":(h.up+"/"+h.total));
    chip.style.color=ok?"#3fb950":"#f85149";
    chip.title=(h.services||[]).map(function(r){ return r.name+" "+r.ms+"ms"+(r.up?"":" DOWN"); }).join(" · ");
  }
  var box=$("healthbody");
  if(box && document.getElementById("health") && document.getElementById("health").classList.contains("on")){
    box.innerHTML=(h.services||[]).map(function(r){
      return '<div style="display:grid;grid-template-columns:140px 70px 90px 1fr;gap:10px;align-items:center;'+
        'font-family:var(--mono);font-size:11.5px;padding:3px 0;border-bottom:1px solid #131c26">'+
        '<b>'+esc(r.name)+'</b>'+
        '<span style="color:'+(r.up?"#3fb950":"#f85149")+'">'+(r.up?"up":"DOWN")+'</span>'+
        '<span style="color:#7c8b9d">'+r.ms+'ms</span>'+
        '<span style="color:#59677a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+
          esc(String(r.detail||"").slice(0,90))+'</span></div>';
    }).join("")+
    '<div class="mk" style="margin-top:8px" id="healthts">updated '+
      new Date(h.ts).toLocaleTimeString()+' · '+h.up+'/'+h.total+' up · worst '+h.worst_ms+'ms</div>';
  }
}

var BUS_RENDER={};
function busRender(view, fn, gap){
  gap = gap || 900;
  var now = Date.now();
  var last = BUS_RENDER[view] || 0;
  if(now - last < gap){
    if(BUS_RENDER["t:"+view]) return;
    BUS_RENDER["t:"+view] = setTimeout(function(){
      delete BUS_RENDER["t:"+view];
      var el=document.getElementById(view);
      if(el && el.classList.contains("on")){ BUS_RENDER[view]=Date.now(); try{ fn(); }catch(e){} }
    }, gap - (now - last));
    return;
  }
  var el=document.getElementById(view);
  if(!el || !el.classList.contains("on")) return;
  BUS_RENDER[view]=now;
  try{ fn(); }catch(e){}
}

function updateAttrCells(row, sg){
  var put=function(sel, txt, colour){
    var c=row.querySelector(sel);
    if(!c) return;
    c.textContent=txt;
    if(colour) c.style.color=colour;
  };
  if(sg.spread_bps!=null) put(".spread", Number(sg.spread_bps).toFixed(1), "#9fb0c4");
  if(sg.buy_ratio!=null) put(".buy", (100*sg.buy_ratio).toFixed(0)+"%",
                                  sg.buy_ratio>=0.5?"#3fb950":"#f85149");
  if(sg.oi_change_1h_pct!=null) put(".oi",
      (sg.oi_change_1h_pct>0?"+":"")+Number(sg.oi_change_1h_pct).toFixed(1)+"%",
      sg.oi_change_1h_pct>=0?"#3fb950":"#f85149");
  if(sg.funding_pct!=null) put(".fund", (100*sg.funding_pct).toFixed(3)+"%", "#9fb0c4");
  if(sg.score!=null) put(".score", (sg.score>0?"+":"")+Number(sg.score).toFixed(1),
                          sg.score>=0?"#3fb950":"#f85149");
}
function fadePrice(cell, up){
  if(!cell) return;
  cell.classList.remove("up-tint","down-tint");
  void cell.offsetWidth;                       /* restart the transition cleanly */
  cell.classList.add(up?"up-tint":"down-tint");
  clearTimeout(cell.__fade);
  cell.__fade=setTimeout(function(){
    cell.classList.remove("up-tint","down-tint");   /* CSS eases back to normal */
  }, 90);
}
var ML_PREV={}, ML_LAST={}, ML_TICKAGE=0, ML_LIVE={}, ML_ES=null, ML_HITS=0;
var ML_SERIES={}, ML_METRICS={}, ML_SERIES_TS=0, ML_SIG="", ML_MINI={};
function mlStream(){
  if(ML_ES || typeof EventSource==="undefined") return;
  try{
    ML_ES=new EventSource("/stream");
    ML_ES.onmessage=function(ev){
      try{
        var env=JSON.parse(ev.data);
        var topic=env.topic||"ticks";
        var d=env.data||env;
        var guard=0;
        while(d && d.data && d.p===undefined && d.symbol===undefined && guard++<3){ d=d.data; }
        if(topic==="model.fill" || topic==="model.pnl"){
          busRender("books", renderBooks, 400);
          busRender("tape", renderTape, 800);
          busRender("review", renderReview, 1500);
          return;
        }
        if(topic==="system.health" && d.services){
          try{ paintHealth(d); }catch(e){}
          return;
        }
        if(topic==="model.decision"){
          busRender("thinking", renderThinking, 1500);
          busRender("why", renderWhy, 2000);
          return;
        }
        if(topic==="intel.forecast"){
          busRender("forecast", renderForecast, 1500);
          return;
        }
        if(topic==="signals" || topic==="intel.signal"){
          busRender("signals", renderSignals, 1200);
          busRender("advisor", renderAdvisor, 1500);
          busRender("metrics", refreshMetrics, 5000);
          busRender("forecast", renderForecast, 4000);
          if(!d.p && !d.symbol){ /* nothing more */ } else { return; }
        }
        if(d.symbol && (topic==="intel.signal" || d.symbol)){
          var sg=d.symbol?d:(d.data||{});
          if(sg && sg.symbol){
            var row=document.querySelector('#mlbody tr[data-sym="'+sg.symbol+'"]');
            if(row) updateAttrCells(row, sg);
          }
          return;
        }
        if(d.p){ ML_LIVE=d.p; }
        if(d.m){ ML_MINI=d.m; }
        ML_TICKAGE=0; ML_HITS++;
        var rows=document.querySelectorAll("#mlbody tbody tr");
        if(!rows.length && document.getElementById("marketlive") &&
           document.getElementById("marketlive").classList.contains("on")){
          try{ renderMarketLive(); }catch(e){}
          rows=document.querySelectorAll("#mlbody tbody tr");
        }
        for(var i=0;i<rows.length;i++){
          var tr=rows[i], sym=tr.getAttribute("data-sym");
          var px=ML_LIVE[sym];
          if(px!=null){
            var cell=tr.querySelector(".px");
            if(cell && cell.getAttribute("data-px")!==String(px)){
              var prev=parseFloat(cell.getAttribute("data-prev")||px);
              cell.setAttribute("data-prev",String(px));
              cell.setAttribute("data-px",String(px));
              cell.textContent=Number(px).toPrecision(7);
              if(px!==prev) fadePrice(cell, px>prev);
              ML_LAST[sym]=Date.now();
            }
          }
          var mm=ML_MINI[sym];
          if(mm){
            var chg=tr.querySelector(".chg");
            if(chg){ chg.textContent=(mm.c>=0?"+":"")+Number(mm.c).toFixed(2)+"%";
                     chg.style.color=mm.c>=0?"#3fb950":"#f85149"; }
            var liq=tr.querySelector(".liq");
            if(liq && mm.q) liq.textContent="$"+(mm.q/1e6).toFixed(1)+"M";
          }
        }
      }catch(e){}
    };
  }catch(e){ ML_ES=null; }
}
function mlStop(){ if(ML_ES){ ML_ES.close(); ML_ES=null; } }

async function renderFlow(){
  var box=$("flowmap"); if(!box) return;
  var d={};
  try{ d=await (await fetch("/api/flows?top=14",{cache:"no-store"})).json(); }catch(e){ return; }
  var rows=d.rows||[];
  if(!rows.length){ box.innerHTML='<div class="mk">money flow unavailable</div>'; return; }
  var max=Math.max.apply(null,rows.map(function(r){return Math.abs(r.net_quote);}))||1;
  var head='<div style="display:flex;gap:14px;align-items:baseline;margin-bottom:8px">'+
    '<span style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#6b7c90">money flow — taker side, 30 min</span>'+
    '<span style="color:#3fb950">buying '+d.buying+'</span>'+
    '<span style="color:#f85149">selling '+d.selling+'</span></div>';
  var body=rows.map(function(r){
    var w=Math.round(100*Math.abs(r.net_quote)/max);
    var pos=r.net_quote>=0;
    return '<div class="flowrow"><b onclick="drillChart(\''+esc(r.symbol)+'\',null)">'+esc(r.symbol)+'</b>'+
      '<span class="fbar"><i style="width:'+w+'%;background:'+(pos?'#3fb950':'#f85149')+';'+
        (pos?'':'margin-left:auto')+'"></i></span>'+
      '<span style="text-align:right;color:'+(pos?'#3fb950':'#f85149')+'">'+
        (pos?'+':'')+'$'+Math.abs(r.net_quote/1000).toFixed(0)+'k</span>'+
      '<span style="text-align:right;color:#9fb0c4">'+(100*r.buy_ratio).toFixed(0)+'%</span>'+
      '<span style="color:'+(r.buy_ratio>0.53?'#3fb950':r.buy_ratio<0.47?'#f85149':'#7c8b9d')+'">'+
        esc(r.sentiment)+'</span></div>';
  }).join("");
  box.innerHTML=head+body;
}
async function renderMovers(){
  var box=$("movers"); if(!box) return;
  var d={};
  try{ d=await (await fetch("/api/movers?limit=8",{cache:"no-store"})).json(); }catch(e){ return; }
  var card=function(title,rows,colour,key){
    if(!rows||!rows.length) return '<div class="mvcard"><h4>'+title+'</h4><div class="mk">no data</div></div>';
    var max=Math.max.apply(null,rows.map(function(r){return key==="qv"?r.qv:Math.abs(r.pct);}))||1;
    return '<div class="mvcard"><h4>'+title+'</h4>'+rows.map(function(r){
      var v=key==="qv"?r.qv:Math.abs(r.pct);
      var pctTxt=(key==="qv"?"$"+(r.qv/1e6).toFixed(0)+"M":(r.pct>=0?"+":"")+r.pct.toFixed(1)+"%");
      return '<div class="mvrow"><b onclick="drillChart(\''+esc(r.s)+'\',null)">'+esc(r.s)+'</b>'+
        '<span class="mvbar"><i style="width:'+Math.round(100*v/max)+'%;background:'+colour+'"></i></span>'+
        '<span style="text-align:right;color:'+(key==="qv"?"#7c8b9d":(r.pct>=0?"#3fb950":"#f85149"))+'">'+pctTxt+'</span>'+
        '<span style="text-align:right;color:#e6edf6">'+(r.p<1?r.p.toPrecision(4):r.p.toLocaleString(undefined,{maximumFractionDigits:2}))+'</span></div>';
    }).join("")+'</div>';
  };
  box.innerHTML=card("most traded — 24h volume",d.most_traded,"#3d6a94","qv")+
    card("biggest gainers — 24h",d.gainers,"#3fb950","pct")+
    card("biggest losers — 24h",d.losers,"#f85149","pct");
}
async function renderMarketLive(){
  var body=$("mlbody"); if(!body) return;
  var t0=Date.now();
  var d={tickers:[]}, f={series:{}}, m={series:{}}, live={};
  try{ d=await (await fetch("/api/universe800",{cache:"no-store"})).json(); }catch(e){}
  f={series:ML_SERIES}; m={series:ML_METRICS};
  if(Date.now()-(ML_SERIES_TS||0) > 12000){
    ML_SERIES_TS=Date.now();
    fetch("/api/sparklines?n=24",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(j){ ML_SERIES=j.series||{}; ML_SERIES_TS=Date.now(); }).catch(function(){});
    fetch("/api/metrics?n=24",{cache:"no-store"}).then(function(r){return r.json();})
      .then(function(j){ ML_METRICS=j.series||{}; }).catch(function(){});
  }
  try{
    var syms=(d.tickers||[]).slice(0,40).map(function(x){return x.s;}).join(",");
    var tk=await (await fetch("/api/tick?syms="+syms,{cache:"no-store"})).json();
    live=tk.p||{}; ML_TICKAGE=tk.age_ms||0;
  }catch(e){}
  var px=(d.tickers||[]).slice(0,40).map(function(x){
    if(live[x.s]!=null) return Object.assign({},x,{price:live[x.s]});
    return x;
  });
  var sp=f.series||{}, mt=m.series||{}, X={};
  try{
    var xd=await (await fetch("/api/signals_ext",{cache:"no-store"})).json();
    (xd.rows||[]).forEach(function(r){ X[r.symbol]=r; });
  }catch(e){}
  var maxLiq=Math.max.apply(null,px.map(function(t){return t.v||0;}).concat([1]));
  if($("mlage")) $("mlage").textContent=ML_HITS+" stream pushes · "+new Date().toISOString().slice(0,19)+"Z";
  if($("mlcount")) $("mlcount").textContent=px.length+" symbols · "+Object.keys(sp).length+" series";
  var now=Date.now();
  var sig=px.map(function(t){return t.s;}).join(",");
  if(sig===ML_SIG && document.querySelector("#mlbody tbody tr")){
    px.forEach(function(t){
      var tr=document.querySelector('#mlbody tr[data-sym="'+t.s+'"]');
      if(!tr) return;
      var cell=tr.querySelector(".px");
      if(!cell || t.price==null) return;
      var prevPx=parseFloat(cell.getAttribute("data-px")||t.price);
      if(String(t.price)!==cell.getAttribute("data-px")){
        cell.setAttribute("data-px",String(t.price));
        cell.textContent=Number(t.price).toPrecision(7);
        if(t.price!==prevPx) fadePrice(cell, t.price>prevPx);
      }
      var chgCell=tr.children[3];
      if(chgCell && t.c!=null){
        chgCell.textContent=(t.c>=0?"+":"")+Number(t.c).toFixed(2)+"%";
        chgCell.style.color=t.c>=0?"#3fb950":"#f85149";
      }
      var ageCell=tr.children[tr.children.length-1];
      if(ageCell){
        var last=ML_LAST[t.s];
        var ago=last?Math.max(0,Math.round((Date.now()-last)/1000)):null;
        ageCell.textContent=(ago==null?"—":(ago===0?"now":ago+"s"));
        ageCell.style.color=(ago!=null&&ago<3)?"#3fb950":"#59677a";
      }
    });
    return;
  }
  ML_SIG=sig;
  var rows=px.map(function(t){
    var sym=t.s, prev=ML_PREV[sym], up=false, changed=false;
    if(prev!=null && t.price!=null && t.price!==prev){ changed=true; up=t.price>prev; ML_LAST[sym]=now; }
    var ago=ML_LAST[sym]?Math.max(0,Math.round((now-ML_LAST[sym])/1000)):null;
    if(t.price!=null) ML_PREV[sym]=t.price;
    var flash="";   /* fade is applied after paint, not baked into the markup */
    var chg=t.c||0;
    return '<tr data-sym="'+esc(sym)+'">'+
      '<td class="sym" onclick="drillChart(\''+esc(sym)+'\',null)">'+esc(sym)+'</td>'+
      '<td class="spark">'+symbolSpark(sym,96,20)+'</td>'+
      '<td class="px'+flash+'" data-px="'+(t.price!=null?t.price:"")+'" data-prev="'+(prev!=null?prev:"")+'">'+
        (t.price!=null?Number(t.price).toPrecision(7):"—")+'</td>'+
      '<td class="chg" style="color:'+(chg>=0?"#3fb950":"#f85149")+'">'+(chg>=0?"+":"")+Number(chg).toFixed(2)+'%</td>'+
      '<td class="liq">'+(t.v?("$"+(t.v/1e6).toFixed(1)+"M"):"—")+
        '<span class="liqbar"><i style="width:'+Math.round(100*(t.v||0)/maxLiq)+'%"></i></span></td>'+
      '<td class="vol">'+((mt[sym]&&mt[sym].volume&&mt[sym].volume.length)?mt[sym].volume[mt[sym].volume.length-1].toFixed(0):"—")+'</td>'+
      '<td class="trades" style="text-align:right;color:#7c8b9d">'+(t.n?t.n.toLocaleString():"—")+'</td>'+
      '<td class="volat" style="text-align:right;color:#e3b341">'+((mt[sym]&&mt[sym].risk&&mt[sym].risk.length)?mt[sym].risk[mt[sym].risk.length-1].toFixed(3)+"%":"—")+'</td>'+
      '<td class="spread" style="text-align:right;color:#9fb0c4">'+((X[sym]&&X[sym].spread_bps!=null)?X[sym].spread_bps.toFixed(1):"—")+'</td>'+
      '<td class="buy" style="text-align:right;color:'+(((X[sym]||{}).buy_ratio||0.5)>=0.5?"#3fb950":"#f85149")+'">'+
        ((X[sym]&&X[sym].buy_ratio!=null)?(100*X[sym].buy_ratio).toFixed(0)+"%":"—")+'</td>'+
      '<td class="oi" style="text-align:right;color:'+(((X[sym]||{}).oi_change_1h_pct||0)>=0?"#3fb950":"#f85149")+'">'+
        ((X[sym]&&X[sym].oi_change_1h_pct!=null)?(X[sym].oi_change_1h_pct>0?"+":"")+X[sym].oi_change_1h_pct.toFixed(1)+"%":"—")+'</td>'+
      '<td class="fund" style="text-align:right;color:#9fb0c4">'+((X[sym]&&X[sym].funding_pct!=null)?(100*X[sym].funding_pct).toFixed(3)+"%":"—")+'</td>'+
      '<td class="score" style="text-align:right;color:'+(((X[sym]||{}).score||0)>=0?"#3fb950":"#f85149")+'">'+
        ((X[sym]&&X[sym].score!=null)?(X[sym].score>0?"+":"")+X[sym].score.toFixed(1):"—")+'</td>'+
      '<td class="age" style="color:'+(ago!=null&&ago<3?"#3fb950":"#59677a")+'">'+
        (ago==null?"—":(ago===0?"now":ago+"s"))+'</td></tr>';
  }).join("");
  body.innerHTML='<div class="mlscroll"><table class="mltable"><thead><tr>'+
    '<th>symbol</th><th>price · 1m</th><th>last</th><th>24h</th>'+
    '<th>liquidity</th><th>vol</th><th>trades</th><th>volat %</th><th>spread</th>'+
    '<th>buy %</th><th>OI 1h</th><th>funding</th><th>score</th><th>updated</th></tr></thead><tbody>'+rows+
    '</tbody></table></div>';
}
async function renderForecast(){
  var box=$("fcbody"), note=$("fcnote"), st=$("fcstate");
  if(!box) return;
  box.innerHTML='<div class="hint" style="padding:10px">running the seven agents…</div>';

  var d={forecasts:[]}, state={};
  try{ d=await (await fetch("/api/forecast_matrix?n=10",{cache:"no-store"})).json(); }catch(e){}
  try{ state=await (await fetch("/api/forecast_state",{cache:"no-store"})).json(); }catch(e){}
  var fs=d.forecasts||[];

  // ---- agent league table
  var agents=Object.keys(state||{}).map(function(k){
    var v=state[k];
    return {agent:k, hits:v.hits||0, total:v.total||0, acc:v.accuracy||0};
  }).sort(function(a,b){ return b.acc-a.acc; });
  if(st){
    st.innerHTML = agents.length ? agents.map(function(a){
      var pct=Math.round(100*a.acc);
      return '<div style="display:grid;grid-template-columns:110px 130px 84px;gap:10px;align-items:center;'+
        'font-family:var(--mono);font-size:11.5px;padding:3px 0;border-bottom:1px solid #131c26">'+
        '<b>'+esc(a.agent)+'</b>'+
        UI.bar(a.acc*100,100,pct>=55?"#3fb950":pct>=45?"#d29922":"#f85149")+
        '<span style="color:#7c8b9d">'+a.hits+'/'+a.total+' · '+pct+'%</span></div>';
    }).join("") : '<div class="hint">no graded forecasts yet — accuracy appears after the first closed trade</div>';
  }
  if(note) note.textContent=fs.length+" forecasts · horizon 1h";

  if(!fs.length){ box.innerHTML='<div class="panel"><div class="empty">no forecasts yet</div></div>'; return; }

  // ---- headline
  var avg=function(fn){ return fs.length? fs.reduce(function(a,f){return a+fn(f);},0)/fs.length : 0; };
  var bull=fs.filter(function(f){return f.prob>=0.55;}).length;
  var bear=fs.filter(function(f){return f.prob<=0.45;}).length;
  var head='<div class="panel" style="padding:12px 14px;margin-bottom:12px">'+
    '<div style="display:flex;gap:26px;flex-wrap:wrap;align-items:center">'+
      UI.stat("forecasts", fs.length)+
      UI.stat("bullish", bull, "#3fb950")+
      UI.stat("bearish", bear, "#f85149")+
      UI.stat("avg probability", (100*avg(function(f){return f.prob;})).toFixed(0)+"%",
              avg(function(f){return f.prob;})>=0.5?"#3fb950":"#f85149")+
      UI.stat("avg expected move", avg(function(f){return f.move_pct;}).toFixed(3)+"%", "#e3b341")+
      UI.stat("avg confidence", (100*avg(function(f){return f.confidence;})).toFixed(0)+"%", "#9fb0c4")+
    '</div></div>';

  // ---- per-symbol cards with every agent's view
  var cards=fs.map(function(f){
    var col=f.prob>=0.55?"#3fb950":f.prob<=0.45?"#f85149":"#d29922";
    var views=(f.views||[]).map(function(v){
      var c=v.prob>=0.55?"#3fb950":v.prob<=0.45?"#f85149":"#7c8b9d";
      return '<div style="display:grid;grid-template-columns:86px 1fr 46px;gap:8px;align-items:center;'+
        'font-family:var(--mono);font-size:10.5px;padding:2px 0">'+
        '<span style="color:#9fb0c4">'+esc(v.agent)+'</span>'+
        UI.bar((v.prob-0.5)*200,100,c)+
        '<span style="text-align:right;color:'+c+'">'+v.prob.toFixed(2)+'</span></div>'+
        '<div style="font-size:9.5px;color:#59677a;margin:0 0 3px 94px">'+esc(v.note||"")+'</div>';
    }).join("");
    var spread=0;
    var probs=(f.views||[]).map(function(v){return v.prob;});
    if(probs.length>1){
      var m=probs.reduce(function(a,b){return a+b;},0)/probs.length;
      spread=Math.sqrt(probs.reduce(function(a,b){return a+(b-m)*(b-m);},0)/probs.length);
    }
    return '<div class="panel" style="padding:12px 14px;margin-bottom:10px">'+
      '<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">'+
        '<b style="cursor:pointer;font-size:13px" onclick="drillChart(\''+esc(f.symbol)+'\',null)">'+
          esc(f.symbol)+'</b>'+
        '<span class="mk">'+esc(f.side)+'</span>'+
        '<b style="color:'+col+';font-size:15px">'+(100*f.prob).toFixed(0)+'%</b>'+
        '<span class="mk">expected '+(f.move_pct||0).toFixed(3)+'%</span>'+
        '<span class="pill '+(f.action==="enter"?"ok":f.action==="liquidate"?"bad":"")+'">'+
          esc(f.action)+'</span>'+
        '<span style="flex:1"></span>'+
        '<span class="mk">bar age '+Math.round(f.bar_age_s||0)+'s</span>'+
        '<span class="mk">agents '+((f.confidence||0)*100).toFixed(0)+'% agreed · σ '+
          spread.toFixed(3)+'</span>'+
      '</div>'+ views +'</div>';
  }).join("");

  box.innerHTML=head+cards;
}
async function renderAgentPanel(){
  var el=$("advagents"); if(!el) return;
  var flagged=[];
  try{
    var ob=await (await fetch("/api/opportunities",{cache:"no-store"})).json();
    flagged=(ob.flagged||[]).slice(0,5);
  }catch(e){}
  if(!flagged.length){ el.innerHTML='<div class="mk">no flagged symbols right now</div>'; return; }
  var rows=[], agg={};
  for(var i=0;i<flagged.length;i++){
    var sym=flagged[i];
    try{
      var f=await (await fetch("/api/forecast?symbol="+encodeURIComponent(sym)+"&side=buy",{cache:"no-store"})).json();
      if(!f||f.error) continue;
      (f.views||[]).forEach(function(v){
        agg[v.agent]=agg[v.agent]||{sum:0,n:0};
        agg[v.agent].sum+=v.prob; agg[v.agent].n++;
      });
      rows.push('<div class="frow" style="grid-template-columns:92px repeat(7,44px) 70px">'+
        '<b style="cursor:pointer" onclick="drillChart(\''+esc(sym)+'\',null)">'+esc(sym)+'</b>'+
        (f.views||[]).map(function(v){
          var c=v.prob>=0.55?"#3fb950":v.prob<=0.45?"#f85149":"#4d5d70";
          return '<span title="'+esc(v.agent)+' '+v.prob.toFixed(2)+' — '+esc(v.note)+'" '+
            'style="color:'+c+';font-weight:700">'+v.prob.toFixed(2)+'</span>';
        }).join("")+
        '<span style="color:'+(f.prob>=0.55?"#3fb950":f.prob<=0.45?"#f85149":"#d29922")+
        ';font-weight:700">'+f.prob.toFixed(2)+'</span></div>');
    }catch(e){}
  }
  var head='<div class="frow" style="grid-template-columns:92px repeat(7,44px) 70px;color:#59677a;'+
    'font-size:9px;letter-spacing:.08em;text-transform:uppercase"><span>symbol</span>'+
    ['momentum','volume','volatility','reversion','ma','breakout','pressure']
      .map(function(n){return '<span>'+n.slice(0,6)+'</span>';}).join("")+
    '<span>blend</span></div>';
  var summary=Object.keys(agg).map(function(k){
    var a=agg[k], avg=a.sum/a.n;
    return '<span style="margin-right:14px">'+esc(k)+' <b style="color:'+
      (avg>=0.55?"#3fb950":avg<=0.45?"#f85149":"#d29922")+'">'+avg.toFixed(2)+'</b></span>';
  }).join("");
  el.innerHTML='<div class="ctitle">what the agents say — per symbol, then averaged</div>'+head+
    rows.join("")+'<div style="margin-top:8px;font-family:var(--mono);font-size:11px">'+summary+'</div>';
}
async function renderAdvisor(){
  var th=$("advthoughts"), bd=$("advboard"), note=$("advnote");
  try{
    var d=await (await fetch("/api/opportunities",{cache:"no-store"})).json();
    var ts=d.thoughts||[], rows=(d.opportunities||[]).slice();
    var ext={};
    try{ var e=await (await fetch("/api/signals_ext",{cache:"no-store"})).json();
         (e.rows||[]).forEach(function(r){ ext[r.symbol]=r; }); }catch(err){}
    if(note) note.textContent=rows.length+" ranked · "+(d.flagged||[]).length+" flagged · "+
      new Date(d.ts||Date.now()).toLocaleTimeString();

    // ---- reasoning stream
    var col={new:"#3fb950",up:"#3fb950",down:"#f85149",out:"#7c8b9d",flag:"#58a6ff"};
    th.innerHTML=(ts||[]).slice(-14).map(function(t){
      var c=col[t.kind]||"#7c8b9d";
      return '<div style="display:grid;grid-template-columns:54px 1fr;gap:10px;padding:3px 0;'+
        'border-bottom:1px solid #131c26;font-family:var(--mono);font-size:11px">'+
        '<span style="color:'+c+';font-weight:700">'+esc(t.kind)+'</span>'+
        '<span style="color:#9fb0c4;display:flex;align-items:center;gap:8px">'+
        (t.sym?symbolSpark(t.sym,50,13):'')+esc(t.msg||t.text||'')+'</span></div>';
    }).join("");

    // ---- ranked board, best choice first
    rows.sort(function(a,b){ return (b.score||0)-(a.score||0); });
    var why=function(r){
      var x=ext[r.symbol]||{}, bits=[];
      var im=x.book_imbalance, br=x.buy_ratio, oi=x.oi_change_1h_pct, sd=x.smart_vs_dumb;
      if(im!=null) bits.push((im>0.15?"bids stacked":"asks stacked")+" ("+(im>0?"+":"")+im.toFixed(2)+")");
      if(br!=null) bits.push("takers "+(100*br).toFixed(0)+"% buying");
      if(oi!=null) bits.push("OI "+(oi>0?"+":"")+oi.toFixed(1)+"% 1h");
      if(sd!=null) bits.push("top traders "+(sd>=1?"":"")+sd.toFixed(2)+"x retail");
      if(x.funding_pct!=null) bits.push("funding "+(100*x.funding_pct).toFixed(3)+"%");
      if(x.spread_bps!=null) bits.push("spread "+x.spread_bps.toFixed(1)+"bp");
      if(x.slippage_bps_5k!=null) bits.push("slip $5k "+x.slippage_bps_5k.toFixed(1)+"bp");
      if(x.flow_conf!=null) bits.push("flow conf "+(x.flow_conf>0?"+":"")+x.flow_conf.toFixed(0));
      return bits.join(" · ") || (r.why||"no signal explanation");
    };
    bd.innerHTML='<table class="wide"><thead><tr>'+
      '<th>#</th><th>symbol</th><th>chart</th><th style="text-align:right">score</th>'+
      '<th style="text-align:right">alert</th><th style="text-align:right">imbal</th>'+
      '<th style="text-align:right">buy</th><th style="text-align:right">OI 1h</th>'+
      '<th style="text-align:right">top/ret</th><th style="text-align:right">funding</th>'+
      '<th style="text-align:right">slip</th><th>why</th></tr></thead><tbody>'+
      rows.slice(0,24).map(function(r,i){
        var x=ext[r.symbol]||{}, sc=r.score||0;
        var c=sc>0.45?"#3fb950":sc>0.35?"#d29922":"#7c8b9d";
        return '<tr><td class="mini">'+(i+1)+'</td>'+
          '<td><b class="tick" style="cursor:pointer" onclick="drillChart(\''+esc(r.symbol)+'\',null)">'+
            esc(r.symbol)+'</b></td>'+
          '<td>'+symbolSpark(r.symbol,72,18)+'</td>'+
          '<td style="text-align:right"><b style="color:'+c+'">'+sc.toFixed(3)+'</b></td>'+
          '<td style="text-align:right;color:#9fb0c4">'+(r.alert==null?"—":Number(r.alert).toFixed(2))+'</td>'+
          '<td style="text-align:right;color:'+((x.book_imbalance||0)>=0?"#3fb950":"#f85149")+'">'+
            (x.book_imbalance==null?"—":(x.book_imbalance>0?"+":"")+x.book_imbalance.toFixed(2))+'</td>'+
          '<td style="text-align:right;color:'+((x.buy_ratio||0.5)>=0.5?"#3fb950":"#f85149")+'">'+
            (x.buy_ratio==null?"—":(100*x.buy_ratio).toFixed(0)+"%")+'</td>'+
          '<td style="text-align:right;color:'+((x.oi_change_1h_pct||0)>=0?"#3fb950":"#f85149")+'">'+
            (x.oi_change_1h_pct==null?"—":(x.oi_change_1h_pct>0?"+":"")+x.oi_change_1h_pct.toFixed(1)+"%")+'</td>'+
          '<td style="text-align:right;color:#9fb0c4">'+(x.smart_vs_dumb==null?"—":x.smart_vs_dumb.toFixed(2))+'</td>'+
          '<td style="text-align:right;color:#9fb0c4">'+(x.funding_pct==null?"—":(100*x.funding_pct).toFixed(3)+"%")+'</td>'+
          '<td style="text-align:right;color:#7c8b9d">'+(x.slippage_bps_5k==null?"—":x.slippage_bps_5k.toFixed(1))+'</td>'+
          '<td class="why2" style="color:#8fa3b8">'+esc(why(r))+'</td></tr>';
      }).join("")+'</tbody></table>';
  }catch(e){
    if(bd) bd.innerHTML='<div class="panel"><div class="empty">advisor data unavailable</div></div>';
  }
}

async function renderOpps(){
  var el=$("oppbox"); if(!el) return;
  try{
    var d=await (await fetch("/api/opportunities",{cache:"no-store"})).json();
    var rows=d.opportunities||[];
    if(!rows.length){ el.innerHTML='<div class="hint">agent warming up…</div>'; return; }
    el.innerHTML=rows.slice(0,10).map(function(r){
      var pct=Math.min(100,100*r.score/0.7);
      var col=r.score>0.45?"#3fb950":r.score>0.3?"#d29922":"#58a6ff";
      return '<div style="display:grid;grid-template-columns:92px 110px 1fr 70px 60px;gap:10px;'+
        'align-items:center;padding:4px 0;font-family:var(--mono);font-size:11.5px">'+
        '<span style="font-weight:700">'+esc(r.symbol)+'</span>'+
        '<span class="lbbar"><i style="width:'+pct.toFixed(0)+'%;background:'+col+'"></i></span>'+
        '<span style="color:#7c8b9d;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+
          esc(r.why)+'</span>'+
        '<span class="'+((r.chg||0)>=0?'up':'down')+'" style="text-align:right">'+
          ((r.chg||0)>=0?"+":"")+Number(r.chg||0).toFixed(1)+'%</span>'+
        '<span style="color:'+col+';text-align:right">'+r.score.toFixed(2)+'</span></div>';
    }).join("");
  }catch(e){ el.innerHTML=''; }
}
async function renderPending(){
  var el=$("pendingbox"); if(!el) return;
  try{
    var d=await (await fetch("/api/pending",{cache:"no-store"})).json();
    var p=(d.pending||[]).slice(0,10), win=d.confirm_secs||10;
    if(!p.length){ el.innerHTML='<div class="hint">no signals awaiting confirmation</div>'; return; }
    el.innerHTML=p.map(function(r){
      var pct=Math.max(0,Math.min(100,100*(1-r.left/win)));
      var col=r.left<=0?"#3fb950":"#d29922";
      return '<div style="display:grid;grid-template-columns:150px 90px 70px 1fr 60px;gap:10px;'+
        'align-items:center;padding:4px 0;font-family:var(--mono);font-size:11.5px">'+
        '<span style="color:#8095ab">'+esc(r.book)+'</span>'+
        '<span style="font-weight:700">'+esc(r.symbol)+'</span>'+
        '<span style="color:'+(r.side==="buy"?"#3fb950":"#f85149")+'">'+r.side.toUpperCase()+'</span>'+
        '<span class="lbbar"><i style="width:'+pct.toFixed(0)+'%;background:'+col+'"></i></span>'+
        '<span style="color:'+col+';text-align:right">'+(r.left>0?r.left.toFixed(1)+"s":"ready")+'</span>'+
        '</div>';
    }).join("");
  }catch(e){ el.innerHTML=''; }
}
var SPARKC={}, METRICS={}, METRICS_TS=0;
async function refreshMetrics(){
  try{
    var d=await (await fetch("/api/metrics?n=30",{cache:"no-store"})).json();
    METRICS=d.series||{}; METRICS_TS=d.ts||Date.now();
  }catch(e){}
}
function metricSpark(sym, metric, colour, w, h){
  var m=METRICS[sym]||{}, vals=m[metric]||[];
  if(vals.length<2) return '<span style="display:inline-block;width:'+(w||52)+'px;height:'+(h||11)+'px;border-bottom:1px solid #1a2430"></span>';
  var lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals), rng=(hi-lo)||1;
  var pts=vals.map(function(v,i){
    var x=(i/(vals.length-1))*(w||52);
    var y=(h||11)-1-((v-lo)/rng)*((h||11)-2);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(" ");
  return '<svg width="'+(w||52)+'" height="'+(h||11)+'" style="display:block">'+
    '<polyline points="'+pts+'" fill="none" stroke="'+colour+'" stroke-width="1.1"/></svg>';
}
function metricBlock(sym){
  var cols={price:"#58a6ff",volume:"#a371f7",risk:"#f85149",liquidity:"#3fb950",forecast:"#e3b341"};
  var labels=[["price","PRICE"],["volume","VOL"],["risk","RISK"],["liquidity","LIQ"],["forecast","FCST"]];
  return '<div class="mblock">'+labels.map(function(l){
    return '<div class="mcell"><span class="mlab">'+l[1]+'</span>'+
      metricSpark(sym,l[0],cols[l[0]],32,10)+'</div>';
  }).join("")+'</div>';
}
async function refreshSparks(){
  try{
    var d=await (await fetch("/api/sparklines?n=40",{cache:"no-store"})).json();
    SPARKC=d.series||{};
  }catch(e){}
}
function engSpark(key, w, h){
  return sparkSVG((HIST[key]||[]).slice(-40), w||62, h||15);
}
function symbolSpark(sym, w, h){
  return sparkSVG(SPARKC[sym], w||64, h||16);
}
function sparkSVG(vals, w, h){
  if(!vals || vals.length < 2){
    return '<span style="display:inline-block;width:'+(w||60)+'px;height:'+(h||16)+'px;'+
      'border-bottom:1px solid #22303d;color:#33414f;font-size:9px;line-height:'+(h||16)+'px">'+
      '</span>';
  }
  var lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals), rng=(hi-lo)||1;
  var pts=vals.map(function(v,i){
    var x=(i/(vals.length-1))*w;
    var y=h-1-((v-lo)/rng)*(h-2);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(" ");
  var up=vals[vals.length-1]>=vals[0];
  return '<svg width="'+w+'" height="'+h+'" style="display:block">'+
    '<polyline points="'+pts+'" fill="none" stroke="'+(up?'#3fb950':'#f85149')+
    '" stroke-width="1.3" stroke-linejoin="round"/></svg>';
}
async function renderExtSig(){
  var box=$("extsig"); if(!box) return;
  var d={};
  try{ d=await (await fetch("/api/signals_ext",{cache:"no-store"})).json(); }catch(e){ return; }
  var rows=d.rows||[];
  if(!rows.length){ box.innerHTML='<div class="mk">extended signals warming up</div>'; return; }
  var head='<div style="display:flex;gap:16px;margin-bottom:8px;font-size:10px;'+
    'letter-spacing:.13em;text-transform:uppercase;color:#6b7c90">'+
    '<span>extended signals — book, derivatives, flow</span>'+
    '<span style="color:#3fb950">'+(rows.filter(function(r){return r.score>0;}).length)+' positive</span>'+
    '<span style="color:#f85149">'+(rows.filter(function(r){return r.score<0;}).length)+' negative</span></div>';
  var body=rows.map(function(r){
    var sc=r.score||0, col=sc>15?"#3fb950":sc<-15?"#f85149":"#d29922";
    var w=Math.min(100,Math.abs(sc));
    return '<div class="extrow"><b onclick="drillChart(\''+esc(r.symbol)+'\',null)">'+esc(r.symbol)+'</b>'+
      '<span class="ebar"><i style="width:'+w+'%;background:'+col+';'+(sc<0?'margin-left:auto':'')+'"></i></span>'+
      '<span style="text-align:right;color:'+col+';font-weight:700">'+(sc>0?'+':'')+sc.toFixed(1)+'</span>'+
      '<span style="text-align:right;color:'+((r.book_imbalance||0)>=0?'#3fb950':'#f85149')+'">'+
        ((r.book_imbalance||0)).toFixed(2)+'</span>'+
      '<span style="text-align:right;color:#9fb0c4">'+((r.buy_ratio||0)*100).toFixed(0)+'%</span>'+
      '<span style="text-align:right;color:'+((r.oi_change_1h_pct||0)>=0?'#3fb950':'#f85149')+'">'+
        ((r.oi_change_1h_pct||0).toFixed(1))+'%</span>'+
      '<span style="text-align:right;color:'+((r.smart_vs_dumb||1)>=1?'#3fb950':'#f85149')+'">'+
        ((r.smart_vs_dumb||0)).toFixed(2)+'</span>'+
      '<span style="text-align:right;color:#7c8b9d">'+((r.slippage_bps_5k||0)).toFixed(1)+'bp</span></div>';
  }).join("");
  box.innerHTML=head+'<div class="extrow" style="color:#59677a;font-size:9px"><span>symbol</span><span>score</span>'+
    '<span style="text-align:right">score</span><span style="text-align:right">imbal</span>'+
    '<span style="text-align:right">buy</span><span style="text-align:right">OI 1h</span>'+
    '<span style="text-align:right">top/ret</span><span style="text-align:right">slip</span></div>'+body;
}
async function showAlert(sym, eid){
  var d={};
  try{ d=await (await fetch("/api/alert_detail?symbol="+encodeURIComponent(sym)+
        "&event_id="+encodeURIComponent(eid||""),{cache:"no-store"})).json(); }catch(e){}
  var a=d.alert||{}, sig=d.signals||{}, fc=d.forecast||{};
  var reasons=(a.reasons||[]).map(function(r){ return '<li>'+esc(r)+'</li>'; }).join("");
  var comps=Object.keys(a.components||{}).map(function(k){
    var v=a.components[k];
    return UI.kv(k, (typeof v==="number"? v.toFixed(2):String(v)),
                 typeof v==="number" ? (v>=0?"#3fb950":"#f85149") : "#e6edf6");
  }).join("");
  var metrics=Object.keys(a.metrics||{}).map(function(k){
    var v=a.metrics[k];
    return UI.kv(k, typeof v==="object"? JSON.stringify(v).slice(0,60) : String(v));
  }).join("");
  var flowTbl=(d.flow||[]).length ? UI.table({key:"ad-flow", cols:[
      {label:"window",align:"left"},{label:"net flow"},{label:"buy %"},{label:"change"}],
      rows:(d.flow||[]).map(function(w){
        return [w.window+"m", (w.net>=0?"+":"")+"$"+Math.abs(w.net/1000).toFixed(0)+"k",
                (100*w.buy_ratio).toFixed(1)+"%",
                (w.chg_pct==null?"—":(w.chg_pct>0?"+":"")+w.chg_pct.toFixed(2)+"%")]; })}) :
    '<div class="mk">no candle data for this symbol yet</div>';
  var body =
    UI.panel("why this triggered", 
      '<div style="font-size:12px;color:#c9d6e3;margin-bottom:8px">'+esc(a.summary||"—")+'</div>'+
      '<ul style="margin:0 0 10px 18px;color:#9fb0c4;font-size:11.5px;line-height:1.6">'+reasons+'</ul>'+
      '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px">'+
        '<div>'+UI.kv("detector",esc(a.detector||"—"))+UI.kv("kind",esc(a.kind||"—"))+
          UI.kv("score",(a.score||0).toFixed(1), "#d29922")+
          UI.kv("severity",esc(a.severity||"—"))+UI.kv("price",a.price==null?"—":a.price)+
          UI.kv("time",UI.time(a.ts_ms))+'</div>'+
        '<div>'+comps+'</div></div>', {note:"components are the score contributions"})+
    UI.panel("market context",
      '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px">'+
      '<div>'+(sig.symbol? [
          UI.kv("book imbalance",(sig.book_imbalance==null?"—":sig.book_imbalance.toFixed(2))),
          UI.kv("taker buy",(sig.buy_ratio==null?"—":(100*sig.buy_ratio).toFixed(1)+"%")),
          UI.kv("OI 1h",(sig.oi_change_1h_pct==null?"—":sig.oi_change_1h_pct.toFixed(2)+"%")),
          UI.kv("top vs retail",(sig.smart_vs_dumb==null?"—":sig.smart_vs_dumb.toFixed(2)+"x")),
          UI.kv("spread",(sig.spread_bps==null?"—":sig.spread_bps.toFixed(1)+"bp")),
          UI.kv("flow confidence",(sig.flow_conf==null?"—":sig.flow_conf.toFixed(0)))
        ].join("") : '<div class="mk">extended signals not covering this symbol</div>')+'</div>'+
      '<div>'+(fc.prob!=null ? [
          UI.kv("forecast probability",(100*fc.prob).toFixed(0)+"%"),
          UI.kv("expected move",(fc.move_pct||0).toFixed(3)+"%"),
          UI.kv("forecast action",esc(fc.action||"—"))
        ].join("") : '<div class="mk">forecast unavailable</div>')+'</div></div>', {note:"what the models see right now"})+
    UI.panel("money flow around the trigger", flowTbl, {note:"windows ending now"});
  var m=$("modal");
  $("sheet").innerHTML='<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'+
    '<h3 style="margin:0">'+esc(sym)+' '+esc(a.kind||"")+'</h3>'+
    '<span class="pill">'+(a.event_id||"").slice(0,8)+'</span>'+
    '<span style="flex:1"></span><button class="tbtn" onclick="closeModal()">close</button></div>'+body;
  if(m) m.classList.add("open");
}
async function renderSignals(){
  var box=$("signalsbody"); if(!box) return;
  var tick, alerts, votes;
  try{ tick=(await (await fetch("/api/universe800",{cache:"no-store"})).json()).tickers||[]; }catch(e){ tick=[]; }
  // overlay the 1-second tick book so prices are live, not 120s cached
  try{
    var sy=tick.slice(0,30).map(function(x){return x.s;}).join(",");
    var tkb=await (await fetch("/api/tick?syms="+sy,{cache:"no-store"})).json();
    var live=tkb.p||{};
    tick=tick.map(function(x){
      if(live[x.s]!=null) return Object.assign({},x,{price:live[x.s]});
      return x;
    });
    window.__TICKAGE=tkb.age_ms||0;
  }catch(e){}
  var sparks=SPARKC;
  try{ alerts=(await (await fetch("/api/alerts?limit=200",{cache:"no-store"})).json()).alerts||[]; }catch(e){ alerts=[]; }
  try{ votes=(await (await fetch("/api/votes",{cache:"no-store"})).json()).signals||[]; }catch(e){ votes=[]; }
  var amap={}, vmap={};
  alerts.forEach(function(a){ if(a.symbol) amap[a.symbol]=a; });
  votes.forEach(function(v){ vmap[v.symbol]=(vmap[v.symbol]||0)+((v.direction||"").indexOf("BUY")>=0||(v.direction||"").indexOf("PUMP")>=0?1:-1); });
  if($("signote")){
    var newest=alerts.length?Math.round((Date.now()-(alerts[0].ts_ms||0))/1000):null;
    $("signote").textContent=tick.length+" symbols · "+alerts.length+" alerts"+
      (newest!=null?(" (newest "+newest+"s)"):"")+" · "+votes.length+" votes · tick "+
      (window.__TICKAGE||0)+"ms";
  }
  var rows=tick.slice(0,40).map(function(t){
    var a=amap[t.s], v=vmap[t.s]||0, chg=t.c||0;
    var al=a?('<span style="color:'+(a.kind==="PUMP"?"#3fb950":"#f85149")+';font-weight:700">'+
      a.kind+' '+Math.round(a.score||0)+' ▸</span>'):'<span style="color:#33414f">—</span>';
    var vc=v>0?"#3fb950":v<0?"#f85149":"#4d5d70";
    var onclick = a ? ("showAlert('"+esc(t.s)+"','"+(a.event_id||"")+"')")
                    : ("drillChart('"+esc(t.s)+"',null)");
    var title = a ? "why this signal fired" : "open "+esc(t.s)+" chart";
    return '<tr class="sigrow" style="cursor:pointer" onclick="'+onclick+'" title="'+title+'">'+
      '<td style="font-weight:600">'+esc(t.s)+'</td>'+
      '<td>'+metricBlock(t.s)+'</td>'+
      '<td>'+(t.price?Number(t.price).toPrecision(7):'—')+'</td>'+
      '<td class="'+(chg>=0?'up':'down')+'">'+(chg>=0?'+':'')+Number(chg).toFixed(2)+'%</td>'+
      '<td>'+(t.v?('$'+(t.v/1e6).toFixed(1)+'M'):'—')+'</td>'+
      '<td>'+al+'</td><td style="color:'+vc+';font-weight:700">'+(v>0?'+':'')+v+'</td></tr>';
  }).join("");
  box.innerHTML='<table class="wide" style="width:100%"><thead><tr><th>symbol</th><th>spark</th><th>price</th>'+
    '<th>24h</th><th>liquidity</th><th>alert</th><th>votes</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
async function renderTape(){
  var box=$("tapebody"); if(!box) return;
  try{ TAPEDATA=(await (await fetch("/api/tape",{cache:"no-store"})).json()).fills||[]; }catch(e){ TAPEDATA=[]; }
  var f=$("tfilt"), k=$("tkind"), q=($("tsearch")?$("tsearch").value:"").toLowerCase();
  var eng=f?f.value:"all", kind=k?k.value:"all";
  if(f && f.options.length<=1){
    var srcs={};TAPEDATA.forEach(function(x){srcs[x.src]=1;});
    Object.keys(srcs).sort().forEach(function(s2){
      var o=document.createElement("option");o.value=s2;o.textContent=s2;f.appendChild(o);});
  }
  var rows=TAPEDATA.filter(function(x){
    if(eng!=="all"&&x.src!==eng)return false;
    if(kind!=="all"&&x.kind!==kind)return false;
    if(q&&(x.sym+" "+x.src+" "+x.side+" "+x.why+" "+x.kind).toLowerCase().indexOf(q)<0)return false;
    return true;});
  var K=TAPESORT.k, D=TAPESORT.dir;
  rows.sort(function(a,b){var x=a[K]||"",y=b[K]||"";
    return typeof x==="number"&&typeof y==="number"?D*(x-y):D*String(x).localeCompare(String(y));});
  if($("tapenf")) $("tapenf").textContent=rows.length+" fills";
  var ar=function(col){return TAPESORT.k===col?(TAPESORT.dir<0?" \u25bc":" \u25b2"):"";};
  var th=function(col,label,al){return '<th class="sortable" style="cursor:pointer;'+
    (al?'text-align:left':'')+'" onclick="tapeSort(\''+col+'\')">'+label+ar(col)+'</th>';};
  box.innerHTML=rows.length?'<table class="wide" style="width:100%"><thead><tr>'+
    th("t","time")+th("src","engine")+th("side","side")+th("sym","symbol")+
    th("kind","type")+th("why","reason",true)+'</tr></thead><tbody>'+
    rows.slice(0,300).map(function(x){
      var c=x.kind==="open"?(x.side==="BUY"?"#3fb950":"#f85149"):"#58a6ff";
      return '<tr class="sigrow" style="cursor:pointer" onclick="drillChart(\''+x.sym+'\',null)">'+
        '<td style="color:#5f7186">'+x.t+'</td>'+
        '<td>'+symbolSpark(x.sym,60,16)+'</td>'+
        '<td style="color:#8095ab">'+x.src+'</td>'+
        '<td style="color:'+c+';font-weight:700">'+x.side+'</td>'+
        '<td style="font-weight:600">'+x.sym+'</td>'+
        '<td>'+(x.kind==="open"?'entry':'exit')+'</td>'+
        '<td style="text-align:left;color:#7c8b9d">'+esc(x.why)+'</td></tr>';
    }).join("")+'</tbody></table>':'<div class="empty">no fills match</div>';
}
function advWhy(r, x){
  var bits=[];
  var im=x.book_imbalance, br=x.buy_ratio, oi=x.oi_change_1h_pct, sd=x.smart_vs_dumb;
  if(im!=null) bits.push((im>0.15?"bids stacked":im<-0.15?"asks stacked":"book balanced")+" ("+(im>0?"+":"")+im.toFixed(2)+")");
  if(br!=null) bits.push("takers "+(100*br).toFixed(0)+"% buying");
  if(oi!=null) bits.push("OI "+(oi>0?"+":"")+oi.toFixed(1)+"% over 1h");
  if(sd!=null) bits.push("top traders "+sd.toFixed(2)+"x retail");
  if(x.funding_pct!=null) bits.push("funding "+(100*x.funding_pct).toFixed(3)+"%");
  if(x.flow_conf!=null) bits.push("flow confidence "+(x.flow_conf>0?"+":"")+x.flow_conf.toFixed(0));
  if(x.spread_bps!=null) bits.push("spread "+x.spread_bps.toFixed(1)+"bp");
  if(x.slippage_bps_5k!=null) bits.push("$5k slippage "+x.slippage_bps_5k.toFixed(1)+"bp");
  if(r.alert!=null) bits.push("alert score "+Number(r.alert).toFixed(2));
  var lead=(r.why||"").split("·")[0].trim();
  return (lead?lead+" — ":"")+bits.join(" · ");
}
async function dashAdv(){
  var el=$("dashadv"); if(!el) return;
  try{
    var d=await (await fetch("/api/opportunities",{cache:"no-store"})).json();
    var f=d.flagged||[], rows=(d.opportunities||[]).filter(function(r){return f.indexOf(r.symbol)>=0;});
    var ext={};
    try{ var e=await (await fetch("/api/signals_ext",{cache:"no-store"})).json();
         (e.rows||[]).forEach(function(r){ ext[r.symbol]=r; }); }catch(err){}
    if(!rows.length){ el.innerHTML='<span class="hint">agent warming up…</span>'; return; }

    // ---- headline visuals
    var pumps=rows.filter(function(r){return (r.why||"").indexOf("PUMP")>=0;}).length;
    var dumps=rows.length-pumps;
    var avg=rows.reduce(function(a,r){return a+(r.score||0);},0)/rows.length;
    var head='<div style="display:flex;gap:18px;align-items:center;margin-bottom:10px;flex-wrap:wrap">'+
      '<div><div style="font-size:9px;letter-spacing:.12em;color:#5f7186">FLAGGED</div>'+
        '<div style="font-size:18px;font-weight:700">'+rows.length+'</div></div>'+
      '<div><div style="font-size:9px;letter-spacing:.12em;color:#5f7186">PUMP / DUMP</div>'+
        '<div style="font-size:18px;font-weight:700"><span class="up">'+pumps+'</span>'+
        '<span style="color:#3d4d5f"> / </span><span class="down">'+dumps+'</span></div></div>'+
      '<div><div style="font-size:9px;letter-spacing:.12em;color:#5f7186">AVG SCORE</div>'+
        '<div style="font-size:18px;font-weight:700;color:'+(avg>0.45?"#3fb950":"#d29922")+'">'+
        avg.toFixed(2)+'</div></div>'+
      '<div style="flex:1;min-width:180px"><div style="font-size:9px;letter-spacing:.12em;color:#5f7186;'+
        'margin-bottom:4px">TAPE PRESSURE</div>'+
        '<div style="display:flex;height:10px;border-radius:5px;overflow:hidden;background:#16202b">'+
        '<i style="width:'+Math.round(100*pumps/rows.length)+'%;background:#3fb950"></i>'+
        '<i style="flex:1;background:#f85149"></i></div></div></div>';

    // ---- per-symbol visual rows
    var cards=rows.slice(0,6).map(function(r){
      var x=ext[r.symbol]||{};
      var col=r.score>0.45?"#3fb950":"#d29922";
      var conf=x.flow_conf||0;
      var chips=[
        ["flow",conf,"#58a6ff"],
        ["imbal",(x.book_imbalance||0)*100,"#a371f7"],
        ["buy",((x.buy_ratio||0)-0.5)*200,"#3fb950"],
        ["OI",(x.oi_change_1h_pct||0)*10,"#e3b341"]
      ].map(function(c){
        var v=Math.max(-100,Math.min(100,c[1]));
        var w=Math.abs(v)/2;
        return '<span style="display:inline-flex;align-items:center;gap:4px;margin-right:10px">'+
          '<span style="font-size:8.5px;color:#5f7186;letter-spacing:.06em">'+c[0]+'</span>'+
          '<span style="width:44px;height:5px;background:#16202b;border-radius:3px;display:flex;overflow:hidden">'+
          '<i style="width:'+w+'%;background:'+c[2]+';'+(v<0?'margin-left:auto':'')+'"></i></span>'+
          '<span style="font-size:9px;color:'+(v>=0?"#8fa3b8":"#8fa3b8")+'">'+Math.round(v)+'</span></span>';
      }).join("");
      var gauge='<span style="display:inline-flex;align-items:center;gap:6px">'+
        '<span style="width:56px;height:6px;background:#16202b;border-radius:3px;display:flex;overflow:hidden">'+
        '<i style="width:'+Math.round(r.score*100)+'%;background:'+col+'"></i></span>'+
        '<b style="color:'+col+';font-size:11px">'+r.score.toFixed(2)+'</b></span>';
      return '<div style="padding:7px 0;border-bottom:1px solid #131c26">'+
        '<div style="display:flex;align-items:center;gap:10px">'+
          '<b class="tick" style="width:86px;cursor:pointer;color:#e6edf6" '+
            'onclick="drillChart(\''+esc(r.symbol)+'\',null)">'+esc(r.symbol)+'</b>'+
          symbolSpark(r.symbol,104,22)+
          gauge+
          '<span style="flex:1;color:#7c8b9d;font-size:10.5px;overflow:hidden;'+
          'text-overflow:ellipsis;white-space:nowrap">'+esc(r.why)+'</span></div>'+
        '<div style="margin:5px 0 0 96px">'+chips+'</div>'+
        '<div style="margin:6px 0 2px 96px;color:#8fa3b8;font-size:10.5px;line-height:1.45">'+
          esc(advWhy(r,x))+'</div></div>';
    }).join("");
    el.innerHTML=head+cards;
  }catch(e){}
}
async function totalEquity(){
  try{
    var a=await (await fetch("/api/all",{cache:"no-store"})).json();
    var L=(a.engines||[]).filter(function(e){return e.equity!=null;});
    var tot=L.reduce(function(s2,e){return s2+(e.equity||0);},0);
    var st=L.reduce(function(s2,e){return s2+(e.start_equity||0);},0);
    var el=$("toteq");
    if(el){
      el.textContent="total $"+Math.round(tot).toLocaleString()+
        " ("+(st?((tot/st-1)*100>=0?"+":"")+((tot/st-1)*100).toFixed(2)+"%":"—")+")";
      el.className="pill "+((st&&tot>=st)?"ok":"bad");
    }
    var rb=$("rail-books"); if(rb) rb.textContent=L.length+" books · $"+Math.round(tot/1000)+"k";
  }catch(e){}
}
async function railStatus(){
  try{
    var t=await (await fetch("/api/trading",{cache:"no-store"})).json();
    var a=await (await fetch("/api/all",{cache:"no-store"})).json();
    var books=(a.engines||[]).filter(function(e){return e.equity!=null;}).length;
    var on=t.on===true;
    $("rail-trade").textContent=on?"trading":"idle";
    $("rail-trade").className="pill "+(on?"ok":"");
    $("rail-books").textContent=books+" books";
    $("rail-live").className="pill "+(books>0?"ok":"");
  }catch(e){}
}
async function tape(){
  var el=$("tapestrip"); if(!el) return;
  try{
    var d=await (await fetch("/api/tape",{cache:"no-store"})).json();
    var f=(d.fills||[]).slice().reverse().slice(0,60);
    document.getElementById("tapen").textContent=f.length?"last "+f[0].t:"—";
    el.innerHTML=f.map(function(x){
      var col=x.kind==="open"?(x.side==="BUY"?"#3fb950":"#f85149"):"#58a6ff";
      return '<div class="taperow">'+
        '<span style="color:#5f7186">'+x.t+'</span>'+
        '<span>'+symbolSpark(x.sym,60,14)+'</span>'+
        '<span style="color:#8095ab">'+x.src+'</span>'+
        '<span style="color:'+col+';font-weight:700">'+x.side+'</span>'+
        '<span style="cursor:pointer" onclick="drillChart(\''+x.sym+'\',null)">'+x.sym+'</span>'+
        '<span class="reason">'+((x.kind==="close")?"● ":"")+esc(x.why)+'</span></div>';
    }).join("")||'<div class="hint">no fills yet</div>';
  }catch(e){}
}
async function beat(){
  var el=$("beat"); if(!el) return;
  try{
    var d=await (await fetch("/api/heartbeat",{cache:"no-store"})).json();
    var age=Math.round((Date.now()-(d.ts||0))/1000);
    el.textContent="beat "+(age<3?"\u25cf":age+"s")+" \u00b7 "+d.books+" books \u00b7 "+
      (d.realised>=0?"+":"")+Number(d.realised||0).toFixed(2);
    el.className="pill "+(age<15?"ok":"bad");
  }catch(e){ el.textContent="beat \u2014"; el.className="pill bad"; }
}

async function tick(){
  let d;
  try{ d=await (await fetch("/api/all",{cache:"no-store"})).json(); }
  catch(e){ $("live").textContent="offline"; $("live").className="pill bad"; return; }
  const es=d.engines||[];
  try{ renderAttrib(es); }catch(e){}
  try{
  const live=es.filter(e=>e.alive).length;
  $("live").textContent=live+"/"+es.length+" live";
  $("live").className="pill "+(live===es.length?"ok":"bad");
  $("clock").textContent=new Date().toLocaleTimeString(undefined,{hour12:false});

  const m=d.models;
  var nBooks=es.filter(function(e){return e.equity!=null;}).length;
  var nOpen=es.reduce(function(a,e){return a+((e.positions||[]).length);},0);
  try{
    var ov=$("ovr");
    if(ov){ fetch("/api/options",{cache:"no-store"}).then(function(r){return r.json();}).then(function(o){
      ov.style.display = o.override_crazy ? "flex" : "none";
    }).catch(function(){}); }
  }catch(e){}
  $("modelsbadge").textContent = nBooks+" models · "+nOpen+" open";

  // totals across engines
  const alive=es.filter(e=>e.alive&&e.equity!=null);
  const eq=alive.reduce((a,e)=>a+(e.equity||0),0);
  const base=alive.reduce((a,e)=>a+(e.start_equity||2000),0);
  const real=alive.reduce((a,e)=>a+(e.realised||0),0);
  const unreal=alive.reduce((a,e)=>a+(e.unrealised||0),0);
  const open=alive.reduce((a,e)=>a+(e.positions||[]).length,0);
  const closed=(d.totals&&d.totals.closed)||alive.reduce((a,e)=>a+(e.closed||0),0);
  $("totals").innerHTML=[
    ["combined equity","$"+money(eq)],
    ["combined return",pct(base?100*(eq/base-1):null)],
    ["realised","$"+money(real)],
    ["unrealised","$"+money(unreal)],
    ["open positions",open],
    ["closed trades",closed],
  ].map(([k,v])=>`<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  LAST=es;
  try{ histPush(es); }catch(e){}
  const groups=[
    ["Core engines", ["turtle","swing"]],
    ["Model ensembles", ["multimodels","statsmodels","swing_turtle"]],
    ["Turtle timeframes", ["t10s","t20s","t45s","t1m","t2m"]],
  ];
  const placed=new Set();
  const card=(e)=>{
    if(e.equity==null){
      return `<div class="card off" title="this engine is not running">
        <div class="top"><div><div class="name" style="color:${e.colour||'#5f7186'}">${esc(e.key)}</div>
        <div class="label">${esc(e.label)}</div></div>
        <div style="text-align:right"><span class="pill">idle</span></div></div>
        <div class="rows"><span style="color:#59677a">not running · no book to report</span></div></div>`;
    }
    const pos=(e.positions||[]).slice(0,5);
    const scanning=(PREV[e.key]!=null&&e.scans!=null&&e.scans>PREV[e.key]);
    if(e.scans!=null) PREV[e.key]=e.scans;
    return `<div class="card ${e.alive?"":"off"} ${scanning?"scanning":""}"
        onclick="openDetail('${e.key}')" title="click for trades, positions, risk, rationale">
      <div class="top">
        <div><div class="name" style="color:${e.colour}">${esc(e.key)}</div>
             <div class="label">${esc(e.label)}</div></div>
        <div style="text-align:right">
          <div class="ret ${cls(e.return_pct)}">${pct(e.return_pct)}</div>
          <button class="force" onclick="event.stopPropagation();forceScan('${e.key}')">force</button>
        </div>
      </div>
      <div class="scanline"></div>
      <div class="rows">
        <span>equity <b>$${money(e.equity)}</b> ${sparkSVG((HIST[e.key]||[]).slice(-40),58,14)}</span>
        <span>start <b>$${money(e.start_equity||2000)}</b></span>
        <span>Δ <b class="${cls((e.equity||0)-(e.start_equity||2000))}">${((e.equity||0)-(e.start_equity||2000)>=0?"+":"")}$${money((e.equity||0)-(e.start_equity||2000))}</b></span>
        <span>real <b class="${cls(e.realised)}">${money(e.realised)}</b></span>
        <span>unreal <b class="${cls(e.unrealised)}">${money(e.unrealised)}</b></span>
        <span>closed <b>${e.closed??"—"}</b></span>
        <span>open <b>${(e.positions||[]).length}</b></span>
      </div>
      <div class="body">
      ${pos.length?`<table><thead><tr><th>ticker</th><th>side</th><th>P&L</th><th>held</th></tr></thead>
        <tbody>${pos.map(p=>`<tr><td class="tick" data-drill-sym=${esc(p.symbol)} data-sym="${esc(p.symbol)}" data-eng="${esc(e.key)}">${esc(p.symbol)}</td>
          <td><span class="side ${p.side==='buy'?'buy':'sell'}">${esc((p.side||'?').toUpperCase())}</span></td>
          <td class="${cls(p.gross_pct)}">${pct(p.gross_pct)}</td>
          <td>${Math.round(p.held_s||0)}s</td></tr>`).join("")}</tbody></table>`
        :'<div class="empty">flat</div>'}
      </div>
    </div>`;
  };
  let html="";
  for(const [title,keys] of groups){
    const items=es.filter(e=>keys.includes(e.key));
    items.forEach(e=>placed.add(e.key));
    if(!items.length) continue;
    html+=`<div class="section"><h2>${title}</h2><div class="grid">${items.map(card).join("")}</div></div>`;
  }
  const rest=es.filter(e=>!placed.has(e.key));
  if(rest.length) html+=`<div class="section"><h2>Other</h2><div class="grid">${rest.map(card).join("")}</div></div>`;
  $("sections").innerHTML=html;

  // model aggregate panel
  if(m){
    const rows=(m.rows||[]).slice(0,10);
    $("modelspanel").innerHTML=`
      <div class="rows" style="margin-top:0">
        <span>symbols <b>${m.symbols??"—"}</b></span>
        <span>signals <b>${m.signals_in_window??"—"}</b></span>
        <span>alerts <b>${m.alerts_in_window??"—"}</b></span>
        <span>decisions <b>${JSON.stringify(m.decisions||{})}</b></span>
      </div>
      ${rows.length?`<table class="wide"><thead><tr>
        <th>ticker</th><th>decision</th><th>net</th><th>agree</th>
        <th>price</th><th>volatility</th><th>volume 24h</th><th>liquidity</th><th>market cap</th>
        <th style="text-align:left">voted</th></tr></thead><tbody>
        ${rows.map(r=>{const m=(MKT&&MKT.get)?(MKT.get(r.symbol)||{}):{};
          const fmt=(v,p)=>v==null?"—":p?v.toFixed(2):money(v);
          return `<tr class="sigrow" data-sym="${esc(r.symbol)}"
                      onclick="zoomToSignal('${esc(r.symbol)}')"
                      title="open ${esc(r.symbol)} chart at its last signal">
          <td class="tick" data-drill-sym=${esc(r.symbol)} data-sym="${esc(r.symbol)}">${esc(r.symbol)}</td>
          <td class="${r.net>0?'up':r.net<0?'down':'flat'}">${esc(r.decision)}</td>
          <td>${(r.net>=0?"+":"")+Number(r.net).toFixed(2)}</td>
          <td>${r.agreement}/${r.models_heard}</td>
          <td>${m.price==null?"—":m.price.toPrecision(7)}</td>
          <td class="${(m.vol||0)>8?'down':'flat'}">${m.vol==null?"—":m.vol.toFixed(1)+"%"}</td>
          <td>${m.volume==null?"—":money(m.volume)}</td>
          <td>${m.liquidity==null?"—":"$"+money(m.liquidity)}</td>
          <td>${m.mcap==null?"—":"$"+(m.mcap/1e9).toFixed(1)+"B"}</td>
          <td style="text-align:left;color:#8a99ad">${esc((r.contributors||[]).map(c=>c.model).join(", "))}</td>
        </tr>`;}).join("")}</tbody></table>`:'<div class="empty">no aggregate rows</div>'}`;
  }else{
    $("modelspanel").innerHTML='<div class="empty">model aggregate offline</div>';
  }

  $("foot").textContent="one page, every engine · updated "+
    new Date(d.ts).toLocaleTimeString(undefined,{hour12:false})+" · paper only, no keys, no orders";
  }catch(err){
    var b=$("err");
    if(b){ b.style.display="block"; b.textContent="render error: "+(err&&err.message||err); }
  }
}
let CHART={sym:null, iv:"5m"};
/* TICKERS declared earlier with the other globals */
/* MKT declared earlier with the other globals */
let PENDING_TS=null;
async function loadTickers(){
  try{
    const d=await (await fetch("/api/tickers",{cache:"no-store"})).json();
    TICKERS=d.tickers||[];
    MKT=new Map(TICKERS.map(t=>[t.s,t]));
    const el=$("mcount"); if(el) el.textContent=TICKERS.length+" tickers";
  }catch(e){}
}
async function applyParams(key){
  const g=id=>{const el=document.getElementById(id);return el&&el.value!==""?parseFloat(el.value):null;};
  const body={risk_pct:g("p-risk"),tp_pct:g("p-tp"),sl_pct:g("p-sl"),
              max_positions:g("p-max"),max_open_risk_pct:g("p-ceil")};
  const st=document.getElementById("pstat-"+key);
  if(st) st.textContent="applying…";
  try{
    const r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(st) st.textContent = r.ok ? "applied · takes effect next scan" : ("rejected: "+(d.error||r.status));
  }catch(e){ if(st) st.textContent="failed"; }
}
function closeModal(){ $("modal").classList.remove("open"); }
function gauge(p, sl, tp){
  const lo=-(sl||1), hi=(tp||1);
  const v=Math.max(lo,Math.min(hi,p||0));
  const pct=((v-lo)/(hi-lo))*100;
  const pos=p>=0;
  return `<div class="gauge"><i style="left:${Math.min(50,pct)}%;width:${Math.abs(pct-50)}%;
          background:${pos?'#3fb950':'#f85149'};opacity:.85"></i></div>`;
}
function drillEngine(key){
  try{ closeChartModal(); }catch(e){}
  openDetail(key);
}
function drillChart(sym, eng){
  try{ closeModal(); }catch(e){}
  openChart(sym, eng||null);
}
function tsISO(ms){
  if(!ms) return "—";
  var d=new Date(ms);
  return d.toTimeString().slice(0,8);
}
function tsFull(ms){ return ms?new Date(ms).toISOString():"—"; }
function openDetail(key){
  const e=(LAST||[]).find(x=>x.key===key);
  if(!e) return;
  const pos=(e.positions||[]).slice().sort((a,b)=>(b.gross_pct||0)-(a.gross_pct||0));
  const trades=(e.trades||e.recent||[]).slice().reverse();
  const tp=e.tp, sl=e.sl, riskPct=(e.risk_usd!=null&&e.equity)?(100*e.risk_usd/e.equity):null;
  const wr=(e.wins!=null&&e.closed)?(100*e.wins/e.closed):null;
  const avg=trades.length?trades.reduce((a,t)=>a+(t.net_pct||0),0)/trades.length:null;
  const wins=trades.filter(t=>(t.net_pct||0)>0).length;
  const best=trades.length?Math.max(...trades.map(t=>t.net_pct||0)):null;
  const worst=trades.length?Math.min(...trades.map(t=>t.net_pct||0)):null;

  $("sheet").innerHTML=`
    <div class="head">
      <span style="position:absolute;left:0;top:0;bottom:0;width:5px;background:${e.colour}"></span>
      <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
        <h2 style="color:${e.colour}">${esc(e.key)}</h2>
        <span class="label" style="font-size:11.5px">${esc(e.label)}</span>
        ${e.basis?`<span class="label">· ${esc(e.basis)}</span>`:""}
      </div>
      <div class="sub">${e.alive?"live":"offline"}${e.scans!=null?" · scan #"+e.scans:""}${e.closed!=null?" · "+e.closed+" closed":""}</div>
      <div class="big ${cls(e.return_pct)}">${pct(e.return_pct)}</div>
      <div class="close2" onclick="closeModal()">×</div>
    </div>

    <div class="body2">
      <div class="tiles">
        <div class="tile"><div class="k">equity</div><div class="v">$${money(e.equity)}</div></div>
        <div class="tile"><div class="k">realised</div><div class="v ${cls(e.realised)}">$${money(e.realised)}</div></div>
        <div class="tile"><div class="k">unrealised</div><div class="v ${cls(e.unrealised)}">$${money(e.unrealised)}</div></div>
        <div class="tile"><div class="k">win rate</div><div class="v">${wr==null?"—":wr.toFixed(0)+"%"}</div></div>
        <div class="tile"><div class="k">avg net</div><div class="v ${cls(avg)}">${avg==null?"—":pct(avg)}</div></div>
        <div class="tile"><div class="k">best / worst</div><div class="v" style="font-size:13px">
          <span class="up">${best==null?"—":pct(best)}</span> / <span class="down">${worst==null?"—":pct(worst)}</span></div></div>
      </div>

      <div class="expo">
        <div class="lbl"><span>open risk · ${pos.length} positions</span>
          <span>$${money(e.risk_usd)} · ${riskPct==null?"—":riskPct.toFixed(1)+"% of equity"}</span></div>
        <div class="track"><i style="width:${Math.min(100,riskPct||0)}%"></i></div>
      </div>

      <div class="hd"><h3>Settings</h3>
        <span class="count">${Object.keys(e.settings||{}).length} live rules</span></div>
      <div class="setpage">
        ${settingsGroups(e).map(g=>`
          <div class="sgroup">
            <div class="sgt">${g.title}</div>
            <div class="sfields">
              ${g.fields.map(f=>`
                <label class="sf">
                  <span>${f.label}</span>
                  <input type="number" step="${f.step}" min="${f.min}" max="${f.max}"
                         id="set-${f.key}" value="${e.settings[f.key] ?? ""}"
                         ${e.settings[f.key]==null?"disabled":""}>
                  <em>${f.hint||""}</em>
                </label>`).join("")}
            </div>
          </div>`).join("")}
        <div class="sapply">
          <button class="applybtn" onclick="applySettings('${e.key}')">Apply settings</button>
          <span class="mk" id="sstat-${e.key}"></span>
        </div>
      </div>

      <div class="hd"><h3>Open positions</h3><span class="count">${pos.length}</span></div>
      ${pos.length?`<table class="wide"><thead><tr>
          <th>ticker</th><th>side</th><th>entered</th><th>entry</th><th>now</th>
          <th>P&amp;L</th><th>peak</th><th>stop/trail</th><th>notional</th>
          <th>risk $</th><th>held</th><th>conf</th><th style="text-align:left">signals used</th>
        </tr></thead><tbody>${pos.map(p=>`<tr>
          <td class="tick" data-drill-sym=${esc(p.symbol)} data-sym="${esc(p.symbol)}" data-eng="${esc(e.key)}">${esc(p.symbol)}</td>
          <td><span class="side ${p.side==='buy'?'buy':'sell'}">${esc((p.side||'?').toUpperCase())}</span></td>
          <td class="mini" title="${tsFull(p.entry_ms)}">${tsISO(p.entry_ms)}</td>
          <td>${p.entry==null?"—":Number(p.entry).toPrecision(8)}</td>
          <td>${p.price==null?"—":Number(p.price).toPrecision(8)}</td>
          <td class="${cls(p.gross_pct)}"><b>${pct(p.gross_pct)}</b></td>
          <td class="mini">${pct(p.peak_pct)}</td>
          <td class="mini">${pct(p.trail_line??p.stop)}</td>
          <td>${p.notional==null?"—":"$"+money(p.notional)}</td>
          <td>${p.risk_usd==null?"—":"$"+money(p.risk_usd)}</td>
          <td class="mini">${Math.round(p.held_s||0)}s</td>
          <td class="mini">${p.conf==null?"—":(100*p.conf).toFixed(0)+"%"}</td>
          <td class="why2">${esc(p.why||"—")}</td>
        </tr>`).join("")}</tbody></table>`
        :'<div class="empty">flat — no open positions</div>'}

      <div class="hd"><h3>Trades</h3><span class="count">${trades.length}</span></div>
      ${trades.length?`<table class="wide"><thead><tr>
          <th>ticker</th><th>side</th><th>entered</th><th>exited</th><th>entry</th><th>exit</th>
          <th>gross</th><th>net</th><th>peak</th><th>P&amp;L $</th>
          <th>notional</th><th>reason</th><th>held</th><th style="text-align:left">signals used</th>
        </tr></thead><tbody>${trades.slice(0,40).map(t=>`<tr>
          <td class="tick" data-drill-sym=${esc(t.symbol)} data-sym="${esc(t.symbol)}" data-eng="${esc(e.key)}">${esc(t.symbol)}</td>
          <td><span class="side ${t.side==='buy'?'buy':'sell'}">${esc((t.side||'?').toUpperCase())}</span></td>
          <td class="mini" title="${tsFull(t.entry_ms)}">${tsISO(t.entry_ms)}</td>
          <td class="mini" title="${tsFull(t.exit_ms)}">${tsISO(t.exit_ms)}</td>
          <td>${t.entry==null?"—":Number(t.entry).toPrecision(8)}</td>
          <td>${t.exit==null?"—":Number(t.exit).toPrecision(8)}</td>
          <td class="mini">${pct(t.gross_pct)}</td>
          <td class="${cls(t.net_pct)}"><b>${pct(t.net_pct)}</b></td>
          <td class="mini">${pct(t.peak_pct)}</td>
          <td class="${cls(t.pnl_usd)}">${t.pnl_usd==null?"—":(t.pnl_usd>=0?"+":"")+"$"+money(t.pnl_usd)}</td>
          <td class="mini">${t.notional==null?"—":"$"+money(t.notional)}</td>
          <td class="mini">${esc(t.reason||"—")}</td>
          <td class="mini">${t.held_s==null?"—":Math.round(t.held_s)+"s"}</td>
          <td class="why2">${esc(t.why||"—")}</td>
        </tr>`).join("")}</tbody></table>`
        :'<div class="empty">no closed trades yet</div>'}
    </div>`;
  $("modal").classList.add("open");
}

document.addEventListener("keydown",e=>{
  if(e.key==="Escape"){
    /* Escape always goes back one layer, in stacking order. */
    const layers=[
      ["chartbox",   function(){ closeChart(); }],
      ["chartmodal", function(){ closeChartModal(); }],
      ["modal",      function(){ closeModal(); }]
    ];
    for(const [id, close] of layers){
      const el=$(id);
      if(!el) continue;
      const open = el.classList.contains("open") || getComputedStyle(el).display!=="none" ||
                   el.style.display==="flex" || el.style.display==="block";
      if(el.classList.contains("open")){ close(); e.preventDefault(); return; }
    }
    const c=$("cctl");
    if(c && c.classList.contains("open")){ c.classList.remove("open"); refreshCtlBtn(); e.preventDefault(); return; }
    /* nothing open: go back to the dashboard */
    const active=["thinking","busview","moversview","marketlive","forecast","advisor","signals",
                  "tape","models","opt","lb","control","review","why","health"]
                 .find(k=>{ const el=$(k); return el && el.classList.contains("on"); });
    if(active){ showTab("dash"); e.preventDefault(); return; }
    return;
  }
  if($("market")&&$("market").classList.contains("on")&&LASTROWS.length){
    const n=LASTROWS.length;
    if(e.key==="+"||e.key==="="){ e.preventDefault(); zoomAt((70+1200-86)/2,(VIEW.c||n)*0.8); }
    if(e.key==="-"||e.key==="_"){ e.preventDefault(); zoomAt((70+1200-86)/2,(VIEW.c||n)*1.25); }
    if(e.key==="ArrowLeft"){ e.preventDefault(); VIEW.s=Math.max(0,(VIEW.s||0)-Math.round((VIEW.c||n)*0.15)); drawMarket(); }
    if(e.key==="ArrowRight"){ e.preventDefault(); VIEW.s=Math.min(n-(VIEW.c||n),(VIEW.s||0)+Math.round((VIEW.c||n)*0.15)); drawMarket(); }
  }
});

// every ticker anywhere opens the chart, in one place
document.addEventListener("click", function(ev){
  var t=ev.target.closest ? ev.target.closest("[data-drill-sym]") : null;
  if(t){
    ev.preventDefault(); ev.stopPropagation();
    drillChart(t.getAttribute("data-drill-sym"), t.getAttribute("data-drill-eng")||null);
    return;
  }
  const el=ev.target.closest(".tick");
  if(!el) return;
  ev.preventDefault();
  ev.stopPropagation();
  const sym=(el.dataset.sym||el.textContent||"").trim().toUpperCase();
  if(sym) openChart(sym, el.dataset.eng||null);
}, true);

function labelTickers(){
  document.querySelectorAll(".tick").forEach(el=>{
    const sym=(el.dataset.sym||el.textContent||"").trim().toUpperCase();
    if(sym && !el.title) el.title="Open "+sym+" chart";
  });
}
setInterval(labelTickers, 2000);
restoreSide();
loadTickers(); setInterval(loadTickers,60000);
tick(); setInterval(tick,2000);
health(); setInterval(health,5000);
var PAGE_BUILD=null;
async function watchBuild(){
  try{
    var d=await (await fetch("/api/version",{cache:"no-store"})).json();
    if(PAGE_BUILD===null){ PAGE_BUILD=d.build; return; }
    if(d.build!==PAGE_BUILD){
      var b=document.getElementById("stale");
      if(b) b.style.display="flex";
      if(!window.__reloading){ window.__reloading=true; setTimeout(function(){ location.reload(true); }, 2500); }
    }
  }catch(e){}
}
watchBuild(); setInterval(watchBuild,15000);
beat(); setInterval(beat,5000);
refreshTrading(); setInterval(refreshTrading,4000);
tape(); setInterval(tape,1500);
dashAdv(); setInterval(dashAdv,3000);
railStatus(); setInterval(railStatus,4000);
totalEquity(); setInterval(totalEquity,4000);
refreshSparks(); setInterval(refreshSparks,15000);
refreshMetrics();
renderTape(); setInterval(function(){ if(document.getElementById("tape").classList.contains("on")) renderTape(); },1500);
renderSignals(); setInterval(function(){ if(document.getElementById("signals").classList.contains("on")) renderSignals(); },1000);
renderOpps();
renderAdvisor(); setInterval(function(){ if(document.getElementById("advisor").classList.contains("on")) renderAdvisor(); },2000);
renderOptions();
renderForecast(); setInterval(function(){ if(document.getElementById("forecast").classList.contains("on")) renderForecast(); },4000);
renderThinking(); setInterval(function(){ if(document.getElementById("thinking") && document.getElementById("thinking").classList.contains("on")) renderThinking(); },4000);
renderMarketLive(); mlStream();
setInterval(function(){ if(document.getElementById("marketlive").classList.contains("on")) renderMarketLive(); },1000);
renderPending(); setInterval(function(){ if(document.getElementById("signals").classList.contains("on")) renderPending(); },1000);

/* ================= SORTING (every table) ================= */
window.__sortState = window.__sortState || {};
window.__sortBusy = false;
function tableKey(t){
  var host=t.closest("[id]");
  var heads=Array.prototype.map.call(t.tHead?t.tHead.rows[0].cells:[],function(c){
    return (c.textContent||"").trim().slice(0,14);
  }).join("|");
  return (host?host.id:"body")+"::"+heads;
}
function cellValue(tr,i){
  var c=tr.cells[i]; if(!c) return "";
  var txt=(c.getAttribute("data-sort")||c.textContent||"").trim();
  var num=parseFloat(txt.replace(/[$,%+]/g,""));
  return isNaN(num)?txt.toLowerCase():num;
}
function sortTableBy(t,idx,dir){
  var tb=t.tBodies[0]; if(!tb) return;
  var rows=Array.prototype.slice.call(tb.rows);
  rows.sort(function(a,b){
    var x=cellValue(a,idx), y=cellValue(b,idx);
    if(typeof x==="number"&&typeof y==="number") return dir*(x-y);
    return dir*String(x).localeCompare(String(y));
  });
  rows.forEach(function(r){ tb.appendChild(r); });
  Array.prototype.forEach.call(t.tHead.rows[0].cells,function(c,i){
    c.classList.remove("sort-asc","sort-desc");
    if(i===idx) c.classList.add(dir>0?"sort-asc":"sort-desc");
  });
}
function applyAllSorts(){
  if(window.__sortBusy) return;
  window.__sortBusy=true;
  try{
    var tables=document.querySelectorAll("table");
    for(var i=0;i<tables.length;i++){
      var t=tables[i];
      if(!t.tHead||!t.tBodies[0]) continue;
      var k=tableKey(t), st=window.__sortState[k];
      var ths=t.tHead.rows[0].cells;
      for(var j=0;j<ths.length;j++){
        if(!ths[j].classList.contains("sortable")) ths[j].classList.add("sortable");
      }
      if(st) sortTableBy(t,st.idx,st.dir);
    }
  }catch(e){}
  window.__sortBusy=false;
}
document.addEventListener("click",function(e){
  var th=e.target.closest("th"); if(!th) return;
  var t=th.closest("table"); if(!t||!t.tHead) return;
  var idx=Array.prototype.indexOf.call(th.parentNode.cells,th);
  var k=tableKey(t);
  var cur=window.__sortState[k];
  var dir = (cur && cur.idx===idx) ? -cur.dir : (idx===0?1:-1);
  window.__sortState[k]={idx:idx,dir:dir};
  sortTableBy(t,idx,dir);
  e.stopPropagation();
},true);
setInterval(applyAllSorts,700);

/* ================= EVERY TICKER IS CLICKABLE ================= */
function bindTicks(){
  var els=document.querySelectorAll('.wrap td, .wrap b, .wrap span, .wrap div');
  for(var i=0;i<els.length;i++){
    var el=els[i];
    if(el.children.length) continue;
    if(el.__tk) continue;
    var t=(el.textContent||"").trim();
    if(!/^[A-Z0-9]{2,15}USDT$/.test(t)) continue;
    el.__tk=1;
    el.style.cursor="pointer";
    el.title=t+" — click for the chart";
    el.addEventListener("click",function(ev){
      ev.stopPropagation();
      var sym=this.textContent.trim();
      if(typeof drillChart==="function") drillChart(sym,null); else openChart(sym);
    });
  }
}
setInterval(bindTicks,800);
</script></body></html>"""

try:
    with open(os.path.join(HERE, "ui", "lib.js")) as _fh:
        PAGE = PAGE.replace("__UI_LIB__", _fh.read())
except Exception:
    PAGE = PAGE.replace("__UI_LIB__", "window.UI=window.UI||{};")



BINANCE = "https://api.binance.com"


def binance_klines(symbol, interval="5m", limit=200):
    """Prefer the Go backend: it returns bars AND computed indicators."""
    try:
        d = jget("http://127.0.0.1:8787/api/klines?symbol=%s&interval=%s&limit=%d"
                 % (symbol, interval, limit), timeout=8.0)
        bars = d.get("bars") or []
        if bars:
            return {"rows": [{"t": b["t"], "o": b["o"], "h": b["h"],
                              "l": b["l"], "c": b["c"], "v": b.get("v") or 0.0}
                             for b in bars],
                    "indicators": d.get("indicators") or {}}
    except Exception:
        pass
    url = ("%s/api/v3/klines?interval=%s&limit=%d&symbol=%s"
           % (BINANCE, interval, limit, symbol))
    raw = jget(url, timeout=6.0)
    return {"rows": [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
                      "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in raw],
            "indicators": {}}


def ui_lib():
    """The shared component library, inlined into every page."""
    try:
        with open(os.path.join(HERE, "ui", "lib.js")) as fh:
            return fh.read()
    except Exception:
        return "window.UI=window.UI||{};"


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _model_control(self):
        import urllib.parse
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        model = (q.get("model", [""])[0] or "").strip()
        act = (q.get("action", [""])[0] or "").strip()
        path = os.path.join(HERE, "state", "disabled.json")
        try:
            cur = json.load(open(path))
        except Exception:
            cur = {"books": []}
        books = [b for b in (cur.get("books") or []) if b]
        if model and act == "stop" and model not in books:
            books.append(model)
        elif model and act == "start" and model in books:
            books.remove(model)
        with open(path + ".t", "w") as fh:
            json.dump({"books": books, "ts": int(time.time() * 1000)}, fh)
        os.replace(path + ".t", path)
        return self._send(200, json.dumps({"ok": True, "disabled": books}),
                          "application/json")

    def do_POST(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/opt/run"):
            import urllib.parse
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            m = q.get("model", [""])[0]
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:8795/run?model=" + urllib.parse.quote(m), method="POST"),
                    timeout=8).read()
                return self._send(200, r, "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if u.path.startswith("/api/opt/apply"):
            import urllib.parse
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            m = q.get("model", [""])[0]
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:8795/apply?model=" + urllib.parse.quote(m), method="POST"),
                    timeout=8).read()
                return self._send(200, r, "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if u.path.startswith("/api/model_control"):
            return self._model_control()
        if u.path == "/api/trading":
            act = urllib.parse.parse_qs(u.query).get("action", [""])[0]
            if act == "start":
                trading_start()
            elif act == "stop":
                trading_stop()
            time.sleep(0.4)
            body = json.dumps(trading_loop_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        from urllib.parse import parse_qs, urlparse
        if self.path.split("?")[0] == "/api/control-all":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception as exc:
                return self._send(400, json.dumps({"error": str(exc)}), "application/json")
            LIMITS = {"risk_pct": (0.05, 15.0), "tp_pct": (0.2, 10.0),
                      "sl_pct": (0.2, 8.0), "max_positions": (1, 40),
                      "min_agree": (1, 5), "max_open_risk_pct": (5, 100),
                      "min_confidence_pct": (0, 100), "hold_sec": (5, 3600),
                      "trail_ratio": (0, 1), "trail_arm_pct": (0, 10),
                      "lock_trade_pct": (0, 5), "lock_profit_pct": (0, 10),
                      "lock_usd": (0, 100000), "max_hold": (0, 86400),
                      "scan_every": (0.5, 600), "conviction_cap": (0.5, 5),
                      "vol_mult": (0, 10), "tolerance": (0, 0.05),
                      "min_stop_frac": (0.0005, 0.05)}
            clean = {"ts": int(time.time() * 1000)}
            for k, v in body.items():
                if k == "ts" or v in (None, ""):
                    continue
                if k not in LIMITS:
                    continue
                try:
                    f = float(v)
                except Exception:
                    continue
                lo, hi = LIMITS[k]
                if not (lo <= f <= hi):
                    return self._send(400, json.dumps(
                        {"error": "%s must be %s..%s" % (k, lo, hi)}), "application/json")
                clean[k] = f
            if len(clean) <= 1:
                return self._send(400, json.dumps({"error": "no valid rules supplied"}),
                                  "application/json")
            applied = {}
            for fname in ("control.json", "control_multi.json",
                          "control_stats.json", "control_turtle.json",
                          "control_t10s.json", "control_t20s.json",
                          "control_t45s.json", "control_t1m.json",
                          "control_t2m.json"):
                try:
                    path = os.path.join(HERE, "state", fname)
                    with open(path + ".tmp", "w") as fh:
                        json.dump(clean, fh)
                    os.replace(path + ".tmp", path)
                    applied[fname] = True
                except Exception:
                    applied[fname] = False
            # the Go turtle keeps its own config
            go_ok = False
            try:
                base = "http://127.0.0.1:8787"
                cfg = jget(base + "/api/turtle/config")["config"]
                upd = dict(cfg)
                if "risk_pct" in clean:
                    upd["risk_pct"] = round(clean["risk_pct"] / 100.0, 5)
                if "max_positions" in clean:
                    upd["max_units_total"] = int(clean["max_positions"])
                if 0 < upd.get("risk_pct", 0) <= 0.2:
                    data = json.dumps(upd).encode()
                    hdr = {"Content-Type": "application/json"}
                    try:
                        urllib.request.urlopen(urllib.request.Request(
                            base + "/api/turtle/config", data=data, headers=hdr,
                            method="POST"), timeout=6).read()
                    except urllib.error.HTTPError as he:
                        if he.code == 409:
                            for _act in ("stop", None, "start"):
                                try:
                                    if _act is None:
                                        urllib.request.urlopen(urllib.request.Request(
                                            base + "/api/turtle/config", data=data,
                                            headers=hdr, method="POST"), timeout=6).read()
                                    else:
                                        urllib.request.urlopen(urllib.request.Request(
                                            base + "/api/turtle?action=" + _act,
                                            data=b"{}", headers=hdr,
                                            method="POST"), timeout=10).read()
                                except Exception:
                                    pass
                        else:
                            raise
                    # never leave the session stopped after a config change
                    try:
                        st = jget(base + "/api/turtle", timeout=4.0)
                        if (st.get("state") or "").lower() != "running":
                            urllib.request.urlopen(urllib.request.Request(
                                base + "/api/turtle?action=start", data=b"{}",
                                headers=hdr, method="POST"), timeout=10).read()
                    except Exception:
                        pass
                    go_ok = True
            except Exception:
                go_ok = False
            return self._send(200, json.dumps({"applied": applied, "go_turtle": go_ok,
                                               "rules": len(clean) - 1}), "application/json")
        if self.path.split("?")[0] == "/api/engine-control":
            q = parse_qs(urlparse(self.path).query)
            engine = (q.get("engine") or [""])[0]
            if engine == "turtle":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                except Exception as exc:
                    return self._send(400, json.dumps({"error": str(exc)}),
                                      "application/json")
                try:
                    base = "http://127.0.0.1:8787"
                    cfg = jget(base + "/api/turtle/config")["config"]
                    upd = dict(cfg)
                    for k in ("s1_entry", "s1_exit", "s2_entry", "s2_exit",
                              "max_units_total", "max_units_per_direction",
                              "max_units_per_market", "stop_n", "n_lookback",
                              "vol_veto_pct"):
                        if k in body and body[k] not in (None, ""):
                            upd[k] = int(float(body[k]))
                    if body.get("risk_pct") not in (None, ""):
                        upd["risk_pct"] = round(float(body["risk_pct"]) / 100.0, 5)
                    if not (0 < upd.get("risk_pct", 0) <= 0.2):
                        return self._send(400, json.dumps(
                            {"error": "risk_pct must be 0.05%%..20%% of equity"}),
                            "application/json")
                    data = json.dumps(upd).encode()
                    hdr = {"Content-Type": "application/json"}
                    req = urllib.request.Request(base + "/api/turtle/config", data=data,
                                                 headers=hdr, method="POST")
                    try:
                        urllib.request.urlopen(req, timeout=6).read()
                    except urllib.error.HTTPError as he:
                        if he.code != 409:
                            raise
                        for act in ("stop", "start"):
                            r2 = urllib.request.Request(
                                base + "/api/turtle?action=" + act, data=b"{}",
                                headers=hdr, method="POST")
                            if act == "stop":
                                urllib.request.urlopen(r2, timeout=8).read()
                        req = urllib.request.Request(base + "/api/turtle/config", data=data,
                                                     headers=hdr, method="POST")
                        urllib.request.urlopen(req, timeout=6).read()
                        r3 = urllib.request.Request(base + "/api/turtle?action=start",
                                                    data=b"{}", headers=hdr, method="POST")
                        urllib.request.urlopen(r3, timeout=10).read()
                    try:
                        stt = jget(base + "/api/turtle", timeout=4.0)
                        if (stt.get("state") or "").lower() != "running":
                            urllib.request.urlopen(urllib.request.Request(
                                base + "/api/turtle?action=start", data=b"{}",
                                headers=hdr, method="POST"), timeout=10).read()
                    except Exception:
                        pass
                    return self._send(200, json.dumps(
                        {"engine": "turtle", "file": "go backend",
                         "applied": upd, "applied_count": len(upd)}), "application/json")
                except Exception as exc:
                    return self._send(502, json.dumps({"error": str(exc)}),
                                      "application/json")
            files = {"swing": "control.json", "multimodels": "control_multi.json",
                     "statsmodels": "control_stats.json",
                     "t10s": "control_t10s.json", "t20s": "control_t20s.json",
                     "t45s": "control_t45s.json", "t1m": "control_t1m.json",
                     "t2m": "control_t2m.json"}
            fname = files.get(engine, "control_turtle.json")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception as exc:
                return self._send(400, json.dumps({"error": str(exc)}), "application/json")
            clean = {"ts": int(time.time() * 1000)}
            for k, lo, hi in (("risk_pct", 0.05, 15.0), ("tp_pct", 0.2, 10.0),
                              ("sl_pct", 0.2, 8.0), ("max_positions", 1, 40),
                              ("min_agree", 1, 5), ("max_open_risk_pct", 5, 100),
                              ("min_confidence_pct", 0, 100), ("hold_sec", 5, 3600),
                              ("trail_ratio", 0, 1), ("trail_arm_pct", 0, 10),
                              ("min_liquidity", 0, 5e9),
                              ("lock_trade_pct", 0, 5), ("lock_profit_pct", 0, 10),
                              ("lock_usd", 0, 100000), ("max_hold", 0, 86400),
                              ("scan_every", 0.5, 600), ("conviction_cap", 0.5, 5),
                              ("vol_mult", 0, 10), ("tolerance", 0, 0.05),
                              ("min_stop_frac", 0.0005, 0.05), ("enabled", 0, 1)):
                if k in body and body[k] is not None:
                    try:
                        v = float(body[k])
                    except Exception:
                        return self._send(400, json.dumps({"error": "%s not a number" % k}),
                                          "application/json")
                    if not (lo <= v <= hi):
                        return self._send(400, json.dumps(
                            {"error": "%s must be %s..%s" % (k, lo, hi)}), "application/json")
                    clean[k] = v
            try:
                path = os.path.join(HERE, "state", fname)
                with open(path + ".tmp", "w") as fh:
                    json.dump(clean, fh)
                os.replace(path + ".tmp", path)
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
            return self._send(200, json.dumps({"engine": engine, "file": fname,
                                               "applied": clean,
                                               "applied_count": len(
                                                   [k for k in clean if k != "ts"])}),
                              "application/json")
        if self.path.split("?")[0] != "/api/force":
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        q = parse_qs(urlparse(self.path).query)
        engine = (q.get("engine") or ["all"])[0]
        targets = ([engine] if engine != "all"
                   else ["swing", "multimodels", "t0", "t1", "t2", "t3", "t4"])
        made = []
        for t in targets:
            try:
                path = os.path.join(HERE, "state", "force_%s.json" % t)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as fh:
                    json.dump({"at": int(time.time() * 1000)}, fh)
                made.append(t)
            except Exception:
                pass
        return self._send(200, json.dumps({"forced": made}), "application/json")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/app", "/app/", "/v2"):
            return self._send(200, PAGE.replace("__UI_LIB__", ui_lib()), "text/html")
        if p in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html")
        if p == "/api/klines":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("symbol") or ["BTCUSDT"])[0]
            iv = (q.get("interval") or ["5m"])[0]
            try:
                k = binance_klines(sym, iv, 200)
                return self._send(200, json.dumps({"symbol": sym, "interval": iv,
                                                   "rows": k["rows"],
                                                   "indicators": k.get("indicators") or {}}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p == "/api/path":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("symbol") or [""])[0]
            a = int((q.get("from") or ["0"])[0] or 0)
            b = int((q.get("to") or ["0"])[0] or 0)
            try:
                k = binance_klines(sym, (q.get("iv") or ["5m"])[0], 500)
                rows = [r for r in k["rows"] if a <= r["t"] <= (b or r["t"])]
                if not rows:
                    rows = k["rows"][-60:]
                step = max(1, len(rows) // 120)
                pts = [round(r["c"], 10) for r in rows][::step]
                return self._send(200, json.dumps(
                    {"symbol": sym, "points": pts,
                     "t0": rows[0]["t"], "t1": rows[-1]["t"]}), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p == "/api/signals":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("symbol") or [""])[0].upper()
            al = safe_jget("http://127.0.0.1:8787/api/alerts?limit=800",
                           {"alerts": []}, 6.0,
                           "alert service (8787) is not running in this build")
            ms = safe_jget("http://127.0.0.1:8787/api/models/signals?limit=800",
                           {"signals": []}, 6.0,
                           "signal service (8787) is not running in this build")
            alerts = [a for a in (al.get("alerts") or []) if a.get("symbol") == sym]
            models = [x for x in (ms.get("signals") or []) if x.get("symbol") == sym]
            return self._send(200, json.dumps(
                {"symbol": sym, "alerts": alerts, "models": models,
                 "unavailable": bool(al.get("unavailable") or ms.get("unavailable")),
                 "reason": al.get("reason") or ms.get("reason") or ""}),
                "application/json")
        if p in ("/lab", "/lab/"):
            try:
                with open(os.path.join(HERE, "chart_simple.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        if p in ("/chart", "/chart/"):
            try:
                with open(os.path.join(HERE, "chart_real.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        if p == "/api/models/all":
            out = []
            for fn, fam in (("multi_models.json", "adaptive"),
                            ("stats_models.json", "statistical")):
                try:
                    with open(os.path.join(HERE, "state", fn)) as fh:
                        d = json.load(fh)
                    for m in d.get("models") or []:
                        out.append({"name": m.get("name"), "family": fam,
                                    "parent": d.get("engine") or fam,
                                    "votes": m.get("voting"), "kind": "ta"})
                except Exception:
                    pass
            try:
                sig = jget("http://127.0.0.1:8787/api/models/signals?limit=600",
                           timeout=8.0).get("signals") or []
                counts, dirs = {}, {}
                for x in sig:
                    n = x.get("model")
                    counts[n] = counts.get(n, 0) + 1
                    dirs.setdefault(n, {"buy": 0, "sell": 0})
                    dd = (x.get("direction") or "").upper()
                    dirs[n]["buy" if ("BUY" in dd or "PUMP" in dd) else "sell"] += 1
                for n, c in sorted(counts.items(), key=lambda kv: -kv[1]):
                    out.append({"name": n, "family": "signal", "parent": "backend",
                                "votes": c, "kind": "signal",
                                "buy": dirs[n]["buy"], "sell": dirs[n]["sell"]})
            except Exception:
                pass
            return self._send(200, json.dumps({"count": len(out), "models": out}),
                              "application/json")
        if p == "/api/trading":
            q = urllib.parse.urlparse(self.path)
            act = urllib.parse.parse_qs(q.query).get("action", [""])[0]
            if self.command == "POST":
                if act == "start":
                    st = trading_start()
                elif act == "stop":
                    st = trading_stop()
                else:
                    st = trading_loop_state()
                time.sleep(0.4)
                return self._send(200, json.dumps(trading_loop_state()), "application/json")
            return self._send(200, json.dumps(trading_loop_state()), "application/json")
        if p == "/api/alerts":
            try:
                q = urllib.parse.urlparse(self.path).query
                d = safe_jget("http://127.0.0.1:8787/api/alerts" + ("?" + q if q else ""),
                              {"alerts": [], "count": 0}, 8.0,
                              "alert service (8787) is not running in this build")
                return self._send(200, json.dumps(d), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/votes":
            try:
                d = safe_jget("http://127.0.0.1:8787/api/models/signals?limit=400",
                              {"signals": [], "count": 0}, 8.0,
                              "signal service (8787) is not running in this build")
                return self._send(200, json.dumps(d), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/metrics":
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                n = int((q.get("n", ["30"])[0]) or 30)
                data = metric_sample(n)
                return self._send(200, json.dumps({"series": data, "ts": int(time.time() * 1000)}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:140]}), "application/json")
        if p == "/api/sparklines":
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                n = int((q.get("n", ["24"])[0]) or 24)
                return self._send(200, json.dumps({"series": sparklines(n)}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/version":
            return self._send(200, json.dumps({"build": BUILD_TS, "pid": os.getpid()}),
                              "application/json")
        if p == "/api/selfcheck":
            checks = []
            def chk(name, url, need_key=None):
                try:
                    d = jget(url, timeout=10.0)
                    ok = True
                    if need_key and not (isinstance(d, dict) and d.get(need_key)):
                        ok = False
                    checks.append({"view": name, "ok": ok,
                                   "detail": "ok" if ok else ("empty %s" % need_key)})
                except Exception as exc:
                    checks.append({"view": name, "ok": False, "detail": str(exc)[:90]})
            base = "http://127.0.0.1:8799"
            chk("Dashboard", base + "/api/all", "engines")
            chk("Tape", base + "/api/tape", "fills")
            chk("Signals", base + "/api/opportunities", "opportunities")
            chk("Advisor", base + "/api/opportunities", "thoughts")
            chk("Forecast", "http://127.0.0.1:8790/forecast?symbol=BTCUSDT&side=buy", "prob")
            chk("Market live", base + "/api/universe800", "tickers")
            chk("Models", base + "/api/all", "engines")
            chk("Leaderboard", base + "/api/all", "engines")
            chk("Optimiser", base + "/api/optimizer", "cycles")
            chk("Why", base + "/api/signals?symbol=BTCUSDT", "alerts")
            chk("Review", base + "/api/all", "engines")
            chk("Control", base + "/api/all", "engines")
            return self._send(200, json.dumps({"checks": checks}), "application/json")
        if p == "/api/opt/jobs":
            try:
                return self._send(200, json.dumps(jget("http://127.0.0.1:8795/jobs", timeout=6)),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/model":
            import urllib.parse as _up
            q = _up.parse_qs(_up.urlparse(self.path).query)
            key = (q.get("engine", [""])[0] or "").lower()
            book = None
            try:
                with open(os.path.join(HERE, "state", "pack_%s.json" % key)) as fh:
                    b = json.load(fh)
                book = {"equity": b.get("equity"), "start": b.get("start_equity"),
                        "closed": b.get("trade_count") or len(b.get("trades") or []),
                        "positions": b.get("positions") or [],
                        "trades": (b.get("trades") or [])[-40:],
                        "return_pct": round(100 * ((b.get("equity") or 0) /
                                                   (b.get("start_equity") or 1) - 1), 3)}
            except Exception:
                book = None
            try:
                with open(os.path.join(HERE, "state", "trading.json")) as fh:
                    profile = json.load(fh)
            except Exception:
                profile = None
            try:
                import models_meta
                meta = models_meta.for_engine(key, book, profile)
            except Exception as exc:
                meta = {"key": key, "error": str(exc)[:120]}
            log_lines = []
            for cand in ("sup_%s.log" % key, "%s.log" % key):
                path = os.path.join(HERE, "logs", cand)
                if os.path.exists(path):
                    try:
                        with open(path, "rb") as fh:
                            fh.seek(0, os.SEEK_END)
                            size = fh.tell()
                            fh.seek(max(0, size - 6000))
                            log_lines = fh.read().decode("utf-8", "replace").splitlines()[-40:]
                        break
                    except Exception:
                        pass
            out = {"meta": meta, "book": book, "log": log_lines}
            return self._send(200, json.dumps(out), "application/json")
        if p == "/api/intel":
            try:
                from lib import intel as _intel
                return self._send(200, json.dumps(_intel.snapshot()), "application/json")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/bus":
            import base64 as _b64
            auth = "Basic " + _b64.b64encode(b"cryptalyst:cryptalyst").decode()
            def rab(path):
                req = urllib.request.Request("http://127.0.0.1:15672/api" + path,
                                             headers={"Authorization": auth})
                with urllib.request.urlopen(req, timeout=6) as r:
                    return json.loads(r.read().decode())
            try:
                ov = rab("/overview")
                queues = rab("/queues/%2F")
                out = {"version": ov.get("rabbitmq_version"),
                       "totals": ov.get("object_totals"),
                       "queues": [{"name": q.get("name"),
                                   "messages": q.get("messages"),
                                   "consumers": q.get("consumers"),
                                   "publish_rate": (q.get("message_stats", {}) or {}).get("publish_details", {}).get("rate"),
                                   "deliver_rate": (q.get("message_stats", {}) or {}).get("deliver_get_details", {}).get("rate"),
                                   "durable": q.get("durable")}
                                  for q in queues]}
                return self._send(200, json.dumps(out), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:140]}), "application/json")
        if p == "/api/alert_detail":
            import urllib.parse as _up
            q = _up.parse_qs(_up.urlparse(self.path).query)
            eid = q.get("event_id", [""])[0]
            sym = q.get("symbol", [""])[0].upper()
            out = {"event_id": eid, "symbol": sym}
            try:
                al = jget("http://127.0.0.1:8787/api/alerts?limit=300", timeout=8.0)
                for a in al.get("alerts") or []:
                    if (eid and a.get("event_id") == eid) or (sym and a.get("symbol") == sym):
                        out["alert"] = a
                        sym = a.get("symbol") or sym
                        break
            except Exception as exc:
                out["alert_error"] = str(exc)[:100]
            try:
                fw = jget("http://127.0.0.1:8799/api/flowwindows?symbol=" + sym, timeout=15.0)
                out["flow"] = fw.get("windows") or []
            except Exception:
                out["flow"] = []
            try:
                with open(os.path.join(HERE, "state", "signals.json")) as fh:
                    for row in (json.load(fh).get("rows") or []):
                        if row.get("symbol") == sym:
                            out["signals"] = row
                            break
            except Exception:
                pass
            try:
                out["forecast"] = jget(
                    "http://127.0.0.1:8790/forecast?symbol=%s&side=buy&horizon=3600" % sym,
                    timeout=10.0)
            except Exception:
                out["forecast"] = None
            return self._send(200, json.dumps(out), "application/json")
        if p == "/api/flowwindows":
            import urllib.parse as _up
            q = _up.parse_qs(_up.urlparse(self.path).query)
            sym = (q.get("symbol", ["BTCUSDT"])[0] or "BTCUSDT").upper()
            try:
                d = jget("http://127.0.0.1:8796/klines?symbol=%s&interval=1m&limit=1440" % sym,
                         timeout=12.0)
                rows = d.get("klines") or []
            except Exception:
                rows = []
            try:
                wins = [int(x) for x in (q.get("windows", ["1,5,15,60,240,1440"])[0] or "").split(",") if x.strip()]
            except Exception:
                wins = [1, 5, 15, 60, 240, 1440]
            wins = sorted(set(w for w in wins if 1 <= w <= 20160))[:8]

            def agg(n):
                win = rows[-n:]
                if not win:
                    return None
                buy = sum((r.get("Q") or 0) for r in win)
                total = sum((r.get("q") or 0) for r in win)
                sell = max(0.0, total - buy)
                hi = max((r.get("h") or 0) for r in win)
                lo = min((r.get("l") or 0) for r in win)
                closes = [r.get("c") or 0 for r in win]
                vol = sum((r.get("v") or 0) for r in win)
                return {"window": n, "net": round(buy - sell, 2),
                        "buy": round(buy, 2), "sell": round(sell, 2),
                        "buy_ratio": round(buy / total, 4) if total else 0.5,
                        "range_pct": round(100 * (hi / lo - 1), 3) if lo else None,
                        "chg_pct": round(100 * (closes[-1] / closes[0] - 1), 3) if closes and closes[0] else None,
                        "volume": round(vol, 2),
                        "delta": round(buy - sell, 2)}
            out = {"symbol": sym,
                   "windows": [w for w in (agg(n) for n in wins) if w],
                   "ts": int(time.time() * 1000)}
            return self._send(200, json.dumps(out), "application/json")
        if p == "/api/thinking":
            out = {"ts": int(time.time() * 1000), "sources": {}, "decisions": [],
                   "blocks": {}, "signals": [], "books": {}}
            def grab(url, t=6.0):
                try:
                    return jget(url, timeout=t)
                except Exception:
                    return None
            md = grab("http://127.0.0.1:8796/health")
            if md:
                out["sources"]["market_data"] = {
                    "status": md.get("stats", {}).get("status"),
                    "msgs": md.get("stats", {}).get("msgs"),
                    "series": md.get("kline_series"),
                    "tickers": md.get("mini_tickers"),
                    "prices": md.get("prices"),
                    "violations": md.get("limits", {}).get("violations")}
            bus = grab("http://127.0.0.1:8793/health")
            if bus:
                out["sources"]["signal_bus"] = {"cursor": bus.get("cursor"),
                                                "buffered": bus.get("buffered")}
            fc = grab("http://127.0.0.1:8790/health")
            if fc:
                out["sources"]["forecast"] = {"agents": len(fc.get("agents") or [])}
            try:
                with open(os.path.join(HERE, "state", "risk.json")) as fh:
                    rj = json.load(fh)
                dec = (rj.get("decisions") or [])[-60:]
                out["decisions"] = dec[::-1]
                for d in dec:
                    if not d.get("allowed"):
                        key = (d.get("reason") or "unknown").split("(")[0].strip()[:46]
                        out["blocks"][key] = out["blocks"].get(key, 0) + 1
            except Exception:
                pass
            try:
                with open(os.path.join(HERE, "state", "signals.json")) as fh:
                    sj = json.load(fh)
                rows = sj.get("rows") or []
                rows.sort(key=lambda r: -(r.get("flow_conf") or 0))
                out["signals"] = [{"symbol": r.get("symbol"), "score": r.get("score"),
                                   "flow_conf": r.get("flow_conf"),
                                   "buy_ratio": r.get("buy_ratio")} for r in rows[:6]]
            except Exception:
                pass
            try:
                books = []
                for fn in os.listdir(os.path.join(HERE, "state")):
                    if fn.startswith("pack_") and fn.endswith(".json"):
                        try:
                            with open(os.path.join(HERE, "state", fn)) as fh:
                                books.append(json.load(fh))
                        except Exception:
                            pass
                out["books"] = {"count": len(books),
                                "open": sum(len(b.get("positions") or []) for b in books),
                                "closed": sum(b.get("trade_count") or 0 for b in books),
                                "equity": sum(b.get("equity") or 0 for b in books)}
            except Exception:
                pass
            try:
                with open(os.path.join(HERE, "state", "review.json")) as fh:
                    rv = json.load(fh)
                out["review"] = {"stopped": rv.get("stopped") or [],
                                 "every_s": rv.get("every_s"), "ts": rv.get("ts")}
            except Exception:
                pass
            try:
                out["options"] = {k: v for k, v in __import__("tactics").options(True).items()}
            except Exception:
                pass
            return self._send(200, json.dumps(out), "application/json")
        if p == "/api/review_state":
            try:
                with open(os.path.join(HERE, "state", "review.json")) as fh:
                    return self._send(200, fh.read(), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/risk":
            try:
                import risk as _R
                out = {"snapshot": _R.snapshot(), "limits": _R.DEFAULTS}
                try:
                    with open(os.path.join(HERE, "state", "risk.json")) as fh:
                        out["decisions"] = (json.load(fh).get("decisions") or [])[-40:]
                except Exception:
                    out["decisions"] = []
                return self._send(200, json.dumps(out), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/signals_ext":
            try:
                with open(os.path.join(HERE, "state", "signals.json")) as fh:
                    return self._send(200, fh.read(), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/flows":
            try:
                q = urllib.parse.urlparse(self.path).query
                d = safe_jget("http://127.0.0.1:8796/flows" + ("?" + q if q else ""),
                              {"windows": [], "count": 0}, 20.0,
                              "flow windows need the intelligence producers")
                return self._send(200, json.dumps(d), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/movers":
            try:
                q = urllib.parse.urlparse(self.path).query
                d = jget("http://127.0.0.1:8796/movers" + ("?" + q if q else ""), timeout=20.0)
                return self._send(200, json.dumps(d), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/options":
            import tactics as _T
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q:
                for k, v in q.items():
                    raw = v[0]
                    cur = _T.options(True).get(k)
                    if isinstance(cur, bool):
                        val = raw.lower() in ("1", "true", "yes", "on")
                    elif isinstance(cur, (int, float)):
                        try:
                            val = float(raw)
                        except Exception:
                            continue
                    else:
                        val = raw
                    _T.set_option(k, val)
            return self._send(200, json.dumps(_T.options(True)), "application/json")
        if p == "/api/model_control":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            model = (q.get("model", [""])[0] or "").strip()
            act = (q.get("action", [""])[0] or "").strip()
            path = os.path.join(HERE, "state", "disabled.json")
            try:
                cur = json.load(open(path))
            except Exception:
                cur = {"books": []}
            books = [b for b in (cur.get("books") or []) if b]
            if model and act == "stop" and model not in books:
                books.append(model)
            elif model and act == "start" and model in books:
                books.remove(model)
            with open(path + ".t", "w") as fh:
                json.dump({"books": books, "ts": int(time.time() * 1000)}, fh)
            os.replace(path + ".t", path)
            return self._send(200, json.dumps({"ok": True, "disabled": books}),
                              "application/json")
        if p == "/api/forecast":
            try:
                q = urllib.parse.urlparse(self.path).query
                d = safe_jget("http://127.0.0.1:8790/forecast" + ("?" + q if q else ""),
                              {"forecasts": [], "count": 0}, 8.0,
                              "forecast service (8790) is not running; it needs 40 bars")
                return self._send(200, json.dumps(d), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/forecast_matrix":
            import urllib.parse as _up
            q = _up.parse_qs(_up.urlparse(self.path).query)
            n = int(q.get("n", ["12"])[0])
            syms, sides = [], {}
            try:
                for b in jget(base + "/api/all", timeout=8.0).get("engines") or []:
                    for pos in (b.get("positions") or [])[:2]:
                        s2 = pos.get("symbol")
                        if s2 and s2 not in sides:
                            sides[s2] = (pos.get("side") or "buy").lower()
            except Exception:
                pass
            try:
                with open(os.path.join(HERE, "state", "signals.json")) as fh:
                    for row in (json.load(fh).get("rows") or [])[:n]:
                        s2 = row.get("symbol")
                        if s2 and s2 not in sides:
                            sides[s2] = "buy" if (row.get("score") or 0) >= 0 else "sell"
            except Exception:
                pass
            out = []
            for sym, side in list(sides.items())[:n]:
                try:
                    f = jget("http://127.0.0.1:8790/forecast?symbol=%s&side=%s&horizon=3600"
                             % (sym, side), timeout=12.0)
                    out.append(f)
                except Exception:
                    continue
            return self._send(200, json.dumps({"forecasts": out, "n": len(out)}),
                              "application/json")
        if p == "/api/forecast_state":
            try:
                d = jget("http://127.0.0.1:8790/state", timeout=6.0)
                return self._send(200, json.dumps(d), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p == "/api/opportunities":
            try:
                with open(os.path.join(HERE, "state", "opportunities.json")) as fh:
                    return self._send(200, fh.read(), "application/json")
            except Exception:
                return self._send(200, json.dumps({"opportunities": []}), "application/json")
        if p in ("/stream", "/stream/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            HUB.start()
            q = HUB.register()
            try:
                while True:
                    try:
                        item = q.get(timeout=10)
                    except Exception:
                        self.wfile.write(b": ping\n\n"); self.wfile.flush(); continue
                    self.wfile.write(("data: " + json.dumps(item) + "\n\n").encode())
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                HUB.unregister(q)
            return
        if p == "/api/books":
            out = []
            try:
                files = [f for f in os.listdir(os.path.join(HERE, "state"))
                         if f.startswith("pack_") and f.endswith(".json")]
            except Exception:
                files = []
            for fn in sorted(files):
                try:
                    with open(os.path.join(HERE, "state", fn)) as fh:
                        b = json.load(fh)
                except Exception:
                    continue
                trades = b.get("trades") or []
                start = b.get("start_equity") or 5000
                eq = b.get("equity") or start
                wins = sum(1 for t in trades if (t.get("net_pct") or 0) > 0)
                curve, run = [], 0.0
                for t in trades[-60:]:
                    run += (t.get("net_pct") or 0)
                    curve.append(round(run, 4))
                out.append({
                    "engine": b.get("engine"),
                    "equity": round(eq, 2), "start": round(start, 2),
                    "return_pct": round(100 * (eq / start - 1), 3) if start else 0,
                    "trades": len(trades), "win_pct": round(100 * wins / len(trades), 1) if trades else 0,
                    "avg_net": round(sum((t.get("net_pct") or 0) for t in trades) / len(trades), 4) if trades else 0,
                    "open": len(b.get("positions") or []),
                    "positions": [{"symbol": q.get("symbol"), "side": q.get("side"),
                                   "entry": q.get("entry"), "notional": q.get("notional"),
                                   "mark": q.get("mark")} for q in (b.get("positions") or [])[:20]],
                    "recent": [{"symbol": t.get("symbol"), "side": t.get("side"),
                                "net_pct": t.get("net_pct"), "reason": t.get("reason"),
                                "held_s": t.get("held_s"), "exit_ms": t.get("exit_ms")}
                               for t in trades[-8:]][::-1],
                    "curve": curve,
                    "ts": b.get("ts")})
            total = sum(r["equity"] for r in out)
            start_total = sum(r["start"] for r in out) or 1
            return self._send(200, json.dumps({
                "books": out, "equity": round(total, 2), "start": round(start_total, 2),
                "return_pct": round(100 * (total / start_total - 1), 3),
                "open": sum(r["open"] for r in out),
                "closed": sum(r["trades"] for r in out),
                "up": sum(1 for r in out if r["return_pct"] > 0),
                "down": sum(1 for r in out if r["return_pct"] < 0),
                "ts": int(time.time() * 1000)}), "application/json")
        if p == "/api/pending":
            try:
                with open(os.path.join(HERE, "state", "pending.json")) as fh:
                    return self._send(200, fh.read(), "application/json")
            except Exception:
                return self._send(200, json.dumps({"pending": []}), "application/json")
        if p == "/api/tape":
            try:
                with open(os.path.join(HERE, "state", "tape.json")) as fh:
                    return self._send(200, fh.read(), "application/json")
            except Exception:
                return self._send(200, json.dumps({"fills": []}), "application/json")
        if p == "/api/heartbeat":
            hb = None
            try:
                with open(os.path.join(HERE, "state", "heartbeat.json")) as fh:
                    hb = json.load(fh)
            except Exception:
                hb = None
            # anything older than half a minute is history, not a heartbeat
            if not hb or time.time() * 1000 - float(hb.get("ts") or 0) > 30000:
                hb = live_heartbeat()
            return self._send(200, json.dumps(hb), "application/json")
        if p == "/api/tick":
            q = urllib.parse.urlparse(self.path).query
            qs = urllib.parse.parse_qs(q).get("syms", [""])[0]
            px = TICK["p"]
            if qs:
                px = {k: px[k] for k in qs.split(",") if k in px}
            return self._send(200, json.dumps({"ts": TICK["ts"], "age_ms": int(
                (time.time() - TICK["ts"]) * 1000), "p": px}), "application/json")
        if p in ("/api/symbols", "/api/symbols/all"):
            try:
                rows = all_symbols()
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                needle = (q.get("q", [""])[0] or "").upper()
                if needle:
                    rows = [r for r in rows if needle in r["s"]][:200]
                return self._send(200, json.dumps({"count": len(rows), "symbols": rows}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)[:120]}), "application/json")
        if p in ("/api/universe800", "/api/tickers800"):
            try:
                rows = universe800(800)
                return self._send(200, json.dumps({"count": len(rows), "tickers": rows}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p == "/api/tickers":
            try:
                meta = market_meta()
                out = [{"s": sym, "v": m["liquidity"], "c": m["change"],
                        "price": m["price"], "vol": m["vol_pct"],
                        "qvol": m["volume"], "mcap": m["mcap"]}
                       for sym, m in meta.items()]
                out.sort(key=lambda x: -(x["v"] or 0))
                return self._send(200, json.dumps({"count": len(out), "tickers": out}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p in ("/healthz", "/api/health"):
            out = {"app": "ok", "checks": {}}
            def probe(name, url):
                try:
                    t0 = time.time()
                    d = jget(url, timeout=6.0)
                    out["checks"][name] = {"ok": True, "ms": int((time.time() - t0) * 1000),
                                           "detail": d}
                except Exception as exc:
                    out["checks"][name] = {"ok": False, "error": str(exc)[:120]}
            # health is about the platform this build actually runs. The legacy
            # python backend is not part of it, so it is reported, not required.
            probe("market data", "http://127.0.0.1:8796/health")
            probe("recorder", "http://127.0.0.1:8801/stats")
            probe("explorer", "http://127.0.0.1:8802/api/stats")
            try:
                a = jget("http://127.0.0.1:8799/api/all", timeout=10.0)
                live = sum(1 for e in a.get("engines", []) if e.get("alive"))
                out["checks"]["books"] = {"ok": True, "detail": {"live": live,
                                          "total": len(a.get("engines", []))}}
            except Exception as exc:
                out["checks"]["books"] = {"ok": False, "error": str(exc)[:120]}
            ok = all(c.get("ok") for c in out["checks"].values())
            out["ok"] = ok
            return self._send(200 if ok else 503, json.dumps(out), "application/json")
        if p == "/api/sentinel":
            try:
                with open(os.path.join(HERE, "state", "sentinel.json")) as fh:
                    return self._send(200, fh.read(), "application/json")
            except Exception:
                return self._send(200, json.dumps({"models": {}, "events": []}), "application/json")
        if p == "/api/optimizer":
            try:
                with open(os.path.join(HERE, "state", "optimizer.json")) as fh:
                    return self._send(200, fh.read(), "application/json")
            except Exception:
                return self._send(200, json.dumps({"cycles": []}), "application/json")
        if p == "/api/all":
            try:
                return self._send(200, json.dumps(collect()), "application/json")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        if p == "/health":
            return self._send(200, json.dumps({"status": "ready"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = this machine only, 0.0.0.0 = reachable on the LAN")
    args = ap.parse_args()
    ticker_loop()
    metric_loop(15.0, 30)
    httpd = ThreadingHTTPServer((args.host, args.port), H)
    print("dashboard on http://%s:%d" % (args.host, args.port), flush=True)
    print("MASTER dashboard: http://127.0.0.1:%d" % args.port, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
