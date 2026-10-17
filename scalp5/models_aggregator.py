#!/usr/bin/env python3
"""Model aggregator -- one or many models per ticker, one aggregated decision.

The backend already runs six independent models and publishes their signals at
/api/models/signals:

    volatility-expansion  volume-climax   structure-break
    flow-imbalance        mean-reversion  momentum-divergence

Each row is {model, symbol, direction: PUMP|DUMP, score: 0..1, reasons, at_ms}.
This service folds those into ONE decision per ticker:

    signed   = score * (+1 PUMP, -1 DUMP) * model_weight
    net      = sum(signed) / sum(weights of models that spoke)    -> [-1, 1]
    agree    = how many models point the same way
    decision = STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL

Confidence is deliberately gated on AGREEMENT, not just magnitude: one loud
model is a single opinion, and the whole point of having six is to require more
than one of them to be right.

    python3 models_aggregator.py --port 8792     -> http://127.0.0.1:8792
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIGNALS_URL = "http://127.0.0.1:8787/api/models/signals"
ALERTS_URL = "http://127.0.0.1:8787/api/alerts"

# The alert stream IS the pump/dump detector. It is folded into the aggregate as
# another weighted voice, so the six models and the onset signal vote together
# instead of living in two disconnected pipelines.
ALERT_WEIGHT = 1.0

WEIGHTS = {
    "flow-imbalance": 1.0,
    "volume-climax": 1.0,
    "structure-break": 1.0,
    "momentum-divergence": 0.9,
    "volatility-expansion": 0.7,
    "mean-reversion": 0.6,      # contrarian by nature: down-weighted
}


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def fetch_signals(timeout=4.0):
    req = urllib.request.Request(SIGNALS_URL, headers={"User-Agent": "agg/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")).get("signals") or []


def fetch_alerts(timeout=4.0):
    """The pump/dump onset signal, with score and severity per symbol."""
    req = urllib.request.Request(ALERTS_URL, headers={"User-Agent": "agg/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")).get("alerts") or []


class Aggregator(object):
    def __init__(self, window_s=120.0, strong=0.6, weak=0.3, min_agree=2):
        self.window_ms = window_s * 1000.0
        self.strong = strong
        self.weak = weak
        self.min_agree = min_agree
        self.latest = {}          # (model, symbol, direction) -> signal
        self.alerts = {}          # symbol -> {direction, score, at_ms, severity}
        self.ticks = 0
        self.last_fetch_ms = 0

    def ingest(self, signals):
        now = int(time.time() * 1000)
        self.last_fetch_ms = now
        for s in signals:
            key = (s.get("model"), s.get("symbol"), s.get("direction"))
            prev = self.latest.get(key)
            if prev is None or (s.get("at_ms") or 0) >= (prev.get("at_ms") or 0):
                self.latest[key] = s
        # expire
        cutoff = now - self.window_ms
        for k in [k for k, v in self.latest.items() if (v.get("at_ms") or 0) < cutoff]:
            self.latest.pop(k, None)

    def ingest_alerts(self, alerts):
        now = int(time.time() * 1000)
        for a in alerts:
            sym = a.get("symbol")
            kind = a.get("kind")
            if not sym or kind not in ("PUMP", "DUMP"):
                continue
            ts = a.get("ts_ms") or 0
            prev = self.alerts.get(sym)
            if prev is None or ts >= (prev.get("at_ms") or 0):
                self.alerts[sym] = {"direction": kind,
                                    "score": float(a.get("score") or 0.0) / 100.0,
                                    "severity": a.get("severity"),
                                    "at_ms": ts}
        cutoff = now - self.window_ms
        for k in [k for k, v in self.alerts.items() if (v.get("at_ms") or 0) < cutoff]:
            self.alerts.pop(k, None)

    def by_symbol(self):
        per = {}
        for (model, symbol, direction), s in self.latest.items():
            w = WEIGHTS.get(model, 0.5)
            sign = 1.0 if direction == "PUMP" else -1.0
            score = float(s.get("score") or 0.0)
            per.setdefault(symbol, []).append({
                "model": model, "direction": direction, "score": round(score, 4),
                "weight": w, "signed": round(sign * score * w, 4),
                "reasons": s.get("reasons") or [], "at_ms": s.get("at_ms"),
            })

        # fold the alert stream in as one more voice per symbol
        for symbol, a in self.alerts.items():
            sign = 1.0 if a["direction"] == "PUMP" else -1.0
            per.setdefault(symbol, []).append({
                "model": "alert(onset)", "direction": a["direction"],
                "score": round(a["score"], 4), "weight": ALERT_WEIGHT,
                "signed": round(sign * a["score"] * ALERT_WEIGHT, 4),
                "reasons": ["severity %s" % a.get("severity")],
                "at_ms": a.get("at_ms"),
            })

        out = []
        for symbol, rows in per.items():
            wsum = sum(r["weight"] for r in rows)
            net = (sum(r["signed"] for r in rows) / wsum) if wsum else 0.0
            sign = 1 if net > 0 else (-1 if net < 0 else 0)
            agree = sum(1 for r in rows
                        if (1 if r["direction"] == "PUMP" else -1) == sign and sign != 0)
            if abs(net) >= self.strong and agree >= max(self.min_agree, 2):
                decision = "STRONG_BUY" if net > 0 else "STRONG_SELL"
            elif abs(net) >= self.weak and agree >= 1:
                decision = "BUY" if net > 0 else "SELL"
            else:
                decision = "NEUTRAL"
            out.append({
                "symbol": symbol,
                "net": round(net, 4),
                "agreement": agree,
                "models_heard": len(rows),
                "decision": decision,
                "confidence": round(min(1.0, abs(net) * (0.6 + 0.2 * agree)), 3),
                "contributors": sorted(rows, key=lambda r: -abs(r["signed"])),
            })
        out.sort(key=lambda r: (-abs(r["net"]), -r["agreement"]))
        return out

    def snapshot(self):
        syms = self.by_symbol()
        counts = {}
        for r in syms:
            counts[r["decision"]] = counts.get(r["decision"], 0) + 1
        return {
            "ts": int(time.time() * 1000),
            "signals_in_window": len(self.latest),
            "alerts_in_window": len(self.alerts),
            "alert_weight": ALERT_WEIGHT,
            "symbols": len(syms),
            "decisions": counts,
            "weights": WEIGHTS,
            "window_s": self.window_ms / 1000.0,
            "rows": syms,
        }


AGG = Aggregator()


def refresh_once():
    try:
        sig = fetch_signals()
        AGG.ingest(sig)
        try:
            AGG.ingest_alerts(fetch_alerts())
        except Exception:
            pass
        AGG.ticks += 1
        return True
    except Exception as exc:
        log("fetch failed: %r" % (exc,))
        return False


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Model aggregate</title><style>
 :root{--bg:#0b0f14;--panel:#141a21;--line:#232c36;--fg:#d8e0ea;--dim:#7b8798;
       --ok:#3fb950;--bad:#f85149;--warn:#d29922;--accent:#58a6ff}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
 header{display:flex;gap:12px;align-items:center;padding:12px 18px;
        border-bottom:1px solid var(--line);background:#101720;flex-wrap:wrap}
 h1{font-size:12px;margin:0;letter-spacing:.14em;text-transform:uppercase}
 .pill{padding:3px 9px;border:1px solid var(--line);border-radius:999px;
       font-size:11px;color:var(--dim)}
 .pill.ok{color:var(--ok);border-color:#1c3a25;background:#0f2417}
 .wrap{padding:14px 18px}
 table{width:100%;border-collapse:collapse}
 th{text-align:left;color:var(--dim);font-size:10px;text-transform:uppercase;
    letter-spacing:.1em;font-weight:500;padding:0 8px 8px 0;
    border-bottom:1px solid var(--line)}
 td{padding:7px 8px 7px 0;border-bottom:1px solid #1a2129;vertical-align:top}
 .sym{color:var(--accent);font-weight:600;font-size:13px}
 .buy{color:var(--ok)}.sell{color:var(--bad)}.neu{color:var(--dim)}
 .bar{height:6px;background:#1d242d;border-radius:3px;position:relative;
      overflow:hidden;width:90px}
 .bar i{position:absolute;top:0;bottom:0;left:50%}
 .m{display:inline-block;padding:1px 6px;margin:1px 3px 1px 0;border-radius:4px;
    border:1px solid var(--line);font-size:10px;color:var(--dim)}
 .m.up{color:var(--ok);border-color:#1c3a25}
 .m.dn{color:var(--bad);border-color:#3a1c1c}
 .num{text-align:right}
 .empty{color:var(--dim);padding:14px 0}
</style></head><body>
<header>
  <h1>Model aggregate</h1>
  <span class="pill" id="win">window</span>
  <span class="pill" id="cnt">…</span>
  <span class="pill ok">6 models → 1 decision</span>
</header>
<div class="wrap"><div id="body"><div class="empty">loading…</div></div></div>
<script>
const $=i=>document.getElementById(i);
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function tick(){
  let d;
  try{ d=await (await fetch("/api/aggregate",{cache:"no-store"})).json(); }
  catch(e){ $("cnt").textContent="offline"; return; }
  $("win").textContent="window "+d.window_s+"s";
  const c=d.decisions||{};
  $("cnt").textContent=(d.symbols||0)+" symbols · "+(c.STRONG_BUY||0)+" strong buy · "
     +(c.BUY||0)+" buy · "+(c.SELL||0)+" sell · "+(c.STRONG_SELL||0)+" strong sell";
  const rows=d.rows||[];
  if(!rows.length){ $("body").innerHTML='<div class="empty">no model signals in the window yet</div>'; return; }
  $("body").innerHTML=`<table><thead><tr>
     <th>ticker</th><th>decision</th><th class="num">net</th><th class="num">agree</th>
     <th>balance</th><th>contributing models</th></tr></thead><tbody>`+
   rows.slice(0,40).map(r=>{
     const cls=r.decision.includes("BUY")?"buy":r.decision.includes("SELL")?"sell":"neu";
     const w=Math.min(50,Math.abs(r.net)*50);
     const bar=r.net>=0
       ? `<span style="position:absolute;left:50%;width:${w}%;top:0;bottom:0;background:var(--ok)"></span>`
       : `<span style="position:absolute;right:50%;width:${w}%;top:0;bottom:0;background:var(--bad)"></span>`;
     const ms=(r.contributors||[]).map(c=>
       `<span class="m ${c.direction==='PUMP'?'up':'dn'}">${esc(c.model)} ${c.score.toFixed(2)}</span>`).join("");
     return `<tr>
       <td class="sym">${esc(r.symbol)}</td>
       <td class="${cls}">${esc(r.decision)}</td>
       <td class="num ${cls}">${r.net>=0?"+":""}${r.net.toFixed(3)}</td>
       <td class="num">${r.agreement}/${r.models_heard}</td>
       <td><div class="bar">${bar}<span style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--line)"></span></div></td>
       <td>${ms}</td></tr>`;
   }).join("")+`</tbody></table>`;
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
        if p == "/api/aggregate":
            refresh_once()
            return self._send(200, json.dumps(AGG.snapshot()), "application/json")
        if p == "/health":
            return self._send(200, json.dumps({"status": "ready"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8792)
    ap.add_argument("--window", type=float, default=120.0)
    ap.add_argument("--state", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "state", "aggregate.json"))
    args = ap.parse_args()

    AGG.window_ms = args.window * 1000.0
    httpd = ThreadingHTTPServer((args.host, args.port), H)
    print("Model aggregate: http://%s:%d" % (args.host, args.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
