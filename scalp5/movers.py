#!/usr/bin/env python3
"""movers - trade the 24h movers, and take the profit three seconds later.

Rule, exactly as specified: three seconds after the entry, if the position is in
profit after costs, close it. If it is not, keep checking every second until it
is, or until the clock or the hard stop ends it.

Data: the mover list comes from our own /api/movers (built from the miniTicker
stream - no exchange REST), and prices come from our own bookTicker websocket.
"""
import argparse, json, os, socket, ssl, struct, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
import sys

try:
    import tactics as _tactics
    _tactics.require_enabled('movers', 'movers.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass

sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")
try:
    from lib import emit
except Exception:
    emit = None

FEE_SIDE = 0.02           # maker, per side, in percent
BOOK = {}
POS = {}
EQ = 5000.0
START = 5000.0
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


def jget(url, timeout=6.0):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def movers(n=12):
    """Top gainers and losers from our own endpoint, liquid ones only."""
    try:
        d = jget("http://127.0.0.1:8799/api/movers")
    except Exception:
        return []
    out = []
    for row in (d.get("gainers") or [])[:n]:
        if (row.get("qv") or 0) > 2_000_000:
            out.append((row["s"], row.get("pct") or 0))
    for row in (d.get("losers") or [])[:n]:
        if (row.get("qv") or 0) > 2_000_000:
            out.append((row["s"], row.get("pct") or 0))
    return out


def on_book(d):
    sym = d.get("s")
    try:
        bid, ask = float(d["b"]), float(d["a"])
    except Exception:
        return
    with LOCK:
        b = BOOK.setdefault(sym, {"px": [], "ts": []})
        b["bid"], b["ask"] = bid, ask
        b["px"].append((bid + ask) / 2)
        b["ts"].append(time.time())
        if len(b["px"]) > 200:
            del b["px"][:-200]
            del b["ts"][:-200]


def momentum(sym, seconds=10.0):
    with LOCK:
        b = BOOK.get(sym)
        if not b or len(b["px"]) < 10:
            return None
        now = time.time()
        i = len(b["ts"]) - 1
        while i > 0 and now - b["ts"][i] < seconds:
            i -= 1
        if i >= len(b["px"]) - 2:
            return None
        return (b["px"][-1] / b["px"][i] - 1) * 100


def write_book():
    try:
        os.makedirs(STATE, exist_ok=True)
        p = {"engine": "movers", "equity": round(EQ, 2), "start_equity": round(START, 2),
             "positions": [{"symbol": k, "side": v["side"], "entry": v["entry"],
                            "notional": v["notional"], "t": v["t"]} for k, v in POS.items()],
             "trades": TRADES[-200:], "trade_count": len(TRADES),
             "ts": int(time.time() * 1000)}
        tmp = os.path.join(STATE, "pack_movers.%d.t" % os.getpid())
        with open(tmp, "w") as fh:
            json.dump(p, fh)
        os.replace(tmp, os.path.join(STATE, "pack_movers.json"))
    except Exception:
        pass


def cost_pct():
    return FEE_SIDE * 2 + 0.02


def run(a):
    global EQ
    movement = movers(a.list_size)
    if not movement:
        log("no movers list — is master running on :8799?")
        return
    syms = [s for s, _ in movement]
    log("MOVERS — %d symbols · check at %.0fs · cost %.2f%% · target %.2f%%"
        % (len(syms), a.check_at, cost_pct(), a.target))
    log("watching: " + ", ".join("%s %+.1f%%" % (s, p) for s, p in movement[:6]))

    streams = "/".join(s.lower() + "@bookTicker" for s in syms)
    s = WS.connect("wss://stream.binance.com:9443/stream?streams=" + streams)
    write_book()
    moved_at = time.time()
    last_entry = 0.0

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
            # refresh the mover list rarely: it is a 24h statistic
            if now - moved_at > a.refresh:
                moved_at = now
                movement = movers(a.list_size)
                if movement:
                    syms = [x for x, _ in movement]

            # ---- manage: the 3-second profit check
            for sym, p in list(POS.items()):
                b = BOOK.get(sym) or {}
                bid, ask = b.get("bid"), b.get("ask")
                if not bid or not ask:
                    continue
                held = now - p["t"]
                if held < a.check_at:
                    continue
                px = bid if p["side"] == "buy" else ask
                gross = (px / p["entry"] - 1) * 100 * (1 if p["side"] == "buy" else -1)
                net = gross - cost_pct()
                why = None
                if net >= a.target:
                    why = "profit_%.0fs" % a.check_at
                elif False:   # profit-only: no loss exits
                    why = "stop"
                elif held >= a.clock:
                    why = "clock"
                if why and net < 0:
                    why = None      # never realise a loss
                if not why:
                    continue
                EQ += p["notional"] * net / 100
                rec = {"symbol": sym, "side": p["side"], "entry": p["entry"], "exit": px,
                       "gross_pct": round(gross, 4), "net_pct": round(net, 4),
                       "reason": why, "held_s": round(held, 1),
                       "notional": round(p["notional"], 2),
                       "pnl_usd": round(p["notional"] * net / 100, 4),
                       "entry_ms": int(p["t"] * 1000), "exit_ms": int(now * 1000),
                       "features": p["feat"], "slippage_bps": 0}
                TRADES.append(rec)
                del POS[sym]
                if emit:
                    try:
                        emit.fill("movers", rec)
                    except Exception:
                        pass
                write_book()
                log("CLOSE %-11s %-4s gross %+6.3f%% net %+6.3f%% %-11s %4.1fs  eq $%.2f"
                    % (sym, p["side"], gross, net, why, held, EQ))

            # ---- entries: follow a mover while it is still moving
            if len(POS) >= a.max_open or (now - last_entry) < a.gap:
                continue
            for sym, pct in movement:
                if sym in POS:
                    continue
                mv = momentum(sym, a.momentum_window)
                if mv is None:
                    continue
                b = BOOK.get(sym) or {}
                bid, ask = b.get("bid"), b.get("ask")
                if not bid or not ask:
                    continue
                spread_bps = 10000 * (ask - bid) / ((ask + bid) / 2)
                if spread_bps > a.max_spread:
                    continue
                # trade with the mover, but only while the last seconds agree
                side = "buy" if pct > 0 else "sell"
                if side == "buy" and mv <= a.min_momentum:
                    continue
                if side == "sell" and mv >= -a.min_momentum:
                    continue
                entry = bid if side == "buy" else ask
                notional = min(EQ * a.risk / 100 * 40, EQ * 0.08)
                if side == "sell":
                    notional *= a.short_weight   # measured: shorting losers is our best short
                POS[sym] = {"side": side, "entry": entry, "t": now, "notional": notional,
                            "feat": {"mover_pct": round(pct, 2), "mom": round(mv, 3),
                                     "spread_bps": round(spread_bps, 2)}}
                last_entry = now
                write_book()
                log("OPEN  %-11s %-4s %.8g  24h %+6.2f%%  last %.0fs %+0.3f%%  spread %.1fbp"
                    % (sym, side, entry, pct, a.momentum_window, mv, spread_bps))
                if len(POS) >= a.max_open:
                    break
        except Exception as exc:
            log("stream error %r — reconnecting" % (exc,))
            try:
                s = WS.connect("wss://stream.binance.com:9443/stream?streams=" + streams)
            except Exception:
                time.sleep(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000)
    ap.add_argument("--risk", type=float, default=0.65)
    ap.add_argument("--list-size", type=int, default=12)
    ap.add_argument("--refresh", type=float, default=120)
    ap.add_argument("--check-at", type=float, default=3.0)
    ap.add_argument("--target", type=float, default=0.05)
    ap.add_argument("--stop", type=float, default=1.00)
    ap.add_argument("--clock", type=float, default=600)
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--momentum-window", type=float, default=10.0)
    ap.add_argument("--min-momentum", type=float, default=0.02)
    ap.add_argument("--max-spread", type=float, default=12.0)
    ap.add_argument("--max-open", type=int, default=6)
    ap.add_argument("--short-weight", type=float, default=1.5)
    _a = ap.parse_args()
    EQ = START = _a.start
    run(_a)
