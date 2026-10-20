#!/usr/bin/env python3
"""audit - prove every part of the running platform answers.

Three layers, because a service can answer an endpoint and still show nothing:
  1. every API route returns 200 with parseable JSON
  2. every dashboard view renders content
  3. every explorer tab renders content

    python3 tools/audit.py            # endpoints + UI
    python3 tools/audit.py --no-ui    # endpoints only
"""
import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MASTER = "http://127.0.0.1:8799"
EXPLORER = "http://127.0.0.1:8802"
FEED = "http://127.0.0.1:8796"
RECORDER = "http://127.0.0.1:8801"

MASTER_ROUTES = ["/api/all", "/api/signals", "/api/models/all", "/api/trading", "/api/alerts",
                 "/api/votes", "/api/metrics", "/api/sparklines", "/api/version", "/api/selfcheck",
                 "/api/intel", "/api/bus", "/api/thinking", "/api/review_state", "/api/risk",
                 "/api/flows", "/api/movers", "/api/forecast", "/api/opportunities", "/api/books",
                 "/api/pending", "/api/tape", "/api/heartbeat", "/api/tick", "/api/tickers",
                 "/api/sentinel", "/api/optimizer", "/api/health"]
EXPLORER_ROUTES = ["/api/envelopes?limit=2", "/api/topics", "/api/stats", "/api/books",
                   "/api/book?engine=grid", "/api/search?limit=2", "/api/why?limit=5",
                   "/api/gantt?mode=positions&limit=5", "/api/flow", "/api/timeline",
                   "/api/health", "/api/operators"]
VIEWS = ["Dashboard", "Thinking", "Message bus", "Tape", "Signals", "Advisor", "Forecast",
         "Movers", "Market live", "Chart", "Leaderboard", "Models", "Optimiser", "Why",
         "Review", "TA Analysis", "Books", "Health"]
TABS = ["list", "journey", "execution", "why", "gantt", "books", "health"]


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "audit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def check_endpoints():
    fails = []
    for base, routes, label in ((MASTER, MASTER_ROUTES, "dashboard"),
                                (EXPLORER, EXPLORER_ROUTES, "explorer"),
                                (FEED, ["/health", "/streamprices", "/klines?symbol=BTCUSDT&interval=1m&limit=3", "/depth?symbol=BTCUSDT", "/flows"], "feed"),
                                (RECORDER, ["/stats"], "recorder")):
        for route in routes:
            try:
                code, body = get(base + route)
                json.loads(body)
                ok = code == 200
            except Exception as exc:
                ok, code = False, str(exc)[:40]
            print("  %-9s %-46s %s" % (label, route, "ok" if ok else "FAIL " + str(code)))
            if not ok:
                fails.append(label + route)
    return fails


# ---- a minimal CDP client, enough to drive the two single-page apps
class CDP:
    def __init__(self, port=9222):
        pages = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port))
        page = [p for p in pages if p.get("type") == "page"][0]
        ws = page["webSocketDebuggerUrl"]              # ws://127.0.0.1:9222/devtools/page/<id>
        hostport = ws.split("//", 1)[1].split("/", 1)[0]
        host, port_s = hostport.split(":")
        self.sock = socket.create_connection((host, int(port_s)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        path = ws.split(hostport, 1)[1]
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n" % (path, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        self.id = 0

    def call(self, method, params=None):
        self.id += 1
        data = json.dumps({"id": self.id, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        n = len(data)
        frame = bytearray([0x81])
        if n < 126:
            frame.append(0x80 | n)
        elif n < 65536:
            frame.append(0x80 | 126)
            frame += struct.pack(">H", n)
        else:
            frame.append(0x80 | 127)
            frame += struct.pack(">Q", n)
        frame += mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(frame))
        while True:
            head = self.sock.recv(2)
            if not head:
                return {}
            ln = head[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self.sock.recv(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self.sock.recv(8))[0]
            body = b""
            while len(body) < ln:
                body += self.sock.recv(ln - len(body))
            msg = json.loads(body)
            if msg.get("id") == self.id:
                return msg.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")


def check_ui():
    fails = []
    c = CDP()
    c.call("Runtime.enable")
    c.call("Page.enable")
    c.call("Emulation.setDeviceMetricsOverride", {"width": 1680, "height": 1000, "deviceScaleFactor": 1, "mobile": False})

    c.js("location.href='%s/'" % MASTER)
    time.sleep(11)
    for name in VIEWS:
        c.js("""(function(){var e=Array.from(document.querySelectorAll('a,button,div'))
            .filter(x=>x.offsetParent && x.textContent.replace(/[^A-Za-z ]/g,'').trim().toLowerCase()==='%s');
            if(e.length) e[0].click();})()""" % name.lower())
        time.sleep(2.2)
        txt = c.js("document.body.innerText") or ""
        ok = len(txt) > 40 and "traceback" not in txt.lower()
        print("  dashboard %-14s %s (%d chars)" % (name, "ok" if ok else "FAIL", len(txt)))
        if not ok:
            fails.append("view:" + name)

    c.js("location.href='%s/'" % EXPLORER)
    time.sleep(11)
    for tab in TABS:
        c.js("document.querySelector('.tab[data-view=\"%s\"]').click()" % tab)
        time.sleep(2.0)
        txt = c.js("document.body.innerText") or ""
        ok = len(txt) > 60
        print("  explorer  %-14s %s (%d chars)" % (tab, "ok" if ok else "FAIL", len(txt)))
        if not ok:
            fails.append("tab:" + tab)
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ui", action="store_true")
    a = ap.parse_args()
    print("endpoints")
    fails = check_endpoints()
    if not a.no_ui:
        print("interfaces")
        try:
            fails += check_ui()
        except Exception as exc:
            print("  UI check unavailable:", exc)
            fails.append("ui-unavailable")
    print()
    if fails:
        print("FAILURES:", len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("every endpoint and every view answered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
