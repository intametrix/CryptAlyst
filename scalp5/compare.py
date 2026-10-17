#!/usr/bin/env python3
"""Visual comparison board for every live engine. Port 8794.

All engines share one paper baseline, so the return column is directly
comparable. A background sampler records every engine's equity every two
seconds, which is what makes the race chart possible.

    turtle :8787/api/turtle/stats   Donchian + ATR, alert-confirmed
    swing  :8790/api/state          alert confidence, 20% trail
    hft    :8793/api/state          trend + RSI + MACD, 4-second cap

    python3 compare.py --port 8794
"""

import argparse
import json
import sys
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FEEDS = [
    ("turtle", "Donchian · ATR · alert", "#58a6ff", "http://127.0.0.1:8787/api/turtle/stats", "stats"),
    ("swing", "confidence · 20% trail", "#3fb950", "http://127.0.0.1:8790/api/state", "state"),
    ("hft", "RSI · MACD · 4s cap", "#d29922", "http://127.0.0.1:8793/api/state", "state"),
    ("swingturtle", "Donchian · 2N ATR · 15m", "#a371f7", "http://127.0.0.1:8796/api/state", "state"),
    ("multimodels", "5 adaptive TA models", "#f778ba", "http://127.0.0.1:8797/api/state", "state"),
]

HISTORY = {k: deque(maxlen=900) for k, _, _, _, _ in FEEDS}
LOCK = threading.Lock()
STARTED = time.time()
BASE = 2000.0


def jget(url, timeout=4.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cmp/1.0"}),
                                timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def collect():
    rows = []
    for key, kind, colour, url, shape in FEEDS:
        row = {"key": key, "kind": kind, "colour": colour, "alive": False,
               "equity": None, "return_pct": None, "trades": None,
               "wins": None, "win_rate": None, "open": None,
               "risk_pct": None, "realised": None, "unrealised": None,
               "total": None, "note": ""}
        try:
            d = jget(url)
            row["alive"] = True
            if key == "turtle":
                # P&L lives on /api/turtle, counters on /api/turtle/stats
                try:
                    live = jget("http://127.0.0.1:8787/api/turtle")
                    row["realised"] = live.get("realized_pnl")
                    row["unrealised"] = live.get("unrealized_pnl")
                    row["total"] = live.get("total_pnl")
                    row["equity"] = live.get("equity") or d.get("equity")
                    npos = live.get("positions")
                    row["open"] = len(npos) if isinstance(npos, (list, dict)) else 0
                    row["return_pct"] = live.get("return_pct")
                except Exception:
                    pass
            if shape == "stats":
                eq = d.get("equity")
                row.update({"equity": row["equity"] or eq,
                            "return_pct": row["return_pct"] if row["return_pct"] is not None
                                          else d.get("return_pct"),
                            "trades": d.get("closed"),
                            "win_rate": d.get("win_rate_pct"),
                            "open": d.get("positions") if isinstance(d.get("positions"), int) else 0,
                            "note": d.get("state", "")})
            else:
                eq = d.get("equity")
                n = d.get("trade_count") or 0
                unrl = sum((pp.get("notional") or 0) * (pp.get("gross_pct") or 0) / 100.0
                           for pp in (d.get("positions") or []))
                row.update({"equity": eq, "return_pct": d.get("return_pct"),
                            "realised": (eq - (d.get("start_equity") or eq)) if eq else None,
                            "unrealised": round(unrl, 4),
                            "total": round((eq - (d.get("start_equity") or eq)) + unrl, 4) if eq else None,
                            "trades": n, "wins": d.get("wins"),
                            "win_rate": (100.0 * d["wins"] / n) if n else None,
                            "open": len(d.get("positions") or []),
                            "risk_pct": (100.0 * (d.get("open_risk_usd") or 0) / eq) if eq else None,
                            "note": d.get("engine", "")})
            if row["return_pct"] is None and eq:
                row["return_pct"] = 100.0 * (eq / BASE - 1.0)
        except Exception:
            row["note"] = "offline"
        rows.append(row)
    return rows


def sampler():
    while True:
        try:
            rows = collect()
            ts = time.time()
            with LOCK:
                for r in rows:
                    if r["alive"]:
                        HISTORY[r["key"]].append([round(ts, 1), round(r["return_pct"] or 0.0, 4)])
        except Exception:
            pass
        time.sleep(2)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Engine race</title><style>
 :root{--bg:#080b10;--card:#111823;--card2:#0d141d;--line:#1e2733;--fg:#e8eef6;
       --dim:#8a99ad;--faint:#59677a;--up:#3fb950;--down:#f85149;--accent:#58a6ff;
       --mono:ui-monospace,SFMono-Regular,Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",var(--mono)}
 .num{font-family:var(--mono);font-variant-numeric:tabular-nums}

 header{padding:16px 26px;background:#0b1119;border-bottom:1px solid var(--line);
        display:flex;align-items:center;gap:16px;flex-wrap:wrap}
 h1{margin:0;font-size:13px;letter-spacing:.22em;text-transform:uppercase;color:var(--dim);font-weight:600}
 .pill{padding:4px 11px;border-radius:999px;border:1px solid var(--line);font-size:11px;color:var(--dim)}
 .pill.ok{color:var(--up);border-color:#1d3a26;background:#0d2416}
 .pill.bad{color:var(--down);border-color:#3a1d1d;background:#240f0f}
 .spacer{flex:1}
 .clock{font-family:var(--mono);font-size:11.5px;color:var(--faint)}

 /* ---- hero cards ---- */
 .hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
       gap:14px;padding:18px 26px 4px}
 .card{background:linear-gradient(160deg,#121a25,#0d141d);
       border:1px solid var(--line);border-radius:14px;padding:16px 18px;position:relative;
       overflow:hidden}
 .card .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
 .card .name{font-size:12px;letter-spacing:.16em;text-transform:uppercase;font-weight:700}
 .card .ret{font-family:var(--mono);font-size:26px;font-variant-numeric:tabular-nums;
            letter-spacing:-.02em;margin-top:6px}
 .card .eq{font-family:var(--mono);font-size:12.5px;color:var(--dim);margin-top:2px}
 .card .meta{display:flex;gap:16px;margin-top:12px;font-size:11px;color:var(--faint)}
 .card .meta b{display:block;color:var(--fg);font-size:13px;font-family:var(--mono);font-weight:600}
 .card .stripe{position:absolute;left:0;top:0;bottom:0;width:4px}
 .card.lead{box-shadow:0 0 0 1px #2b3d52, 0 12px 32px -18px #58a6ff}
 .crown{position:absolute;right:14px;top:12px;font-size:11px;letter-spacing:.14em;
        color:#d29922;text-transform:uppercase}

 /* ---- chart ---- */
 .chartwrap{margin:14px 26px;background:var(--card);border:1px solid var(--line);
            border-radius:14px;padding:14px 16px 6px}
 .chartwrap h2{margin:0 0 6px;font-size:10px;letter-spacing:.16em;text-transform:uppercase;
               color:var(--faint);font-weight:600}
 svg{display:block;width:100%;height:240px}
 .legend{display:flex;gap:18px;padding:6px 2px 10px;font-size:11.5px;color:var(--dim)}
 .legend span{display:flex;align-items:center;gap:7px}
 .dot{width:9px;height:9px;border-radius:2px;display:inline-block}

 /* ---- table ---- */
 .tablewrap{margin:0 26px 26px;background:var(--card);border:1px solid var(--line);
            border-radius:14px;overflow:hidden}
 table{width:100%;border-collapse:collapse;font-family:var(--mono);
       font-variant-numeric:tabular-nums;font-size:13px}
 th{text-align:right;font-size:10px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--faint);padding:11px 16px;border-bottom:1px solid var(--line);font-weight:600}
 th:first-child,th:nth-child(2){text-align:left}
 td{padding:11px 16px;text-align:right;border-bottom:1px solid #151d27}
 td:first-child,td:nth-child(2){text-align:left}
 tbody tr:nth-child(even){background:#0e151e}
 tbody tr:hover{background:#141e29}
 .engine{font-weight:700;letter-spacing:.06em}
 .kind{color:var(--faint);font-size:11.5px}
 .up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--dim)}
 .track{height:7px;background:#1a222c;border-radius:4px;overflow:hidden;min-width:110px}
 .track i{display:block;height:100%;border-radius:4px}
 footer{padding:0 26px 24px;color:var(--faint);font-size:11px;font-family:var(--mono)}
</style></head><body>

<header>
  <h1>Engine race</h1>
  <span class="pill" id="base">baseline $2,000 each</span>
  <span class="pill ok" id="alive">—</span>
  <span class="pill" id="elapsed">—</span>
  <span class="spacer"></span>
  <span class="clock" id="clock">—</span>
</header>

<div class="hero" id="hero">—</div>

<div class="chartwrap">
  <h2>Return % since the common start</h2>
  <svg id="chart" viewBox="0 0 1000 240" preserveAspectRatio="none"></svg>
  <div class="legend" id="legend"></div>
</div>

<div class="tablewrap">
  <table>
    <thead><tr><th>engine</th><th>strategy</th><th>equity</th><th>return</th>
      <th>realised</th><th>unrealised</th><th>total</th>
      <th>closed</th><th>win rate</th><th>open</th><th>risk</th><th></th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<footer id="foot">—</footer>

<script>
const $=i=>document.getElementById(i);
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const money=(v,d=2)=>v==null?"—":Number(v).toLocaleString(undefined,
   {minimumFractionDigits:d,maximumFractionDigits:d});
const pct=(v,d=3)=>v==null?"—":(v>=0?"+":"")+Number(v).toFixed(d)+"%";
const cls=v=>(v==null)?"flat":(v>0?"up":v<0?"down":"flat");

function linePath(points, ts0, ts1, yMin, yMax){
  const W=1000,H=240,P=6;
  const x=t=>P+(W-2*P)*((t-ts0)/Math.max(1e-9,ts1-ts0));
  const y=v=>H-P-(H-2*P)*((v-yMin)/Math.max(1e-9,yMax-yMin));
  if(!points.length) return "";
  return points.map((p,i)=>(i?"L":"M")+x(p[0]).toFixed(1)+","+y(p[1]).toFixed(1)).join(" ");
}

async function tick(){
  let d;
  try{ d=await (await fetch("/api/compare",{cache:"no-store"})).json(); }
  catch(e){ $("alive").textContent="board offline"; $("alive").className="pill bad"; return; }
  const rows=d.rows||[], hist=d.history||{}, elapsed=d.elapsed_s||0;
  const live=rows.filter(r=>r.alive).length;
  $("alive").textContent=live+"/"+rows.length+" live";
  $("alive").className="pill "+(live===rows.length?"ok":"bad");
  $("elapsed").textContent="t+"+Math.round(elapsed)+"s";
  $("clock").textContent=new Date().toLocaleTimeString(undefined,{hour12:false});

  const ranked=rows.slice().sort((a,b)=>(b.return_pct??-1e9)-(a.return_pct??-1e9));
  const leader=ranked.length?ranked[0].key:null;

  // ---- hero cards ----
  $("hero").innerHTML = rows.map(r=>{
    const c=r.return_pct;
    const bar=c==null?0:Math.min(100,Math.abs(c)*22);
    return `<div class="card ${r.key===leader?'lead':''}">
      <span class="stripe" style="background:${r.colour}"></span>
      ${r.key===leader?'<span class="crown">leader</span>':''}
      <div class="top"><span class="name" style="color:${r.colour}">${esc(r.key)}</span>
        <span class="pill ${r.alive?'ok':'bad'}">${r.alive?'live':'offline'}</span></div>
      <div class="ret ${cls(c)}">${pct(c,3)}</div>
      <div class="eq">$${money(r.equity)}</div>
      <div class="meta">
        <div>realised<b class="${cls(r.realised)}">${r.realised==null?'—':(r.realised>=0?'+':'')+'$'+money(r.realised)}</b></div>
        <div>unrealised<b class="${cls(r.unrealised)}">${r.unrealised==null?'—':(r.unrealised>=0?'+':'')+'$'+money(r.unrealised)}</b></div>
      </div>
      <div class="meta">
        <div>closed<b>${r.trades==null?'—':r.trades}</b></div>
        <div>win<b>${r.win_rate==null?'—':r.win_rate.toFixed(0)+'%'}</b></div>
        <div>open<b>${r.open==null?'—':r.open}</b></div>
      </div>
      <div class="track" style="margin-top:10px"><i style="width:${bar}%;background:${r.colour}"></i></div>
    </div>`;
  }).join("");

  // ---- chart ----
  const series=Object.entries(hist);
  let all=[];
  series.forEach(([k,pts])=>{ pts.forEach(p=>all.push(p)); });
  let ts0=Math.min(...all.map(p=>p[0])), ts1=Math.max(...all.map(p=>p[0]));
  let vals=all.map(p=>p[1]);
  let yMin=Math.min(...vals,0), yMax=Math.max(...vals,0);
  const pad=Math.max(0.01,(yMax-yMin)*0.15);
  yMin-=pad; yMax+=pad;
  const W=1000,H=240,P=6;
  const y=v=>H-P-(H-2*P)*((v-yMin)/Math.max(1e-9,yMax-yMin));
  let svg="";
  // zero line
  svg+=`<line x1="0" y1="${y(0).toFixed(1)}" x2="${W}" y2="${y(0).toFixed(1)}"
          stroke="#2a3644" stroke-width="1" stroke-dasharray="4 4"/>`;
  // grid labels
  [yMax-pad, 0, yMin+pad].forEach(v=>{
    svg+=`<text x="4" y="${(y(v)-4).toFixed(1)}" fill="#4c5a6c" font-size="10"
            font-family="ui-monospace,monospace">${v.toFixed(2)}%</text>`;
  });
  series.forEach(([k,pts])=>{
    const meta=rows.find(r=>r.key===k);
    if(!meta||!pts.length) return;
    svg+=`<path d="${linePath(pts,ts0,ts1,yMin,yMax)}" fill="none"
            stroke="${meta.colour}" stroke-width="2.2" stroke-linejoin="round"
            stroke-linecap="round"/>`;
    const last=pts[pts.length-1];
    const x=P+(W-2*P)*((last[0]-ts0)/Math.max(1e-9,ts1-ts0));
    svg+=`<circle cx="${x.toFixed(1)}" cy="${y(last[1]).toFixed(1)}" r="4" fill="${meta.colour}"/>`;
  });
  $("chart").innerHTML=svg;
  $("legend").innerHTML=rows.map(r=>{
    const pts=hist[r.key]||[];
    const last=pts.length?pts[pts.length-1][1]:null;
    return `<span><i class="dot" style="background:${r.colour}"></i>
      ${esc(r.key)} <b class="${cls(last)}">${pct(last,3)}</b></span>`;
  }).join("");

  // ---- table ----
  const best=Math.max(...rows.map(r=>Math.abs(r.return_pct||0)),0.01);
  $("tbody").innerHTML=rows.map(r=>{
    const w=Math.round(Math.abs(r.return_pct||0)/best*100);
    return `<tr>
      <td class="engine" style="color:${r.colour}">${esc(r.key)}</td>
      <td class="kind">${esc(r.kind)}</td>
      <td>$${money(r.equity)}</td>
      <td class="${cls(r.return_pct)}"><b>${pct(r.return_pct,3)}</b></td>
      <td class="${cls(r.realised)}">${r.realised==null?'—':(r.realised>=0?'+':'')+'$'+money(r.realised)}</td>
      <td class="${cls(r.unrealised)}">${r.unrealised==null?'—':(r.unrealised>=0?'+':'')+'$'+money(r.unrealised)}</td>
      <td class="${cls(r.total)}"><b>${r.total==null?'—':(r.total>=0?'+':'')+'$'+money(r.total)}</b></td>
      <td>${r.trades==null?'—':r.trades}</td>
      <td>${r.win_rate==null?'—':Number(r.win_rate).toFixed(0)+'%'}</td>
      <td>${r.open==null?'—':r.open}</td>
      <td>${r.risk_pct==null?'—':Number(r.risk_pct).toFixed(1)+'%'}</td>
      <td><span class="track"><i style="width:${w}%;background:${r.colour}"></i></span></td>
    </tr>`;
  }).join("");

  $("foot").textContent = ranked.length
    ? `leader ${ranked[0].key} ${pct(ranked[0].return_pct,3)} · same paper baseline, no keys, no orders · sampled every 2s`
    : "waiting for engines";
}
tick(); setInterval(tick,2000);
</script></body></html>
"""


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
        if p == "/api/compare":
            rows = collect()
            with LOCK:
                hist = {k: list(v) for k, v in HISTORY.items()}
            return self._send(200, json.dumps({
                "rows": rows, "history": hist,
                "elapsed_s": int(time.time() - STARTED)}), "application/json")
        if p == "/health":
            return self._send(200, json.dumps({"status": "ready"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8794)
    args = ap.parse_args()
    threading.Thread(target=sampler, daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), H)
    print("visual comparison: http://%s:%d" % (args.host, args.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
