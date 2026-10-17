#!/usr/bin/env python3
"""Local dashboard for the Binance Turtle detector.

Stdlib-only HTTP server (no Flask, no CDN -- the machine's general internet is
blocked, only Binance is reachable).  Reads the detector's append-only ledger
and state file and renders them as an auto-refreshing page.

    python3 -m binance_turtle.dashboard --port 8787
    open http://127.0.0.1:8787
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)

DEFAULT_LEDGER = os.path.join(PROJECT, "ledger", "signals.jsonl")
DEFAULT_STATE = os.path.join(PROJECT, "state", "detector_state.json")


# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------
def read_ledger(path, max_lines=300):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-max_lines:]


def read_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def build_summary(ledger_path, state_path):
    scans = read_ledger(ledger_path)
    state = read_state(state_path)
    now = time.time() * 1000
    latest = scans[-1] if scans else None

    detector = {
        "scans_recorded": len(scans),
        "state": state,
    }
    if latest:
        age_s = max(0.0, (now - latest.get("ts", now)) / 1000.0)
        interval = 900.0
        detector["latest_scan_iso"] = latest.get("iso")
        detector["latest_scan_age_seconds"] = round(age_s, 1)
        detector["alive"] = age_s < (interval * 2.5)

    history = []
    for s in scans[-40:]:
        history.append({
            "iso": s.get("iso"),
            "ts": s.get("ts"),
            "setups": len(s.get("setups", [])),
            "universe_size": s.get("universe_size"),
            "btc_risk_off": (s.get("btc") or {}).get("risk_off"),
            "btc_vol": (s.get("btc") or {}).get("realized_vol_20d"),
        })

    # setup frequency across the recorded scans
    freq = {}
    for s in scans:
        for st in s.get("setups", []):
            key = (st.get("symbol"), st.get("system"))
            freq[key] = freq.get(key, 0) + 1
    leaders = sorted(
        ({"symbol": k[0], "system": k[1], "count": v} for k, v in freq.items()),
        key=lambda r: -r["count"])[:15]

    return {
        "now_iso": datetime.now(timezone.utc).isoformat(),
        "detector": detector,
        "latest": latest,
        "history": history,
        "leaders": leaders,
        "ledger_path": ledger_path,
    }


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Binance Turtle — dashboard</title>
<style>
  :root { --bg:#0e1116; --panel:#161b22; --line:#232a33; --fg:#d7dee8;
          --dim:#7d8899; --ok:#3fb950; --warn:#d29922; --bad:#f85149;
          --accent:#58a6ff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
  header { padding:16px 22px; border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
  h1 { font-size:15px; margin:0; letter-spacing:.10em; text-transform:uppercase; }
  .pill { padding:3px 9px; border-radius:999px; border:1px solid var(--line);
          font-size:11px; letter-spacing:.06em; }
  .pill.ok { color:var(--ok); border-color:#1c3a25; background:#0f2417; }
  .pill.warn { color:var(--warn); border-color:#3a2f14; background:#241d0c; }
  .pill.bad { color:var(--bad); border-color:#3a1c1c; background:#240f0f; }
  main { padding:18px 22px; display:grid; gap:18px;
         grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); }
  section { background:var(--panel); border:1px solid var(--line);
            border-radius:8px; padding:14px 16px; }
  section h2 { font-size:11px; margin:0 0 12px; color:var(--dim);
               text-transform:uppercase; letter-spacing:.12em; font-weight:600; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
           gap:12px; }
  .stat .k { color:var(--dim); font-size:10px; text-transform:uppercase;
             letter-spacing:.1em; }
  .stat .v { font-size:19px; margin-top:3px; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; color:var(--dim); font-weight:500; font-size:10px;
       text-transform:uppercase; letter-spacing:.1em;
       border-bottom:1px solid var(--line); padding:0 6px 7px 0; }
  td { padding:6px 6px 6px 0; border-bottom:1px solid #1b2027; }
  tr:last-child td { border-bottom:none; }
  .sym { color:var(--accent); font-weight:600; }
  .num { text-align:right; }
  .empty { color:var(--dim); padding:10px 0; }
  .bar { height:5px; background:#1f2630; border-radius:3px; overflow:hidden;
         min-width:60px; }
  .bar > i { display:block; height:100%; background:var(--accent); }
  footer { padding:0 22px 22px; color:var(--dim); font-size:11px; }
  a { color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>Binance Turtle</h1>
  <span id="alive" class="pill">…</span>
  <span id="regime" class="pill">…</span>
  <span id="mode" class="pill">paper · no keys · no orders</span>
  <span id="age" class="pill">…</span>
</header>
<main>
  <section>
    <h2>Detector</h2>
    <div class="stats" id="stats"></div>
  </section>
  <section>
    <h2>Latest scan — setups</h2>
    <div id="setups"><div class="empty">loading…</div></div>
  </section>
  <section>
    <h2>Scan history</h2>
    <div id="history"><div class="empty">loading…</div></div>
  </section>
  <section>
    <h2>Most frequent setups</h2>
    <div id="leaders"><div class="empty">loading…</div></div>
  </section>
</main>
<footer id="foot"></footer>
<script>
const esc = s => String(s === null || s === undefined ? "" : s)
  .replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const pct = v => (v === null || v === undefined) ? "n/a"
  : (v * 100).toFixed(2) + "%";
const num = (v, d) => (v === null || v === undefined) ? "—"
  : Number(v).toLocaleString(undefined, {maximumFractionDigits: d === undefined ? 2 : d});

function pill(el, text, cls) { el.textContent = text; el.className = "pill " + (cls || ""); }

async function tick() {
  let d;
  try {
    d = await (await fetch("/api/summary", {cache:"no-store"})).json();
  } catch (e) { pill(document.getElementById("alive"), "server unreachable", "bad"); return; }

  const det = d.detector || {};
  pill(document.getElementById("alive"),
       det.alive ? "detector live" : "detector idle",
       det.alive ? "ok" : "warn");

  const btc = (d.latest && d.latest.btc) || {};
  pill(document.getElementById("regime"),
       btc.risk_off ? "BTC risk-off — no new longs" : "BTC risk-on",
       btc.risk_off ? "bad" : "ok");

  const age = det.latest_scan_age_seconds;
  pill(document.getElementById("age"),
       age === undefined ? "no scans yet"
         : (age < 120 ? Math.round(age) + "s ago" : Math.round(age/60) + "m ago"),
       age === undefined ? "warn" : (age < 2400 ? "ok" : "warn"));

  document.getElementById("stats").innerHTML = [
    ["scans recorded", num(det.scans_recorded, 0)],
    ["universe", num((d.latest||{}).universe_size, 0)],
    ["setups (latest)", num(((d.latest||{}).setups||[]).length, 0)],
    ["BTC 20d vol", pct(btc.realized_vol_20d, 0)],
    ["de-risk", btc.de_risk ? "ON" : "off"],
    ["last scan", esc(det.latest_scan_iso ? det.latest_scan_iso.slice(11,19) + "Z" : "—")],
  ].map(([k,v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  const setups = (d.latest && d.latest.setups) || [];
  const maxScore = Math.max(0.001, ...setups.map(s => s.score || 0));
  document.getElementById("setups").innerHTML = setups.length === 0
    ? '<div class="empty">No qualifying breakout in the latest scan. Every symbol either broke no channel, failed volume confirmation, sat outside the ATR band, or the BTC gate blocked new longs.</div>'
    : `<table><thead><tr><th>symbol</th><th>layer</th><th class="num">score</th>
        <th class="num">price</th><th class="num">ATR%</th><th class="num">vol×</th>
        <th class="num">stop</th><th class="num">qty @10k</th><th class="num">notional</th>
        <th class="num">cluster</th></tr></thead><tbody>` +
      setups.map(s => `<tr>
        <td class="sym">${esc(s.symbol)}</td>
        <td>${esc(s.system)}</td>
        <td class="num"><div style="display:flex;align-items:center;gap:8px;justify-content:flex-end">
            <span>${(s.score||0).toFixed(3)}</span>
            <span class="bar" style="width:56px"><i style="width:${Math.round((s.score||0)/maxScore*100)}%"></i></span>
          </div></td>
        <td class="num">${num(s.price, 8)}</td>
        <td class="num">${pct(s.atr_pct)}</td>
        <td class="num">${s.vol_mult === null ? "—" : Number(s.vol_mult).toFixed(2)}</td>
        <td class="num">${num(s.stop, 8)}</td>
        <td class="num">${num(s.order_qty_hint, 4)}</td>
        <td class="num">${num(s.notional, 0)}</td>
        <td class="num">${s.cluster === null || s.cluster === undefined ? "—" : s.cluster}</td>
      </tr>`).join("") + "</tbody></table>";

  const hist = d.history || [];
  document.getElementById("history").innerHTML = hist.length === 0
    ? '<div class="empty">No scans recorded yet.</div>'
    : `<table><thead><tr><th>time (UTC)</th><th class="num">universe</th>
        <th class="num">setups</th><th>BTC</th><th class="num">20d vol</th></tr></thead><tbody>` +
      hist.slice().reverse().map(h => `<tr>
        <td>${esc((h.iso||"").slice(0,19).replace("T"," "))}</td>
        <td class="num">${num(h.universe_size,0)}</td>
        <td class="num">${num(h.setups,0)}</td>
        <td>${h.btc_risk_off ? '<span style="color:var(--bad)">risk-off</span>' : "risk-on"}</td>
        <td class="num">${pct(h.btc_vol,0)}</td>
      </tr>`).join("") + "</tbody></table>";

  const lead = d.leaders || [];
  document.getElementById("leaders").innerHTML = lead.length === 0
    ? '<div class="empty">No recurring setups yet.</div>'
    : `<table><thead><tr><th>symbol</th><th>layer</th><th class="num">scans seen</th></tr></thead><tbody>` +
      lead.map(l => `<tr><td class="sym">${esc(l.symbol)}</td><td>${esc(l.system)}</td>
        <td class="num">${num(l.count,0)}</td></tr>`).join("") + "</tbody></table>";

  document.getElementById("foot").textContent =
    "ledger: " + d.ledger_path + "   ·   refreshed " + d.now_iso.slice(11,19) + "Z";
}
tick();
setInterval(tick, 20000);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    ledger_path = DEFAULT_LEDGER
    state_path = DEFAULT_STATE
    server_version = "binance-turtle-dashboard/0.1"

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html")
            elif path == "/api/summary":
                self._send(200, json.dumps(
                    build_summary(self.ledger_path, self.state_path)))
            elif path == "/health":
                self._send(200, json.dumps({"status": "ready"}))
            elif path == "/api/ledger":
                self._send(200, json.dumps(read_ledger(self.ledger_path)))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except BrokenPipeError:
            pass
        except Exception as exc:
            try:
                self._send(500, json.dumps({"error": repr(exc)}))
            except Exception:
                pass

    def log_message(self, fmt, *args):
        if os.environ.get("DASHBOARD_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Binance Turtle dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--state", default=DEFAULT_STATE)
    args = ap.parse_args(argv)

    Handler.ledger_path = args.ledger
    Handler.state_path = args.state
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Binance Turtle dashboard: http://%s:%d" % (args.host, args.port),
          flush=True)
    print("  ledger: %s" % args.ledger, flush=True)
    print("  state:  %s" % args.state, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
