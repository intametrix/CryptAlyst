#!/usr/bin/env python3
"""grid - harvest oscillation instead of predicting direction.

The one strategy from the article we did not have. A grid needs no forecast: it
places buy levels below a reference price and sell levels above it, and every
round trip between adjacent levels earns the spacing minus fees.

Design notes that matter:
  - maker only (0.02%/side). A taker grid is a fee pump.
  - the grid is re-anchored when price leaves the band, not on a timer
  - spacing is set from realised volatility, so quiet pairs get tight grids and
    wild pairs get wide ones
  - a fill is simulated only when the book actually trades through the level
"""
import argparse, json, os, signal, socket, ssl, struct, sys, threading, time

try:
    import tactics as _tactics
    _tactics.require_enabled('grid', 'grid.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")
try:
    from lib import emit
except Exception:
    emit = None

FEE_SIDE = 0.02
EQ = 5000.0
START = 5000.0
BOOK = {}
GRIDS = {}
TRADES = []
LOCK = threading.RLock()


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


class WS:
    @staticmethod
    def connect(url):
        host, path = url.split("://", 1)[1].split("/", 1)
        host, port = (host.split(":") + ["443"])[:2]
        raw = socket.create_connection((host, int(port)), timeout=15)
        s = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                   "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host)).encode())
        b = b""
        while b"\r\n\r\n" not in b:
            b += s.recv(4096)
        return s

    @staticmethod
    def read(s):
        def rd(n):
            b = b""
            while len(b) < n:
                c = s.recv(n - len(b))
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
    def pong(s, payload):
        mask = os.urandom(4)
        s.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask +
                  bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def jget(u, t=6.0):
    import urllib.request
    with urllib.request.urlopen(u, timeout=t) as r:
        return json.loads(r.read().decode())


def write_book():
    try:
        os.makedirs(STATE, exist_ok=True)
        rows = []
        for sym, g in GRIDS.items():
            for lv in g["levels"]:
                if lv.get("pos"):
                    rows.append({"symbol": sym, "side": lv["pos"]["side"],
                                 "entry": lv["pos"]["entry"], "notional": lv["pos"]["notional"],
                                 "t": lv["pos"]["t"], "level": lv["px"]})
        p = {"engine": "grid", "equity": round(EQ, 2), "start_equity": round(START, 2),
             "positions": rows, "trades": TRADES[-200:], "trade_count": len(TRADES),
             "ts": int(time.time() * 1000)}
        tmp = os.path.join(STATE, "pack_grid.%d.t" % os.getpid())
        with open(tmp, "w") as fh:
            json.dump(p, fh)
        os.replace(tmp, os.path.join(STATE, "pack_grid.json"))
    except Exception:
        pass


def vol_pct(sym, n=60):
    """Realised range as a percent, used to size the grid spacing."""
    try:
        d = jget("http://127.0.0.1:8796/klines?symbol=%s&interval=1m&limit=%d" % (sym, n))
        bars = d.get("klines") or []
        if len(bars) < 10:
            return None
        spans = [100 * (b["h"] / b["l"] - 1) for b in bars if b.get("l")]
        spans.sort()
        return spans[len(spans) // 2]        # median bar range
    except Exception:
        return None


def build_grid(sym, mid, spacing_pct, levels, notional):
    g = {"mid": mid, "spacing": spacing_pct / 100.0, "levels": [], "anchor_t": time.time()}
    for i in range(1, levels + 1):
        for side, sign in (("buy", -1), ("sell", 1)):
            px = mid * (1 + sign * i * spacing_pct / 100.0)
            g["levels"].append({"px": px, "side": side, "pos": None, "i": i})
    g["notional"] = notional
    return g


def anchor(a, syms):
    """Pick symbols that are ranging, and build a grid around the current mid."""
    picked = 0
    for sym in syms:
        if picked >= a.max_symbols:
            break
        if sym in GRIDS:
            continue
        v = vol_pct(sym)
        if v is None:
            continue
        if v < a.min_range or v > a.max_range:
            continue                    # too dead to fill, or too wild to hold
        b = BOOK.get(sym)
        if not b:
            continue
        mid = (b["bid"] + b["ask"]) / 2
        spacing = max(a.min_spacing, min(a.max_spacing, v * a.spacing_mult))
        costs = FEE_SIDE * 2 + 0.02
        if spacing < costs * 1.5:
            continue                    # a grid step below cost is a fee pump
        notional = min(EQ * a.risk / 100 * 40, EQ * 0.05)
        GRIDS[sym] = build_grid(sym, mid, spacing, a.levels, notional)
        picked += 1
        log("GRID  %-11s mid %.8g  spacing %.3f%%  %d levels  (60m range %.3f%%)"
            % (sym, mid, spacing, a.levels * 2, v))


def on_book(d):
    sym = d.get("s")
    try:
        bid, ask = float(d["b"]), float(d["a"])
    except Exception:
        return
    with LOCK:
        b = BOOK.setdefault(sym, {})
        b["bid"], b["ask"] = bid, ask


class Gate:
    """A clock gate: true at most once per `every` seconds."""

    def __init__(self, every):
        self.every = max(0.0, float(every))
        self.last = None

    def due(self, now):
        if self.last is None or (now - self.last) >= self.every:
            self.last = now
            return True
        return False


def should_write(gate, now, fills=0):
    """A fill is written at once; otherwise the pack is written on the clock."""
    if fills:
        return True
    return gate.due(now)


def evaluate(now, a):
    """One pass over the live grids: fills and closes. Returns the fill count."""
    global EQ
    fills = 0
    for sym, g in list(GRIDS.items()):
        b = BOOK.get(sym)
        if not b:
            continue
        mid = (b["bid"] + b["ask"]) / 2
        # re-anchor when price walks out of the band
        edge = g["spacing"] * a.levels
        if abs(mid / g["mid"] - 1) > edge * 1.2:
            log("REANCHOR %-10s %.3f%% from mid" % (sym, 100 * (mid / g["mid"] - 1)))
            del GRIDS[sym]
            continue
        for lv in g["levels"]:
            if lv["pos"] is None:
                # a buy level fills when the market trades down to it
                if lv["side"] == "buy" and b["ask"] <= lv["px"]:
                    lv["pos"] = {"side": "buy", "entry": lv["px"], "t": now,
                                 "notional": g["notional"]}
                elif lv["side"] == "sell" and b["bid"] >= lv["px"]:
                    lv["pos"] = {"side": "sell", "entry": lv["px"], "t": now,
                                 "notional": g["notional"]}
            else:
                p = lv["pos"]
                # close against the neighbouring level on the other side
                target = p["entry"] * (1 + g["spacing"] * (1 if p["side"] == "buy" else -1))
                hit = (b["bid"] >= target) if p["side"] == "buy" else (b["ask"] <= target)
                if not hit:
                    continue
                gross = (target / p["entry"] - 1) * 100 * (1 if p["side"] == "buy" else -1)
                net = gross - FEE_SIDE * 2 - 0.02
                if net <= 0:
                    continue      # never realise a loss, by directive
                EQ += p["notional"] * net / 100
                rec = {"symbol": sym, "side": p["side"], "entry": p["entry"],
                       "exit": target, "gross_pct": round(gross, 4),
                       "net_pct": round(net, 4), "reason": "grid",
                       "held_s": round(now - p["t"], 1),
                       "notional": round(p["notional"], 2),
                       "pnl_usd": round(p["notional"] * net / 100, 4),
                       "entry_ms": int(p["t"] * 1000), "exit_ms": int(now * 1000),
                       "features": {"spacing_pct": round(g["spacing"] * 100, 3),
                                    "level": lv["i"]},
                       "slippage_bps": 0}
                TRADES.append(rec)
                fills += 1
                lv["pos"] = None
                if emit:
                    try:
                        emit.fill("grid", rec)
                    except Exception:
                        pass
                log("FILL  %-11s %-4s step %+6.3f%% net %+6.3f%%  eq $%.2f"
                    % (sym, rec["side"], gross, net, EQ))
    return fills


def main_loop(a, syms):
    global EQ
    streams = "/".join(s.lower() + "@bookTicker" for s in syms)
    s = WS.connect("wss://stream.binance.com:9443/stream?streams=" + streams)
    write_book()
    last_anchor = 0.0
    eval_gate = Gate(getattr(a, "eval_every", 0.10))
    write_gate = Gate(getattr(a, "write_every", 1.0))
    while True:
        try:
            op, payload = WS.read(s)
            if op == 0x9:
                WS.pong(s, payload); continue
            if op != 0x1:
                continue
            env = json.loads(payload.decode("utf-8", "replace"))
            d = env.get("data") or {}
            if d.get("s"):
                on_book(d)

            now = time.time()
            if now - last_anchor > a.reanchor:
                last_anchor = now
                anchor(a, syms)

            # The stream sends one message per book change across up to 300
            # symbols, so this loop runs hundreds of times a second. Evaluating
            # the grids and rewriting the pack on every message was the burn:
            # measured 0.275 ms of pack write plus 0.017 ms of evaluation per
            # message, which is most of a core at stream rate. Both now run on a
            # clock - evaluation ten times a second, the pack once a second -
            # while a fill is still written immediately, because a fill is news.
            fills = 0
            if eval_gate.due(now):
                with LOCK:
                    fills = evaluate(now, a)
            if should_write(write_gate, now, fills):
                with LOCK:
                    write_book()
        except Exception as exc:
            log("stream error %r — reconnecting" % (exc,))
            try:
                s = WS.connect("wss://stream.binance.com:9443/stream?streams=" + streams)
            except Exception:
                time.sleep(2)


def _flush_and_exit(signum, frame):
    """SIGTERM/SIGINT: persist the pack, then exit.

    Before this, a restart dropped every in-memory grid position because the
    pack was only written on the message cadence and nothing flushed on exit.
    This handler makes a restart flush the working state first.
    """
    try:
        write_book()
        log("SIG%d — pack written, exiting" % signum)
    finally:
        raise SystemExit(0)


def run(a):
    try:
        d = jget("http://127.0.0.1:8796/streamprices")
        syms = sorted((d.get("p") or {}).keys())[:a.symbols]
    except Exception as exc:
        log("no symbol list: %r" % (exc,))
        return
    log("GRID — %d symbols · maker only · spacing %.2f–%.2f%% · %d levels a side"
        % (len(syms), a.min_spacing, a.max_spacing, a.levels))
    main_loop(a, syms)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000)
    ap.add_argument("--risk", type=float, default=0.65)
    ap.add_argument("--symbols", type=int, default=60)
    ap.add_argument("--max-symbols", type=int, default=6)
    ap.add_argument("--levels", type=int, default=6)
    ap.add_argument("--min-range", type=float, default=0.15)
    ap.add_argument("--max-range", type=float, default=1.20)
    ap.add_argument("--spacing-mult", type=float, default=0.8)
    ap.add_argument("--min-spacing", type=float, default=0.08)
    ap.add_argument("--max-spacing", type=float, default=0.60)
    ap.add_argument("--reanchor", type=float, default=300)
    ap.add_argument("--eval-every", type=float, default=0.10)
    ap.add_argument("--write-every", type=float, default=1.0)
    _a = ap.parse_args()
    EQ = START = _a.start
    signal.signal(signal.SIGTERM, _flush_and_exit)
    signal.signal(signal.SIGINT, _flush_and_exit)
    run(_a)
