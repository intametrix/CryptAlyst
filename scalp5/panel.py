#!/usr/bin/env python3
"""Readable panel for the live trader (port 8790).

Design goals, in order:
  1. Numbers are right-aligned and monospaced so columns line up and can be
     compared by eye.
  2. Every row answers: what am I in, at what price, and is it winning.
  3. Colour is used only for sign (P&L, side), never for decoration.
  4. Dense but not cramped: one screen, no scrolling for the open book.

    python3 panel.py --port 8790
"""

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trader — live book</title>
<style>
  :root{
    --bg:#0a0e13; --card:#121821; --card2:#0f151d; --line:#1f2833;
    --fg:#e6edf5; --dim:#8494a8; --faint:#5a6a7d;
    --up:#3fb950; --down:#f85149; --warn:#d29922; --accent:#58a6ff;
    --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",var(--mono)}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums}

  /* ---------- header ---------- */
  header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
         padding:14px 22px;background:#0d131b;border-bottom:1px solid var(--line)}
  .brand{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--dim)}
  .equity{font-family:var(--mono);font-size:22px;font-variant-numeric:tabular-nums}
  .pill{padding:3px 10px;border-radius:999px;border:1px solid var(--line);
        font-size:11px;color:var(--dim);letter-spacing:.04em}
  .pill.ok{color:var(--up);border-color:#1d3a26;background:#0e2416}
  .pill.bad{color:var(--down);border-color:#3a1d1d;background:#240f0f}
  .pill.warn{color:var(--warn);border-color:#3a2f14;background:#221c0c}
  .spacer{flex:1}
  .clock{font-family:var(--mono);font-size:11px;color:var(--faint)}

  /* ---------- summary strip ---------- */
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:1px;background:var(--line);border-bottom:1px solid var(--line)}
  .kpi{background:var(--card2);padding:12px 22px}
  .kpi .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
  .kpi .v{font-family:var(--mono);font-size:18px;margin-top:3px;
          font-variant-numeric:tabular-nums}

  /* ---------- layout ---------- */
  main{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);
       gap:16px;padding:16px 22px 24px}
  @media(max-width:1050px){main{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        overflow:hidden}
  .card h2{margin:0;padding:10px 14px;font-size:10px;letter-spacing:.14em;
           text-transform:uppercase;color:var(--dim);font-weight:600;
           border-bottom:1px solid var(--line);display:flex;
           justify-content:space-between;align-items:center}
  .card h2 span{color:var(--faint);letter-spacing:.04em}

  /* ---------- tables ---------- */
  table{width:100%;border-collapse:collapse;font-family:var(--mono);
        font-variant-numeric:tabular-nums;font-size:12.5px}
  th{position:sticky;top:0;background:#101720;color:var(--faint);
     font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;
     font-weight:600;text-align:right;padding:8px 12px;border-bottom:1px solid var(--line)}
  th:first-child{text-align:left}
  td{padding:7px 12px;text-align:right;border-bottom:1px solid #161d26}
  td:first-child{text-align:left}
  tbody tr:nth-child(even){background:#0e141c}
  tbody tr:hover{background:#151d27}
  tr:last-child td{border-bottom:none}
  .sym{color:var(--accent);font-weight:600;letter-spacing:.02em}
  .up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--dim)}
  .side{padding:1px 7px;border-radius:4px;font-size:10px;letter-spacing:.08em}
  .side.buy{color:var(--up);background:#0e2416;border:1px solid #1d3a26}
  .side.sell{color:var(--down);background:#240f0f;border:1px solid #3a1d1d}
  .muted{color:var(--faint)}
  .empty{padding:22px 16px;color:var(--faint);text-align:center}
  .scroll{max-height:calc(100vh - 300px);overflow:auto}
  footer{padding:0 22px 20px;color:var(--faint);font-size:11px;font-family:var(--mono)}
  .controls{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
            padding:10px 22px;background:#0d131b;border-bottom:1px solid var(--line)}
  .ctitle{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint)}
  .controls label{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--dim)}
  .controls input{width:74px;background:#0a1017;color:var(--fg);border:1px solid var(--line);
                  border-radius:6px;padding:4px 7px;font-family:var(--mono);font-size:12px}
  .controls input:focus{outline:none;border-color:#2f4256}
  .controls button{background:#16202b;color:var(--fg);border:1px solid #2a3a4a;
                   border-radius:6px;padding:5px 14px;font:inherit;font-size:11px;cursor:pointer}
  .controls button:hover{border-color:#3d5470}
  .cstat{font-size:11px;font-family:var(--mono);color:var(--faint)}
</style></head><body>

<header>
  <span class="brand">Live book</span>
  <span class="equity num" id="eq">—</span>
  <span class="pill" id="ret">—</span>
  <span class="pill ok" id="mode">paper · no keys</span>
  <span class="pill" id="floor">—</span>
  <span class="spacer"></span>
  <span class="clock" id="clock">—</span>
</header>

<div class="kpis">
  <div class="kpi"><div class="k">Open positions</div><div class="v num" id="k-open">—</div></div>
  <div class="kpi"><div class="k">Unrealised</div><div class="v num" id="k-upnl">—</div></div>
  <div class="kpi"><div class="k">Realised</div><div class="v num" id="k-real">—</div></div>
  <div class="kpi"><div class="k">Total P&amp;L</div><div class="v num" id="k-total">—</div></div>
  <div class="kpi"><div class="k">Open risk</div><div class="v num" id="k-risk">—</div></div>
  <div class="kpi"><div class="k">Closed / wins</div><div class="v num" id="k-closed">—</div></div>
</div>

<div class="controls">
  <span class="ctitle">risk controls</span>
  <label>risk / trade %<input id="c-risk" type="number" step="0.05" min="0.05" max="10"></label>
  <label>max open risk %<input id="c-maxrisk" type="number" step="1" min="5" max="100"></label>
  <label>take profit %<input id="c-tp" type="number" step="0.1" min="0.2" max="10"></label>
  <label>stop %<input id="c-sl" type="number" step="0.1" min="0.2" max="5"></label>
  <label>confidence floor %<input id="c-conf" type="number" step="1" min="0" max="100"></label>
  <button id="apply">apply</button>
  <span id="cstat" class="cstat"></span>
</div>

<main>
  <section class="card">
    <h2>Open positions <span id="h-open"></span></h2>
    <div class="scroll"><div id="positions"><div class="empty">loading…</div></div></div>
  </section>

  <section class="card">
    <h2>Closed trades <span id="h-closed"></span></h2>
    <div class="scroll"><div id="trades"><div class="empty">no closed trades yet</div></div></div>
  </section>
</main>

<footer id="foot">—</footer>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const money=(v,d=2)=>v==null?"—":Number(v).toLocaleString(undefined,
   {minimumFractionDigits:d,maximumFractionDigits:d});
const pct=(v,d=2)=>v==null?"—":(v>=0?"+":"")+Number(v).toFixed(d)+"%";
const cls=v=>v>0?"up":v<0?"down":"flat";
const price=v=>{
  if(v==null) return "—";
  const a=Math.abs(v);
  if(a>=1000) return money(v,2);
  if(a>=1) return Number(v).toFixed(4);
  if(a>=0.01) return Number(v).toFixed(6);
  return Number(v).toFixed(8);
};

async function tick(){
  let d;
  try{ d=await (await fetch("/api/state",{cache:"no-store"})).json(); }
  catch(e){ $("clock").textContent="panel offline"; return; }
  if(!d||d.missing){ $("clock").textContent="trader not publishing"; return; }

  const eq=d.equity, ps=(d.positions||[]).slice().sort((a,b)=>(b.gross_pct||0)-(a.gross_pct||0));
  const ts=(d.trades||[]).slice().reverse();
  const upnl=ps.reduce((a,p)=>a+(p.notional||0)*(p.gross_pct||0)/100,0);
  const risk=(d.open_risk_usd||0);
  const realised=eq-(d.start_equity||eq);

  $("eq").textContent="$"+money(eq);
  const r=d.return_pct||0;
  $("ret").textContent=pct(r,3); $("ret").className="pill "+(r>=0?"ok":"bad");
  $("floor").textContent = (d.min_confidence!=null)
    ? "floor "+(100*d.min_confidence).toFixed(0)+"% · "+(d.basis||"")
    : "basis "+(d.basis||"confidence");
  $("clock").textContent=new Date().toLocaleTimeString(undefined,{hour12:false});

  $("k-open").textContent=ps.length+"/"+(d.max_positions||"—");
  $("k-upnl").textContent=(upnl>=0?"+":"")+"$"+money(upnl);
  $("k-upnl").className="v num "+cls(upnl);
  $("k-risk").textContent="$"+money(risk)+"  ("+(eq?money(100*risk/eq,1):"—")+"%)";
  $("k-closed").textContent=(d.trade_count||0)+" / "+(d.wins||0);
  $("k-real").textContent=(realised>=0?"+":"")+"$"+money(realised);
  $("k-real").className="v num "+cls(realised);
  const total=realised+upnl;
  $("k-total").textContent=(total>=0?"+":"")+"$"+money(total);
  $("k-total").className="v num "+cls(total);

  $("h-open").textContent=ps.length+" open";
  $("positions").innerHTML = ps.length ? `
    <table><thead><tr>
      <th>ticker</th><th>side</th><th>entry</th><th>last</th>
      <th>P&amp;L</th><th>peak</th><th>stop / trail</th><th>held</th><th>conf</th>
    </tr></thead><tbody>`+ps.map(p=>`
      <tr>
        <td class="sym">${esc(p.symbol)}</td>
        <td><span class="side ${p.side==='buy'?'buy':'sell'}">${esc(p.side.toUpperCase())}</span></td>
        <td>${price(p.entry)}</td>
        <td>${price(p.price)}</td>
        <td class="${cls(p.gross_pct)}"><b>${pct(p.gross_pct,3)}</b></td>
        <td class="muted">${pct(p.peak_pct,3)}</td>
        <td class="muted">${pct(p.trail_line,2)}</td>
        <td class="muted">${Math.round(p.held_s||0)}s</td>
        <td class="muted">${p.conf==null?"—":(100*p.conf).toFixed(0)+"%"}</td>
      </tr>`).join("")+`</tbody></table>` :
    '<div class="empty">flat — waiting for a signal</div>';

  $("h-closed").textContent=(d.trade_count||0)+" closed";
  $("trades").innerHTML = ts.length ? `
    <table><thead><tr>
      <th>ticker</th><th>side</th><th>net</th><th>reason</th><th>held</th>
    </tr></thead><tbody>`+ts.map(t=>`
      <tr>
        <td class="sym">${esc(t.symbol)}</td>
        <td><span class="side ${t.side==='buy'?'buy':'sell'}">${esc(t.side.toUpperCase())}</span></td>
        <td class="${cls(t.net_pct)}"><b>${pct(t.net_pct,3)}</b></td>
        <td class="muted">${esc(t.reason||"")}</td>
        <td class="muted">${Math.round(t.held_s||0)}s</td>
      </tr>`).join("")+`</tbody></table>` :
    '<div class="empty">no closed trades yet</div>';

  $("foot").textContent =
    `${d.engine||"trader"} · state ${d.state||"live"} · updated `+
    new Date(d.ts).toLocaleTimeString(undefined,{hour12:false})+
    ` · open risk ${eq?money(100*risk/eq,2):"—"}% of equity`;
}
async function loadControls(){
  try{
    const c=await (await fetch("/api/control",{cache:"no-store"})).json();
    $("c-risk").value=c.risk_pct ?? "";
    $("c-maxrisk").value=c.max_open_risk_pct ?? "";
    $("c-tp").value=c.tp_pct ?? "";
    $("c-sl").value=c.sl_pct ?? "";
    $("c-conf").value=c.min_confidence_pct ?? "";
    $("cstat").textContent=c.ts ? "live settings loaded" : "no overrides yet";
  }catch(e){ $("cstat").textContent="controls offline"; }
}
$("apply").onclick=async ()=>{
  const body={
    risk_pct: parseFloat($("c-risk").value),
    max_open_risk_pct: parseFloat($("c-maxrisk").value),
    tp_pct: parseFloat($("c-tp").value),
    sl_pct: parseFloat($("c-sl").value),
    min_confidence_pct: parseFloat($("c-conf").value),
  };
  $("cstat").textContent="applying…";
  try{
    const r=await fetch("/api/control",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    const d=await r.json();
    $("cstat").textContent = r.ok ? "applied at "+new Date().toLocaleTimeString(undefined,{hour12:false})
                                  : ("rejected: "+(d.error||r.status));
  }catch(e){ $("cstat").textContent="apply failed"; }
};
loadControls();
tick(); setInterval(tick,2000);
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    state_path = ""
    control_path = ""

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
            st = load(self.state_path)
            if st is None:
                return self._send(200, json.dumps({"missing": True}), "application/json")
            # merge the live controls so the header shows the floor the engine
            # is actually enforcing, even when the engine does not publish it
            ctl = load(self.control_path) or {}
            if ctl.get("min_confidence_pct") is not None:
                st["min_confidence"] = float(ctl["min_confidence_pct"]) / 100.0
            for k_src, k_dst, scale in (("risk_pct", "risk_pct", 1.0),
                                        ("max_open_risk_pct", "max_open_risk_pct", 1.0),
                                        ("tp_pct", "tp_pct", 1.0),
                                        ("sl_pct", "sl_pct", 1.0)):
                if ctl.get(k_src) is not None:
                    st[k_dst] = float(ctl[k_src]) * scale
            return self._send(200, json.dumps(st), "application/json")
        if p == "/api/control":
            return self._send(200, json.dumps(load(self.control_path) or {}),
                              "application/json")
        if p == "/health":
            return self._send(200, json.dumps({"status": "ready"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def do_POST(self):
        if self.path.split("?")[0] != "/api/control":
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8")
            incoming = json.loads(raw)
        except Exception as exc:
            return self._send(400, json.dumps({"error": "bad json: %s" % exc}), "application/json")
        clean = {}
        for k, lo, hi in (("risk_pct", 0.05, 10.0), ("max_open_risk_pct", 5.0, 100.0),
                          ("tp_pct", 0.2, 10.0), ("sl_pct", 0.2, 5.0),
                          ("min_confidence_pct", 0.0, 100.0)):
            if k in incoming:
                try:
                    v = float(incoming[k])
                except Exception:
                    return self._send(400, json.dumps({"error": "%s must be a number" % k}),
                                      "application/json")
                if not (lo <= v <= hi):
                    return self._send(400, json.dumps(
                        {"error": "%s must be between %s and %s" % (k, lo, hi)}),
                        "application/json")
                clean[k] = v
        clean["ts"] = int(time.time() * 1000)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.control_path)) or ".", exist_ok=True)
            tmp = self.control_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(clean, fh)
            os.replace(tmp, self.control_path)
        except Exception as exc:
            return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        return self._send(200, json.dumps(clean), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--state", default=os.path.join(HERE, "state", "swing_state.json"))
    ap.add_argument("--control", default=os.path.join(HERE, "state", "control.json"))
    args = ap.parse_args()
    H.state_path = args.state
    H.control_path = args.control
    httpd = ThreadingHTTPServer((args.host, args.port), H)
    print("panel: http://%s:%d" % (args.host, args.port), flush=True)
    print("  state: %s" % args.state, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
