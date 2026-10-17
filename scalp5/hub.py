#!/usr/bin/env python3
"""One dashboard, one tab per system. Port 8795.

Each tab embeds that system's own live board, so nothing is duplicated or
re-rendered here: the hub is navigation, not a second source of truth.

    :8794  Race      every engine side by side
    :8790  Swing     alert confidence + 20% trail
    :8793  HFT       trend + RSI + MACD, 4-second cap
    :8787  Pumpdump  the Go backend + React app (turtle lives here)
    :8792  Models    6 models -> 1 aggregate
    :8787/api/turtle/stats  Turtle raw feed (JSON)

    python3 hub.py --port 8795
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TABS = [
    ("race", "Race", "http://127.0.0.1:8794/", "#58a6ff"),
    ("swing", "Swing", "http://127.0.0.1:8790/", "#3fb950"),
    ("hft", "HFT 4s", "http://127.0.0.1:8793/", "#d29922"),
    ("pumpdump", "Pumpdump", "http://127.0.0.1:8787/", "#a371f7"),
    ("models", "Models", "http://127.0.0.1:8792/", "#f778ba"),
]

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Trading console</title><style>
 :root{--bg:#080b10;--line:#1e2733;--fg:#e8eef6;--dim:#8a99ad;--faint:#59677a;
       --mono:ui-monospace,SFMono-Regular,Menlo,monospace}
 *{box-sizing:border-box}
 html,body{height:100%;margin:0}
 body{background:var(--bg);color:var(--fg);display:flex;flex-direction:column;
      font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",var(--mono);overflow:hidden}
 header{display:flex;align-items:center;gap:8px;padding:10px 18px;flex-wrap:wrap;
        background:#0b1119;border-bottom:1px solid var(--line)}
 h1{margin:0 14px 0 0;font-size:12px;letter-spacing:.2em;text-transform:uppercase;
    color:var(--dim);font-weight:600}
 .tab{padding:6px 14px;border-radius:8px;border:1px solid transparent;cursor:pointer;
      font-size:12px;letter-spacing:.06em;color:var(--dim);background:transparent;
      font-family:inherit;display:flex;align-items:center;gap:7px;transition:.12s}
 .tab:hover{color:var(--fg);background:#131c26}
 .tab.active{color:var(--fg);background:#16202b;border-color:#26333f}
 .dot{width:7px;height:7px;border-radius:50%}
 .spacer{flex:1}
 .live{font-size:11px;color:var(--faint);font-family:var(--mono)}
 .frame{flex:1;position:relative;min-height:0}
 iframe{position:absolute;inset:0;width:100%;height:100%;border:0;background:var(--bg)}
 .hidden{display:none}
</style></head><body>

<header>
  <h1>Console</h1>
  <div id="tabs" style="display:flex;gap:4px;flex-wrap:wrap"></div>
  <span class="spacer"></span>
  <span class="live" id="clock">—</span>
</header>

<div class="frame" id="frame"></div>

<script>
const TABS = __TABS__;
const frame = document.getElementById("frame");
const tabsBar = document.getElementById("tabs");
const made = {};

function build(){
  TABS.forEach(([key,label,url,colour])=>{
    const b=document.createElement("button");
    b.className="tab"; b.dataset.key=key;
    b.innerHTML=`<span class="dot" style="background:${colour}"></span>${label}`;
    b.onclick=()=>show(key);
    tabsBar.appendChild(b);

    const f=document.createElement("iframe");
    f.src=url; f.loading="lazy"; f.className="hidden"; f.dataset.key=key;
    frame.appendChild(f);
    made[key]=f;
  });
}

function show(key){
  Object.entries(made).forEach(([k,f])=>f.classList.toggle("hidden",k!==key));
  document.querySelectorAll(".tab").forEach(b=>
    b.classList.toggle("active", b.dataset.key===key));
  history.replaceState(null,"","#"+key);
  localStorage.setItem("console-tab", key);
}

setInterval(()=>{
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString(undefined,{hour12:false});
},1000);

build();
const start = location.hash.slice(1) || localStorage.getItem("console-tab") || "race";
show(made[start] ? start : "race");
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            body = PAGE.replace("__TABS__", json.dumps(TABS)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif p == "/health":
            body = json.dumps({"status": "ready", "tabs": [t[0] for t in TABS]}).encode()
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
    ap.add_argument("--port", type=int, default=8795)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), H)
    print("console: http://%s:%d" % (args.host, args.port), flush=True)
    print("  tabs: %s" % ", ".join(t[0] for t in TABS), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
