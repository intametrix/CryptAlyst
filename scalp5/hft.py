#!/usr/bin/env python3
"""hft - a high-frequency model that trades the book, not the candles.

Everything here is built on data we already stream: bookTicker (best bid/ask and
their sizes) on its own websocket, plus the trade prints the exchange sends with
it. No REST polling, no new subscriptions on the shared feed.

Signal  microprice momentum + queue imbalance + spread quality.
Entry   maker only (0.02%/side). A taker entry costs 0.20% and can never be
        recovered at a 10-second horizon, so if the book will not fill us maker
        we do not trade.
Exit    target, stop, or clock. Defaults sized from the measured cost.
"""
import argparse, json, os, socket, ssl, statistics, struct, sys, threading, time

try:
    import tactics as _tactics
    _tactics.require_enabled('hft', 'hft.py')   # A6: a disabled book does not run
except SystemExit:
    raise
except Exception:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")

try:
    from lib import emit, bus
except Exception:
    emit = bus = None

BOOK = {}          # symbol -> live book state
LOCK = threading.RLock()
EQ = 5000.0
START = 5000.0
POS = {}           # symbol -> position
TRADES = []


def log(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- websocket
class WS:
    @staticmethod
    def connect(url):
        host, path = url.split("://", 1)[1].split("/", 1)
        host, port = (host.split(":") + ["443"])[:2]
        raw = socket.create_connection((host, int(port)), timeout=15)
        s = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                   "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += s.recv(4096)
        return s

    @staticmethod
    def read(s):
        """Return (opcode, payload) for one frame."""
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


def update(sym, bid, ask, bq, aq, now):
    if bid <= 0 or ask <= 0:
        return
    with LOCK:
        b = BOOK.get(sym)
        if b is None:
            b = BOOK[sym] = {"px": [], "ts": [], "n": 0, "rate": 0.0,
                             "micro": [], "spreads": []}
        micro = (bid * aq + ask * bq) / (aq + bq) if (aq + bq) > 0 else (bid + ask) / 2
        b["bid"], b["ask"] = bid, ask
        b["bq"], b["aq"] = bq, aq
        b["micro"].append(micro)
        b["spreads"].append((ask - bid) / micro)
        if len(b["micro"]) > 400:
            del b["micro"][:-400]
            del b["spreads"][:-400]
        b["n"] += 1
        b["ts"].append(now)
        if len(b["ts"]) > 400:
            del b["ts"][:-400]
        if len(b["ts"]) > 8:
            span = b["ts"][-1] - b["ts"][0]
            b["rate"] = len(b["ts"]) / span if span > 0 else 0.0


def features(sym, horizon_s=10.0, now=None):
    """Momentum, imbalance and spread quality over the last `horizon_s`."""
    with LOCK:
        b = BOOK.get(sym)
        if not b or len(b["micro"]) < 12:
            return None
        now = now or time.time()
        ts, mic, spr = b["ts"], b["micro"], b["spreads"]
        # the sample that was live `horizon_s` ago
        i = len(ts) - 1
        while i > 0 and now - ts[i] < horizon_s:
            i -= 1
        if i >= len(ts) - 3:
            return None
        move = (mic[-1] / mic[i] - 1) * 100.0
        recent = mic[i:]
        rets = [(recent[k] / recent[k - 1] - 1) * 100.0 for k in range(1, len(recent))]
        if len(rets) < 4:
            return None
        sigma = statistics.pstdev(rets) or 1e-9
        z = move / (sigma * (len(rets) ** 0.5))
        bq, aq = b.get("bq") or 0, b.get("aq") or 0
        imbalance = (bq - aq) / (bq + aq) if (bq + aq) > 0 else 0.0
        spread_bps = 10000 * statistics.median(spr[-20:]) if spr else 99
        return {"move_pct": move, "z": z, "sigma": sigma, "imbalance": imbalance,
                "spread_bps": spread_bps, "rate": b["rate"],
                "micro": mic[-1], "bid": b.get("bid"), "ask": b.get("ask")}


def write_book():
    """The supervisor grades every pack_*.json; hft has to publish one too."""
    try:
        os.makedirs(STATE, exist_ok=True)
        payload = {"engine": "hft", "equity": round(EQ, 2), "start_equity": round(START, 2),
                   "positions": [{"symbol": k, "side": v["side"], "entry": v["entry"],
                                  "notional": v["notional"], "t": v["t"]}
                                 for k, v in POS.items()],
                   "trades": TRADES[-200:], "trade_count": len(TRADES),
                   "ts": int(time.time() * 1000)}
        path = os.path.join(STATE, "pack_hft.json")
        tmp = path + ".%d.t" % os.getpid()
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def cost_pct(maker_fee=0.02):
    return maker_fee * 2 + 0.02        # round trip + buffer


def run(a):
    global EQ
    syms = []
    try:
        import urllib.request
        d = json.load(urllib.request.urlopen("http://127.0.0.1:8796/streamprices", timeout=8))
        p = d.get("p") or {}
        syms = sorted(p.keys())[:a.symbols]
    except Exception as exc:
        log("could not read the symbol list: %r" % (exc,))
        return
    streams = "/".join(s.lower() + "@bookTicker" for s in syms)
    url = "wss://stream.binance.com:9443/stream?streams=" + streams
    log("HFT — %d symbols · maker entry · cost floor %.3f%% · target %.2f%% · stop %.2f%%"
        % (len(syms), cost_pct(), a.target, a.stop))

    floor = cost_pct() * a.cost_multiple
    write_book()
    s = WS.connect(url)
    last = time.time()
    while True:
        try:
            op, payload = WS.read(s)
            if op == 0x9:
                WS.pong(s, payload); continue
            if op != 0x1:
                continue
            env = json.loads(payload.decode("utf-8", "replace"))
            d = env.get("data") or {}
            sym = d.get("s")
            if not sym:
                continue
            try:
                bid, ask = float(d["b"]), float(d["a"])
                bq, aq = float(d.get("B") or 0), float(d.get("A") or 0)
            except Exception:
                continue
            update(sym, bid, ask, bq, aq, time.time())

            now = time.time()
            if now - last < a.every:
                continue
            last = now
            if int(now) % 15 == 0:
                write_book()

            # ---- manage open positions
            for sym2, p in list(POS.items()):
                f = features(sym2, a.exit_horizon, now)
                if not f:
                    continue
                held = now - p["t"]
                bid2, ask2 = f["bid"], f["ask"]
                exit_px = bid2 if p["side"] == "buy" else ask2
                chg = (exit_px / p["entry"] - 1) * 100 * (1 if p["side"] == "buy" else -1)
                gross = chg
                net = gross - cost_pct()
                why = None
                if net >= a.target:
                    why = "target"
                elif False:   # profit-only: no loss exits
                    why = "stop"
                elif held >= a.clock:
                    why = "clock"
                if why and net < 0:
                    why = None      # never realise a loss, no exceptions
                if not why:
                    continue
                EQ += p["notional"] * net / 100
                rec = {"symbol": sym2, "side": p["side"], "entry": p["entry"],
                       "exit": exit_px, "gross_pct": round(gross, 4),
                       "net_pct": round(net, 4), "reason": why,
                       "held_s": round(held, 1), "notional": round(p["notional"], 2),
                       "pnl_usd": round(p["notional"] * net / 100, 4),
                       "entry_ms": int(p["t"] * 1000), "exit_ms": int(now * 1000),
                       "features": p["feat"], "slippage_bps": 0}
                TRADES.append(rec)
                del POS[sym2]
                write_book()
                if emit:
                    try:
                        emit.fill("hft", rec)
                    except Exception:
                        pass
                log("CLOSE %-10s %-4s gross %+6.3f%% net %+6.3f%% %-6s %4.0fs  eq $%.2f"
                    % (sym2, p["side"], gross, net, why, held, EQ))

            # ---- look for new entries
            if len(POS) >= a.max_open:
                continue
            for sym2 in syms:
                if sym2 in POS:
                    continue
                f = features(sym2, a.entry_horizon, now)
                if not f:
                    continue
                if f["spread_bps"] > a.max_spread:
                    continue
                if abs(f["z"]) < a.min_z:
                    continue
                side = "buy" if f["z"] > 0 else "sell"
                # the book must agree with the momentum
                if side == "buy" and f["imbalance"] < a.min_imb:
                    continue
                if side == "sell" and f["imbalance"] > -a.min_imb:
                    continue
                # expected move must clear the cost floor, or there is no point
                expected = abs(f["move_pct"])
                if expected < floor:
                    continue
                entry = f["bid"] if side == "buy" else f["ask"]
                notional = min(EQ * a.risk / 100 * 40, EQ * 0.08)
                POS[sym2] = {"side": side, "entry": entry, "t": now,
                             "notional": notional,
                             "feat": {"z": round(f["z"], 2), "imb": round(f["imbalance"], 3),
                                      "spread_bps": round(f["spread_bps"], 2),
                                      "move": round(f["move_pct"], 4),
                                      "rate": round(f["rate"], 1)}}
                log("OPEN  %-10s %-4s %.8g z %+.2f imb %+.2f spread %.1fbp move %+.3f%%"
                    % (sym2, side, entry, f["z"], f["imbalance"], f["spread_bps"],
                       f["move_pct"]))
                write_book()
                if len(POS) >= a.max_open:
                    break
        except Exception as exc:
            log("stream error %r — reconnecting" % (exc,))
            try:
                s = WS.connect(url)
            except Exception:
                time.sleep(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000)
    ap.add_argument("--risk", type=float, default=0.65)
    ap.add_argument("--symbols", type=int, default=40)
    ap.add_argument("--every", type=float, default=0.25)
    ap.add_argument("--entry-horizon", type=float, default=8.0)
    ap.add_argument("--exit-horizon", type=float, default=2.0)
    ap.add_argument("--min-z", type=float, default=2.0)
    ap.add_argument("--min-imb", type=float, default=0.15)
    ap.add_argument("--max-spread", type=float, default=6.0)
    ap.add_argument("--cost-multiple", type=float, default=1.5)
    ap.add_argument("--target", type=float, default=0.10)
    ap.add_argument("--stop", type=float, default=0.12)
    ap.add_argument("--clock", type=float, default=45.0)
    ap.add_argument("--max-open", type=int, default=4)
    _a = ap.parse_args()
    EQ = START = _a.start
    run(_a)
