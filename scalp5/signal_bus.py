#!/usr/bin/env python3
"""signal_bus - deliver alerts to the models the moment they are computed.

The Go backend writes alerts to its log; fetching /api/alerts costs a file read
plus JSON of the whole buffer. This service keeps a tight loop on that endpoint
(at 250ms), dedupes by event_id and serves only the new events, so a model can
ask "what happened since cursor N" and get an answer in microseconds.

    GET /latest?since=N     -> {"cursor":M,"signals":[...]}
    GET /health
"""
import argparse, json, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND = "http://127.0.0.1:8787"
PORT = 8793
SIGNALS = []          # ring buffer, oldest first
SEEN = set()
CURSOR = 0
START = time.time()


def jget(u, t=4.0):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "bus/1"}), timeout=t) as r:
        return json.loads(r.read().decode())


def poll(limit=60):
    global CURSOR
    while True:
        try:
            d = jget(BACKEND + "/api/alerts?limit=%d" % limit)
            for a in reversed(d.get("alerts") or []):
                eid = a.get("event_id") or (a.get("symbol"), a.get("ts_ms"), a.get("kind"))
                if eid in SEEN:
                    continue
                SEEN.add(eid)
                CURSOR += 1
                a["cursor"] = CURSOR
                a["received_ms"] = int(time.time() * 1000)
                SIGNALS.append(a)
            if len(SIGNALS) > 800:
                del SIGNALS[:-800]
            if len(SEEN) > 4000:
                SEEN.clear()
        except Exception:
            pass
        time.sleep(0.25)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/latest":
            try:
                since = int(q.get("since", ["0"])[0])
            except Exception:
                since = 0
            out = [s for s in SIGNALS if s.get("cursor", 0) > since]
            return self._send(200, {"cursor": CURSOR, "signals": out[-50:],
                                    "buffered": len(SIGNALS)})
        if u.path == "/health":
            return self._send(200, {"ok": True, "service": "signal_bus",
                                    "buffered": len(SIGNALS), "cursor": CURSOR,
                                    "uptime_s": round(time.time() - START)})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    import threading
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    threading.Thread(target=poll, daemon=True).start()
    print("signal bus on :%d (polling backend every 250ms)" % a.port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
