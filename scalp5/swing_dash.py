#!/usr/bin/env python3
"""Panel dashboard for the 3% swing trader (read-only, stdlib HTTP).

    python3 swing_dash.py --port 8790     ->  http://127.0.0.1:8790

Reads state/swing_state.json (published by swing3.py) and the JSONL ledger.
Never talks to Binance, never places an order.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def tail_jsonl(path, n=80):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows[-n:]


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Swing 3% — panels</title>
<style>
  :root{--bg:#0b0f14;--panel:#141a21;--panel2:#101720;--line:#232c36;--fg:#d8e0ea;
        --dim:#7b8798;--ok:#3fb950;--bad:#f85149;--warn:#d29922;--accent:#58a6ff;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
  header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
         padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel2)}
  h1{font-size:13px;margin:0;letter-spacing:.14em;text-transform:uppercase}
  .pill{padding:3px 9px;border:1px solid var(--line);border-radius:999px;font-size:11px}
  .pill.ok{color:var(--ok);border-color:#1c3a25;background:#0f2417}
  .pill.bad{color:var(--bad);border-color:#3a1c1c;background:#240f0f}
  .pill.warn{color:var(--warn);border-color:#3a2f14;background:#241d0c}
  .grid{display:grid;gap:14px;padding:16px 20px;
        grid-template-columns:repeat(12,1fr)}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px}
  .card h2{margin:0 0 12px;font-size:10px;color:var(--dim);letter-spacing:.14em;
           text-transform:uppercase;font-weight:600}
  .s4{grid-column:span 4}.s6{grid-column:span 6}.s8{grid-column:span 8}.s12{grid-column:span 12}
  @media(max-width:1000px){.s4,.s6,.s8{grid-column:span 12}}
  .row{display:flex;justify-content:space-between;gap:10px;padding:3px 0}
  .k{color:var(--dim)}
  .big{font-size:26px;letter-spacing:-.02em}
  .pos{color:var(--ok)}.neg{color:var(--bad)}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;color:var(--dim);font-size:10px;text-transform:uppercase;
     letter-spacing:.1em;font-weight:500;border-bottom:1px solid var(--line);padding:0 6px 7px 0}
  td{padding:5px 6px 5px 0;border-bottom:1px solid #1a2129}
  tr:last-child td{border-bottom:none}
  .sym{color:var(--accent);font-weight:600}
  .num{text-align:right}
  .bar{height:7px;background:#1d242d;border-radius:4px;overflow:hidden;position:relative;margin:10px 0 4px}
  .bar i{position:absolute;top:0;bottom:0;left:50%;background:var(--accent)}
  .bar .tick{position:absolute;top:-2px;bottom:-2px;width:1px;background:var(--line)}
  .empty{color:var(--dim);padding:8px 0}
  .mono{font-variant-numeric:tabular-nums}
</style></head><body>
<header>
  <h1>Swing 3% · panels</h1>
  <span id="mode" class="pill">paper · no keys · no orders</span>
  <span id="state" class="pill">…</span>
  <span id="tp" class="pill">…</span>
  <span id="age" class="pill">…</span>
</header>
<div class="grid">
  <div class="card s4"><h2>Equity</h2>
    <div class="big mono" id="equity">—</div>
    <div class="row"><span class="k">return</span><span id="ret" class="mono">—</span></div>
    <div class="row"><span class="k">start</span><span id="starteq" class="mono">—</span></div>
  </div>
  <div class="card s4"><h2>Performance</h2>
    <div class="row"><span class="k">trades</span><span id="n" class="mono">0</span></div>
    <div class="row"><span class="k">wins</span><span id="wins" class="mono">0</span></div>
    <div class="row"><span class="k">win rate</span><span id="wr" class="mono">—</span></div>
    <div class="row"><span class="k">avg net / trade</span><span id="avg" class="mono">—</span></div>
  </div>
  <div class="card s4"><h2>Scanner</h2>
    <div class="row"><span class="k">scans</span><span id="scans" class="mono">0</span></div>
    <div class="row"><span class="k">symbols</span><span id="syms" class="mono">0</span></div>
    <div class="row"><span class="k">mode</span><span id="mode2" class="mono">one position</span></div>
    <div class="row"><span class="k">fee</span><span class="mono">0.20% round trip</span></div>
  </div>
  <div class="card s8"><h2>Open position</h2><div id="posbox"><div class="empty">flat — scanning for the next cross</div></div></div>
  <div class="card s4"><h2>Distance to target</h2><div id="dist"><div class="empty">—</div></div></div>
  <div class="card s12"><h2>Trade log</h2><div id="log"><div class="empty">no closed trades yet</div></div></div>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const p=(v,d=2)=>v==null?"—":(v>=0?"+":"")+Number(v).toFixed(d)+"%";
const n=(v,d=2)=>v==null?"—":Number(v).toLocaleString(undefined,{maximumFractionDigits:d});

async function tick(){
  let d;
  try{ d=await (await fetch("/api/state",{cache:"no-store"})).json(); }
  catch(e){ $("state").textContent="offline"; $("state").className="pill bad"; return; }
  if(!d || d.missing){
    $("state").textContent="runner not publishing"; $("state").className="pill warn";
    return;
  }
  $("state").textContent="running"; $("state").className="pill ok";
  $("tp").textContent="TP +"+n(d.tp_pct,1)+"% / SL -"+n(d.sl_pct,1)+"%";
  const age=Math.round((Date.now()-d.ts)/1000);
  $("age").textContent=age<90?age+"s ago":Math.round(age/60)+"m ago";
  $("age").className="pill "+(age<120?"ok":"warn");

  $("equity").textContent=n(d.equity,2);
  const r=d.return_pct||0;
  $("ret").textContent=p(r,3); $("ret").className="mono "+(r>=0?"pos":"neg");
  $("starteq").textContent=n(d.start_equity,2);
  $("n").textContent=d.trade_count||0;
  $("wins").textContent=d.wins||0;
  const wr=d.trade_count?100*(d.wins||0)/d.trade_count:null;
  $("wr").textContent=wr==null?"—":wr.toFixed(0)+"%";
  const ts=d.trades||[];
  const avg=ts.length?ts.reduce((a,t)=>a+(t.net_pct||0),0)/ts.length:null;
  $("avg").textContent=avg==null?"—":p(avg,3);
  $("avg").className="mono "+(avg==null?"":(avg>=0?"pos":"neg"));
  $("scans").textContent=d.scans||0;
  $("syms").textContent=d.symbols||0;

  const plist=d.positions||(d.position?[d.position]:[]);
  if(!plist.length){
    $("posbox").innerHTML='<div class="empty">flat — waiting for an alert that clears the confidence floor</div>';
    $("dist").innerHTML='<div class="empty">—</div>';
  }else{
    $("posbox").innerHTML=`<table><thead><tr>
        <th>ticker</th><th>side</th><th class="num">entry</th><th class="num">now</th>
        <th class="num">PnL</th><th class="num">peak</th><th class="num">trail line</th>
        <th class="num">held</th><th class="num">conf</th></tr></thead><tbody>`+
      plist.map(p=>`<tr>
        <td class="sym">${esc(p.symbol)}</td><td>${esc(p.side.toUpperCase())}</td>
        <td class="num">${n(p.entry,8)}</td><td class="num">${n(p.price,8)}</td>
        <td class="num ${p.gross_pct>=0?'pos':'neg'}">${p(p.gross_pct,3)}</td>
        <td class="num">${p(p.peak_pct,3)}</td>
        <td class="num neg">${p(p.trail_line,3)}</td>
        <td class="num">${n(p.held_s,0)}s</td>
        <td class="num">${p.conf==null?'—':(p.conf*100).toFixed(0)+'%'}</td></tr>`).join("")+
      `</tbody></table>`;
    const pos=plist[0];
    const total=d.tp_pct+d.sl_pct;
    const frac=Math.max(0,Math.min(1,((pos.gross_pct||0)+d.sl_pct)/total));
    const cls=(pos.gross_pct||0)>=0?"pos":"neg";
    $("posbox").innerHTML=`
      <div class="row"><span class="k">symbol</span><span class="sym">${esc(pos.symbol)}</span></div>
      <div class="row"><span class="k">side</span><span>${esc(pos.side.toUpperCase())}</span></div>
      <div class="row"><span class="k">entry</span><span class="mono">${n(pos.entry,8)}</span></div>
      <div class="row"><span class="k">now</span><span class="mono">${n(pos.price,8)}</span></div>
      <div class="row"><span class="k">gross</span><span class="mono ${cls}">${p(pos.gross_pct,3)}</span></div>
      <div class="row"><span class="k">net of fees</span><span class="mono ${cls}">${p(pos.net_pct,3)}</span></div>
      <div class="row"><span class="k">held</span><span class="mono">${n(pos.held_s,0)}s</span></div>`;
    $("dist").innerHTML=`
      <div class="row"><span class="k">to cap (${n(d.tp_pct,0)}%)</span><span class="mono pos">${n(pos.to_cap,2)}%</span></div>
      <div class="bar"><i style="left:${(frac*100).toFixed(1)}%;width:3px"></i>
        <span class="tick" style="left:50%"></span></div>
      <div class="row"><span class="k">stop at</span><span class="mono neg">-${n(d.sl_pct,1)}%</span>
        <span class="k">target</span><span class="mono pos">+${n(d.tp_pct,1)}%</span></div>
      <div class="row"><span class="k">buffer to stop</span><span class="mono">${n(pos.to_sl_pct,2)}%</span></div>`;
  }

  $("log").innerHTML = ts.length? `<table><thead><tr>
      <th>symbol</th><th>side</th><th class="num">net</th><th class="num">gross</th>
      <th>reason</th><th class="num">held</th><th class="num">equity</th></tr></thead><tbody>`+
    ts.slice().reverse().map(t=>`<tr>
      <td class="sym">${esc(t.symbol)}</td><td>${esc(t.side)}</td>
      <td class="num ${t.net_pct>=0?"pos":"neg"}">${p(t.net_pct,2)}</td>
      <td class="num">${p(t.gross_pct,2)}</td>
      <td>${esc(t.reason)}</td><td class="num">${n(t.held_s,0)}s</td>
      <td class="num">${n(t.equity_after,2)}</td></tr>`).join("")+`</tbody></table>`
    : '<div class="empty">no closed trades yet</div>';
}
tick(); setInterval(tick,2000);
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    state_path = ""
    ledger_path = ""

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
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html")
        if path == "/api/state":
            st = load(self.state_path)
            if st is None:
                return self._send(200, json.dumps({"missing": True}),
                                  "application/json")
            st["ledger"] = tail_jsonl(self.ledger_path, 80)
            return self._send(200, json.dumps(st), "application/json")
        if path == "/health":
            return self._send(200, json.dumps({"status": "ready"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--state", default=os.path.join(HERE, "state", "swing_state.json"))
    ap.add_argument("--ledger", default=os.path.join(HERE, "ledger", "swing3.jsonl"))
    args = ap.parse_args()

    H.state_path = args.state
    H.ledger_path = args.ledger
    httpd = ThreadingHTTPServer((args.host, args.port), H)
    print("Swing dashboard: http://%s:%d" % (args.host, args.port), flush=True)
    print("  state:  %s" % args.state, flush=True)
    print("  ledger: %s" % args.ledger, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
