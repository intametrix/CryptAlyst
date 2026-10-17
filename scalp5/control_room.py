#!/usr/bin/env python3
"""Control room: both dashboards laid out as panels on one page.

    python3 control_room.py --port 8791   ->  http://127.0.0.1:8791

Panels are iframes onto the two local apps:
    :8787  pumpdump (Go backend + React dashboard, embedded)
    :8790  swing 3% trader panels (stdlib dashboard)

Read-only aggregator. It holds no state and never talks to Binance.
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Control room — pumpdump + swing</title>
<style>
  :root{--bg:#0b0f14;--panel:#141a21;--line:#232c36;--fg:#d8e0ea;--dim:#7b8798;
        --ok:#3fb950;--accent:#58a6ff}
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow:hidden}
  header{height:46px;display:flex;align-items:center;gap:12px;padding:0 16px;
         border-bottom:1px solid var(--line);background:#101720}
  h1{font-size:12px;margin:0;letter-spacing:.16em;text-transform:uppercase}
  .pill{padding:3px 9px;border:1px solid var(--line);border-radius:999px;font-size:11px;
        color:var(--dim)}
  .pill.live{color:var(--ok);border-color:#1c3a25;background:#0f2417}
  a{color:var(--accent);text-decoration:none;font-size:11px}
  .spacer{flex:1}
  button{background:#1a222c;color:var(--fg);border:1px solid var(--line);
         border-radius:6px;padding:4px 10px;font:inherit;font-size:11px;cursor:pointer}
  button:hover{border-color:#39485a}
  main{height:calc(100vh - 46px);display:grid;gap:10px;padding:10px;
       grid-template-columns:1fr 1fr 1fr}
  main.stacked{grid-template-columns:1fr;grid-template-rows:1fr 1fr;overflow:auto}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:9px;
        overflow:hidden;display:flex;flex-direction:column;min-height:0}
  .card h2{margin:0;padding:7px 12px;font-size:10px;color:var(--dim);letter-spacing:.14em;
           text-transform:uppercase;border-bottom:1px solid var(--line);
           display:flex;justify-content:space-between;align-items:center}
  iframe{border:0;flex:1;width:100%;background:#0b0f14;min-height:0}
  @media(max-width:900px){main{grid-template-columns:1fr;grid-template-rows:1fr 1fr 1fr}}
</style></head><body>
<header>
  <h1>Control room</h1>
  <span class="pill live">both local</span>
  <a href="http://127.0.0.1:8787" target="_blank">pumpdump ↗</a>
  <a href="http://127.0.0.1:8790" target="_blank">swing ↗</a>
  <a href="http://127.0.0.1:8792" target="_blank">models ↗</a>
  <span class="spacer"></span>
  <button id="layout">side by side</button>
  <button id="reload">reload panels</button>
</header>
<main id="main">
  <div class="card">
    <h2><span>pumpdump — flow · screen · turtle</span><span>127.0.0.1:8787</span></h2>
    <iframe id="f1" src="http://127.0.0.1:8787/" loading="eager"></iframe>
  </div>
  <div class="card">
    <h2><span>swing 3% — position · equity · log</span><span>127.0.0.1:8790</span></h2>
    <iframe id="f2" src="http://127.0.0.1:8790/" loading="eager"></iframe>
  </div>
  <div class="card">
    <h2><span>models — 6 → 1 aggregate</span><span>127.0.0.1:8792</span></h2>
    <iframe id="f3" src="http://127.0.0.1:8792/" loading="eager"></iframe>
  </div>
</main>
<script>
  const main=document.getElementById("main");
  document.getElementById("layout").onclick=e=>{
    main.classList.toggle("stacked");
    e.target.textContent=main.classList.contains("stacked")?"stacked":"side by side";
  };
  document.getElementById("reload").onclick=()=>{
    document.getElementById("f1").src=document.getElementById("f1").src;
    document.getElementById("f2").src=document.getElementById("f2").src;
    document.getElementById("f3").src=document.getElementById("f3").src;
  };
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.split("?")[0] == "/health":
            body = json.dumps({"status": "ready"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8791)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), H)
    print("Control room: http://%s:%d" % (args.host, args.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
