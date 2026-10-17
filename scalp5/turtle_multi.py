#!/usr/bin/env python3
"""Five Turtle timeframes running side by side, each with its own book.

The same Donchian/ATR logic, sampled at 10s, 20s, 45s, 1m and 2m. Each copy is
an independent paper account so their P&L is directly comparable: the only
difference between them is how much time a trade is allowed to develop.

    t10s : 1s bars, 20-bar channel, 10s maximum hold
    t20s : 1s bars, 20-bar channel, 20s maximum hold
    t45s : 1s bars, 45-bar channel, 45s maximum hold
    t1m  : 1m bars, 20-bar channel, 60s maximum hold
    t2m  : 1m bars, 20-bar channel, 120s maximum hold

Each has its own server page on :8798, so one job runs all five engines and
their dashboard.

PAPER ONLY. No keys, no orders.
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from turtle_core import analyse, setup, universe, all_prices, log, atr_wilder, median, ema

HERE = os.path.dirname(os.path.abspath(__file__))

CONFIGS = [
    {"name": "t10s", "interval": "1s", "entry": 20, "exit": 10, "hold": 10.0, "scan": 2.0},
    {"name": "t20s", "interval": "1s", "entry": 20, "exit": 10, "hold": 20.0, "scan": 2.0},
    {"name": "t45s", "interval": "1s", "entry": 45, "exit": 20, "hold": 45.0, "scan": 3.0},
    {"name": "t1m", "interval": "1m", "entry": 20, "exit": 10, "hold": 60.0, "scan": 5.0},
    {"name": "t2m", "interval": "1m", "entry": 20, "exit": 10, "hold": 120.0, "scan": 8.0},
]

STATE = {"ts": 0, "engines": []}
LOCK = threading.Lock()


EDITABLE = ("risk_pct", "max_open_risk_pct", "max_positions", "tp_pct", "sl_pct",
            "trail_ratio", "trail_arm_pct", "lock_trade_pct", "lock_profit_pct",
            "lock_usd", "max_hold", "min_agree", "min_confidence_pct",
            "scan_every", "conviction_cap", "vol_mult", "tolerance", "min_stop_frac")


def settings_snapshot(args):
    return {k: getattr(args, k) for k in EDITABLE if hasattr(args, k)}


def apply_settings(args, c, extra=None):
    """Generic: any key that exists on args is coerced to its type and set."""
    changed = []
    for k, v in c.items():
        if k == "ts":
            continue
        if k == "hold_sec" and extra is not None:
            extra(float(v)); changed.append("hold_sec=" + str(v)); continue
        if not hasattr(args, k):
            continue
        cur = getattr(args, k)
        try:
            setattr(args, k, type(cur)(v))
            changed.append("%s=%s" % (k, getattr(args, k)))
        except Exception:
            pass
    return changed

def run_engine(cfg, syms, args, shared_prices):
    name = cfg["name"]
    equity = args.equity
    start_eq = equity
    positions, trades = {}, []
    FUNNEL = {"scanned": 0, "no_break": 0, "opened": 0}
    opened = scans = 0
    next_scan = 0.0

    _statedir = os.path.join(HERE, "state")
    os.makedirs(_statedir, exist_ok=True)
    _own = os.path.join(_statedir, "control_%s.json" % name)
    _shared = os.path.join(_statedir, "control_turtle.json")
    CTRL = {"path": _own if os.path.exists(_own) else _shared, "mtime": 0.0}

    def apply_control():
        try:
            mt = os.path.getmtime(CTRL["path"])
        except OSError:
            return
        if mt <= CTRL["mtime"]:
            return
        CTRL["mtime"] = mt
        try:
            with open(CTRL["path"]) as fh:
                c = json.load(fh)
        except Exception:
            return
        def _hold(v):
            for cfg in CONFIGS:
                cfg["hold"] = float(v)
        ch = apply_settings(args, c, _hold)
        if ch:
            log("SETTINGS APPLIED -> " + ", ".join(ch))

    while True:
        apply_control()
        try:
            prices = dict(shared_prices["px"])
            # ---- exits -------------------------------------------------
            for s in list(positions):
                p = positions[s]
                px = prices.get(s)
                if not px:
                    continue
                raw = (px / p["entry"] - 1.0) * 100.0
                chg = raw if p["side"] == "buy" else -raw
                p["peak"] = max(p.get("peak", 0.0), chg)
                reason = None
                if chg >= args.tp_pct:
                    reason = "scrape"
                elif p["side"] == "buy" and px <= p["stop"]:
                    reason = "2N_stop"
                elif p["side"] == "sell" and px >= p["stop"]:
                    reason = "2N_stop"
                elif (time.time() - p["opened"]) >= cfg["hold"]:
                    reason = "clock_%ds" % int(cfg["hold"])
                if reason:
                    net = chg - 200 * args.fee
                    pnl = p["notional"] * net / 100.0
                    equity += pnl
                    trades.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                                   "exit": px, "gross_pct": round(chg, 3),
                                   "net_pct": round(net, 3), "reason": reason,
                                   "peak_pct": round(p.get("peak", 0.0), 3),
                                   "held_s": round(time.time() - p["opened"], 1),
                                   "notional": round(p["notional"], 2),
                                   "pnl_usd": round(pnl, 4),
                                   "equity_after": round(equity, 4),
                                   "entry_ms": int(p["opened"] * 1000),
                                   "decision": p.get("decision"),
                                   "exit_ms": int(time.time() * 1000)})
                    positions.pop(s, None)

            # ---- entries ------------------------------------------------
            _force = os.path.join(HERE, "state", "force_%s.json" % name)
            if os.path.exists(_force):
                try:
                    os.remove(_force)
                except Exception:
                    pass
                next_scan = 0.0
            if time.time() >= next_scan:
                scans += 1
                next_scan = time.time() + cfg["scan"]
                free = args.max_positions - len(positions)
                if free > 0:
                    cand = []
                    for s in syms:
                        if s in positions:
                            continue
                        try:
                            x = analyse(s, cfg["interval"], cfg["entry"], cfg["exit"])
                        except Exception:
                            continue
                        st = setup(x, args.vol_mult, args.tolerance)
                        FUNNEL["scanned"] += 1
                        if st:
                            cand.append((st["vol_ratio"], s, st))
                        else:
                            FUNNEL["no_break"] += 1
                    cand.sort(reverse=True)
                    for _, s, st in cand[:free]:
                        px = prices.get(s) or st["entry"]
                        # A 1s ATR can be 0.001% of price; dividing by that
                        # produced 1000x leverage and absurd P&L. Floor the stop
                        # distance and cap notional at 3x equity.
                        stop_frac = abs(st["entry"] - st["stop"]) / st["entry"]
                        stop_frac = max(stop_frac, args.min_stop_frac)
                        if stop_frac <= 0:
                            continue
                        notional = equity * (args.risk_pct / 100.0) / stop_frac
                        notional = min(notional, equity * args.max_leverage)
                        positions[s] = {"side": st["side"], "entry": px,
                                        "opened": time.time(), "stop": st["stop"],
                                        "notional": notional, "peak": 0.0,
                                        "why": "%db bar broke channel, vol x%.2f, 2N stop" % (
                                            cfg["entry"], st.get("vol_ratio") or 0),
                                        "decision": {
                                            "gates": {"channel_bars": cfg["entry"],
                                                      "hold_sec": cfg["hold"],
                                                      "vol_mult": args.vol_mult,
                                                      "tolerance": args.tolerance},
                                            "votes": {},
                                            "features": {"vol_ratio": round(st.get("vol_ratio") or 0, 3),
                                                         "atr_pct": round(x.get("atr_pct") or 0, 4)},
                                            "size_usd": round(notional, 2),
                                            "risk_usd": round(abs(px - st["stop"]) *
                                                              (notional / px), 2)}}
                        opened += 1
                        FUNNEL["opened"] += 1

            plist = []
            for s, p in positions.items():
                px = prices.get(s, p["entry"])
                raw = (px / p["entry"] - 1.0) * 100.0
                chg = raw if p["side"] == "buy" else -raw
                stop_pct = ((p["stop"] / p["entry"] - 1.0) * 100.0) if p["entry"] else 0.0
                plist.append({"symbol": s, "side": p["side"], "entry": p["entry"],
                              "price": px, "gross_pct": round(chg, 3),
                              "net_pct": round(chg - 200 * args.fee, 3),
                              "peak_pct": round(p.get("peak", 0.0), 3),
                              "trail_line": round(stop_pct, 3),
                              "notional": round(p["notional"], 2),
                              "risk_usd": round(abs(p["entry"] - p["stop"]) *
                                                (p["notional"] / p["entry"]), 2),
                              "held_s": round(time.time() - p["opened"], 1),
                              "entry_ms": int(p["opened"] * 1000),
                              "conf": None, "pcal": None,
                              "why": p.get("why"),
                              "decision": p.get("decision")})
            with LOCK:
                for e in STATE["engines"]:
                    if e["name"] == name:
                        snap = settings_snapshot(args)
                        snap["hold_sec"] = cfg["hold"]
                        snap["interval_bars"] = cfg["entry"]
                        e.update({"settings": snap,
                                  "risk_usd": round(sum(
                                      abs(p["entry"] - p["stop"]) * (p["notional"] / p["entry"])
                                      for p in positions.values()), 2),
                                  "equity": round(equity, 2),
                                  "return_pct": round(100 * (equity / start_eq - 1), 3),
                                  "open": plist, "closed": len(trades),
                                  "wins": sum(1 for t in trades if t["net_pct"] > 0),
                                  "opened": opened, "scans": scans,
                                  "recent": trades[-6:],
                                  "trades": trades[-40:],
                                  "realised": round(equity - start_eq, 2),
                                  "unrealised": round(sum(
                                      p["notional"] * ((prices.get(s, p["entry"]) / p["entry"] - 1) * 100 *
                                                       (1 if p["side"] == "buy" else -1)) / 100
                                      for s, p in positions.items()), 2)})
                STATE["ts"] = int(time.time() * 1000)
            time.sleep(0.4)
        except Exception as exc:
            log("%s error: %r" % (name, exc))
            time.sleep(2.0)


def price_loop(shared):
    while True:
        try:
            shared["px"] = all_prices()
        except Exception:
            pass
        time.sleep(1.0)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Turtle timeframes</title><style>
 :root{--bg:#080b10;--card:#111823;--line:#1e2733;--fg:#e8eef6;--dim:#8a99ad;
       --faint:#59677a;--up:#3fb950;--down:#f85149;--mono:ui-monospace,Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.5 -apple-system,BlinkMacSystemFont,var(--mono)}
 header{padding:14px 22px;background:#0b1119;border-bottom:1px solid var(--line);
        display:flex;gap:14px;align-items:center}
 h1{margin:0;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
 .pill{padding:3px 10px;border-radius:999px;border:1px solid var(--line);font-size:11px;color:var(--dim)}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;padding:16px 22px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
 .name{font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;color:#a371f7}
 .ret{font-family:var(--mono);font-size:24px;margin-top:4px;font-variant-numeric:tabular-nums}
 .row{display:flex;justify-content:space-between;font-family:var(--mono);font-size:11.5px;color:var(--dim);padding:2px 0}
 .up{color:var(--up)}.down{color:var(--down)}
 table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px;margin-top:8px}
 th{text-align:right;color:var(--faint);font-size:9.5px;text-transform:uppercase;letter-spacing:.1em;padding-bottom:4px}
 th:first-child{text-align:left} td{text-align:right;padding:3px 0;border-top:1px solid #161d26}
 td:first-child{text-align:left;color:#58a6ff}
 .empty{color:var(--faint);font-size:11px;margin-top:8px}
 footer{padding:0 22px 20px;color:var(--faint);font-size:11px;font-family:var(--mono)}
</style></head><body>
<header><h1>Turtle timeframes</h1>
  <span class="pill" id="live">—</span>
  <span class="pill">10s · 20s · 45s · 1m · 2m</span></header>
<div class="grid" id="grid"></div>
<footer id="foot">—</footer>
<script>
const $=i=>document.getElementById(i);
const pct=v=>(v==null?"—":(v>=0?"+":"")+Number(v).toFixed(3)+"%");
const money=v=>(v==null?"—":Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}));
async function tick(){
  let d; try{ d=await (await fetch("/api/state",{cache:"no-store"})).json(); }catch(e){ return; }
  const es=d.engines||[];
  $("live").textContent=es.length+" engines live";
  $("grid").innerHTML=es.map(e=>`
    <div class="card">
      <div class="name">${e.name} · ${e.interval}</div>
      <div class="ret ${e.return_pct>=0?'up':'down'}">${pct(e.return_pct)}</div>
      <div class="row"><span>equity</span><span>$${money(e.equity)}</span></div>
      <div class="row"><span>realised / unrealised</span><span>${money(e.realised)} / ${money(e.unrealised)}</span></div>
      <div class="row"><span>closed / wins</span><span>${e.closed} / ${e.wins}</span></div>
      ${e.open.length? `<table><thead><tr><th>ticker</th><th>side</th><th>P&L</th><th>held</th></tr></thead>
        <tbody>${e.open.map(p=>`<tr><td>${p.symbol}</td><td>${p.side.toUpperCase()}</td>
          <td class="${p.gross_pct>=0?'up':'down'}">${pct(p.gross_pct)}</td>
          <td>${Math.round(p.held_s)}s</td></tr>`).join("")}</tbody></table>`
        : '<div class="empty">flat — no breakout qualifies</div>'}
    </div>`).join("");
  $("foot").textContent="all five share one paper baseline ($2,000 each) · updated "+
    new Date(d.ts||Date.now()).toLocaleTimeString(undefined,{hour12:false});
}
tick(); setInterval(tick,2000);
</script></body></html>"""


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

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html")
        if p == "/api/state":
            with LOCK:
                return self._send(200, json.dumps(STATE), "application/json")
        if p == "/health":
            return self._send(200, json.dumps({"status": "ready"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=200)
    ap.add_argument("--min-volume", type=float, default=20_000_000.0)
    ap.add_argument("--tp-pct", type=float, default=1.0)
    ap.add_argument("--vol-mult", type=float, default=0.8)
    ap.add_argument("--tolerance", type=float, default=0.004)
    ap.add_argument("--max-positions", type=int, default=4)
    ap.add_argument("--risk-pct", type=float, default=2.0)
    ap.add_argument("--min-stop-frac", type=float, default=0.0015,
                    help="floor on the 2N stop distance (fraction of price)")
    ap.add_argument("--max-leverage", type=float, default=3.0,
                    help="cap notional at this multiple of equity")
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--equity", type=float, default=2000.0)
    ap.add_argument("--port", type=int, default=8798)
    args = ap.parse_args()

    syms = universe(args.universe, args.min_volume)
    log("TURTLE x5 -- PAPER ONLY | universe %d | %d positions each | risk %.1f%%"
        % (len(syms), args.max_positions, args.risk_pct))
    for c in CONFIGS:
        log("  %-5s %-3s bars | %d-bar channel | hold <= %.0fs | scan %.0fs"
            % (c["name"], c["interval"], c["entry"], c["hold"], c["scan"]))

    with LOCK:
        STATE["engines"] = [{"name": c["name"], "interval": c["interval"],
                             "equity": args.equity, "return_pct": 0.0,
                             "open": [], "closed": 0, "wins": 0, "opened": 0,
                             "scans": 0, "recent": [], "realised": 0.0,
                             "unrealised": 0.0, "risk_usd": 0.0,
                             "settings": {}} for c in CONFIGS]

    shared = {"px": {}}
    threading.Thread(target=price_loop, args=(shared,), daemon=True).start()
    for c in CONFIGS:
        threading.Thread(target=run_engine, args=(c, syms, args, shared),
                         daemon=True).start()

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    log("dashboard: http://127.0.0.1:%d" % args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
