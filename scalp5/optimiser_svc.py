#!/usr/bin/env python3
"""optimiser_svc - per-model backtest optimiser with live progress.

POST /run?model=donchian55   start a job (returns immediately)
GET  /jobs                   [{model, progress, status, result}]
GET  /result?model=X         the finished result
POST /apply?model=X          write the winning params for the engine to read

The backtest replays real 1m klines for the model's own basket, applies its rule,
and grid-searches lock/stop/clock on realised net PnL after fees.
"""
import json, os, threading, time, urllib.request, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lib import solver

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
# first-class fee model: fee + slippage with provenance (solver AMEND)
FEE_MODEL = solver.fee_model(fee_bps=20.0, slippage_bps=0.0, provenance="hand-constant")
FEE = FEE_MODEL["round_trip_bps"] / 100.0   # percent (was 0.2)
JOBS = {}
LOCK = threading.Lock()


def code_hash():
    """Hash of this optimiser source, so a recommendation names its code."""
    try:
        src = open(os.path.join(HERE, "optimiser_svc.py"), "rb").read()
        return hashlib.sha1(src).hexdigest()[:12]
    except Exception:
        return "unknown"

GRID = [{"lock": 0.4, "stop": 1.0, "clock": 300},
        {"lock": 0.5, "stop": 0.8, "clock": 600},
        {"lock": 0.6, "stop": 1.2, "clock": 900},
        {"lock": 0.8, "stop": 1.5, "clock": 1200}]


def jget(u, t=10.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "opt/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def klines(sym, n=500):
    d = jget("https://api.binance.com/api/v3/klines?symbol=%s&interval=1m&limit=%d" % (sym, n))
    return [{"o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in d]


def ema(v, n):
    k = 2.0 / (n + 1); e = v[0]
    for x in v[1:]:
        e = x * k + e * (1 - k)
    return e


def rsi(cl, n=14):
    g = l = 0.0
    for i in range(max(1, len(cl) - n), len(cl)):
        ch = cl[i] - cl[i - 1]
        if ch > 0: g += ch
        else: l -= ch
    if l == 0: return 100.0
    return 100 - 100 / (1 + (g / n) / (l / n))


def atr(bs, n=14):
    s = c = 0
    for i in range(max(1, len(bs) - n), len(bs)):
        s += max(bs[i]["h"] - bs[i]["l"], abs(bs[i]["h"] - bs[i - 1]["c"]), abs(bs[i - 1]["c"] - bs[i]["l"]))
        c += 1
    return s / max(1, c)


def rule(name, bs, i):
    """Signal from the bar series up to index i."""
    w = bs[:i + 1]
    if len(w) < 60:
        return None
    cl = [b["c"] for b in w]
    c = cl[-1]
    if name == "donchian55":
        hi = max(b["h"] for b in w[-55:]); lo = min(b["l"] for b in w[-55:])
        return "buy" if c >= hi else "sell" if c <= lo else None
    if name == "boll_squeeze":
        s = cl[-20:]; m = sum(s) / 20
        sd = (sum((x - m) ** 2 for x in s) / 20) ** 0.5
        if sd == 0: return None
        if c > m + 2 * sd: return "buy"
        if c < m - 2 * sd: return "sell"
        return None
    if name == "rsi2":
        r = rsi(cl[-20:], 2)
        return "buy" if r < 10 else "sell" if r > 90 else None
    if name == "vwap_revert":
        pv = sum(b["c"] * b["v"] for b in w[-30:]); vv = sum(b["v"] for b in w[-30:]) or 1
        vw = pv / vv
        return "buy" if c < vw * 0.994 else "sell" if c > vw * 1.006 else None
    if name == "macd_trend":
        f = ema(cl[-40:], 12) - ema(cl[-60:], 26)
        p = ema(cl[-41:-1], 12) - ema(cl[-61:-1], 26)
        return "buy" if p <= 0 < f else "sell" if p >= 0 > f else None
    if name == "vol_break":
        avg = sum(b["v"] for b in w[-20:-1]) / 19 or 1
        b = w[-1]
        if b["v"] < 3 * avg: return None
        rng = b["h"] - b["l"] or 1
        return "buy" if (b["c"] - b["l"]) / rng > 0.7 else "sell" if (b["h"] - b["c"]) / rng > 0.7 else None
    if name == "ema_pullback":
        e20 = ema(cl[-40:], 20); e50 = ema(cl[-80:], 50)
        if e20 > e50 and c > e50 and abs(c - e20) / e20 < 0.002: return "buy"
        if e20 < e50 and c < e50 and abs(c - e20) / e20 < 0.002: return "sell"
        return None
    if name == "atr_channel":
        m = ema(cl[-40:], 20); a = atr(w, 14)
        if a <= 0: return None
        return "buy" if c > m + 1.5 * a else "sell" if c < m - 1.5 * a else None
    if name == "orb":
        hi = max(b["h"] for b in w[-30:]); lo = min(b["l"] for b in w[-30:])
        return "buy" if c > hi * 1.0005 else "sell" if c < lo * 0.9995 else None
    return None


def simulate(series, name, params):
    lock, stop, clock = params["lock"], params["stop"], params["clock"]
    trades = []
    pos = None
    for i in range(60, len(series)):
        px = series[i]["c"]
        if pos:
            if pos["side"] == "buy":
                chg = (px / pos["entry"] - 1) * 100
            else:
                chg = (1 - px / pos["entry"]) * 100
            held = i - pos["i"]
            reason = None
            if chg >= lock: reason = "lock"
            elif False: reason = "stop"   # profit-only: no loss exits
            elif held >= clock: reason = "clock"
            if reason:
                trades.append(chg - FEE)
                pos = None
        if not pos:
            side = rule(name, series, i)
            if side:
                pos = {"side": side, "entry": px, "i": i}
    if not trades:
        return None
    wins = sum(1 for t in trades if t > 0)
    return {"trades": len(trades), "win_pct": 100 * wins / len(trades),
            "avg_net": sum(trades) / len(trades), "total": sum(trades),
            "best": max(trades), "worst": min(trades), "nets": trades}


def job(model):
    job = JOBS[model]
    try:
        job["status"] = "running"; job["progress"] = 3
        uni = jget("http://127.0.0.1:8799/api/universe800", 12).get("tickers") or []
        syms = [t["s"] for t in uni if t.get("s", "").endswith("USDT")][:6]
        series = {}
        for k, s in enumerate(syms):
            try:
                series[s] = klines(s, 500)
            except Exception:
                continue
            job["progress"] = 3 + int(40 * (k + 1) / max(1, len(syms)))
            job["note"] = "loaded %d/%d symbols" % (k + 1, len(syms))
        results = []
        for gi, params in enumerate(GRID):
            if not solver.bounds_ok(params):
                continue
            train_nets = []
            val_nets = []
            for s, ser in series.items():
                cut = int(len(ser) * 0.7)
                tr = simulate(ser[:cut], model, params)
                va = simulate(ser[cut:], model, params)
                if tr:
                    train_nets += tr.get("nets") or []
                if va:
                    val_nets += va.get("nets") or []
            obj = solver.objective(train_nets)
            if obj is not None:
                results.append({"params": params, "objective": obj,
                                "validate_objective": solver.objective(val_nets),
                                "train_trades": len(train_nets),
                                "validate_trades": len(val_nets)})
            job["progress"] = 45 + int(50 * (gi + 1) / len(GRID))
            job["note"] = "grid %d/%d" % (gi + 1, len(GRID))
        results.sort(key=lambda r: r["objective"], reverse=True)
        best = results[0] if results else None
        rec = None
        if best:
            rec = solver.recommend(
                model, best["params"], best["objective"],
                {"validate_objective": best["validate_objective"],
                 "validate_trades": best["validate_trades"]},
                code_hash=code_hash(), budget=len(GRID),
                priors={"lock_pct": 0.4}, window="walk-forward 70/30",
                tier="backtest_only")
        job["result"] = {"model": model, "grid": results, "best": rec,
                         "symbols": list(series), "ts": int(time.time() * 1000),
                         "fee_model": FEE_MODEL}
        job["status"] = "done"; job["progress"] = 100; job["note"] = "complete"
    except Exception as exc:
        job["status"] = "failed"; job["error"] = str(exc)[:200]; job["progress"] = 100


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
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
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path == "/jobs":
            return self._send(200, {"jobs": JOBS})
        if u.path == "/result":
            m = (q.get("model", [""])[0])
            return self._send(200, JOBS.get(m) or {"status": "idle"})
        if u.path == "/health":
            return self._send(200, {"ok": True, "service": "optimiser_svc", "jobs": len(JOBS)})
        return self._send(404, {"error": "not found"})
    def do_POST(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        m = (q.get("model", [""])[0])
        if u.path == "/run":
            if not m:
                return self._send(400, {"error": "model required"})
            with LOCK:
                if JOBS.get(m, {}).get("status") == "running":
                    return self._send(200, JOBS[m])
                JOBS[m] = {"model": m, "status": "queued", "progress": 0, "result": None}
            threading.Thread(target=job, args=(m,), daemon=True).start()
            return self._send(200, {"started": m})
        if u.path == "/apply":
            j = JOBS.get(m) or {}
            best = (j.get("result") or {}).get("best")
            if not best:
                return self._send(400, {"error": "no result to apply"})
            p = os.path.join(STATE, "params_%s.json" % m)
            with open(p + ".t", "w") as fh:
                json.dump({"params": best["params"], "evidence": best,
                           "ts": int(time.time() * 1000)}, fh)
            os.replace(p + ".t", p)
            return self._send(200, {"applied": m, "params": best["params"]})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8795)
    a = ap.parse_args()
    print("optimiser service on :%d" % a.port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
