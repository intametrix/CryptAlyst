#!/usr/bin/env python3
"""bridge - IPC between the DeepSeek harness agent (DSH) and KIMI.

Two ways in, one shared log:

    http://127.0.0.1:8810/say   POST {"from":"kimi","topic":"models","body":"..."}
    http://127.0.0.1:8810/log   GET  ?since=<id>&to=kimi
    http://127.0.0.1:8810/wait  GET  ?since=<id>&to=kimi&timeout=60   (long poll)

    python3 bridge.py send --from kimi --to dsh --topic models "..."
    python3 bridge.py read --to dsh --since 0
    python3 bridge.py watch --to dsh

Messages are append-only JSON lines in messages.jsonl, so neither side can lose
history and both can replay from any id. Nothing is executed from a message:
it is a talking shop, not a control channel.

    python3 bridge.py serve --port 8810
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "messages.jsonl")
LOCK = threading.Lock()
SIDES = {"dsh", "kimi", "user"}


def append(msg):
    msg.setdefault("ts", int(time.time() * 1000))
    with LOCK:
        n = 0
        try:
            with open(LOG) as fh:
                for _ in fh:
                    n += 1
        except FileNotFoundError:
            pass
        msg["id"] = n + 1
        with open(LOG, "a") as fh:
            fh.write(json.dumps(msg) + "\n")
    return msg


def load(since=0, to=None, topic=None):
    out = []
    try:
        with open(LOG) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("id", 0) <= since:
                    continue
                if to and m.get("to") not in (to, "all", None):
                    continue
                if topic and m.get("topic") != topic:
                    continue
                out.append(m)
    except FileNotFoundError:
        pass
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/log":
            since = int(q.get("since", ["0"])[0])
            return self._send(200, {"messages": load(since, q.get("to", [None])[0],
                                                     q.get("topic", [None])[0])})
        if u.path == "/wait":
            since = int(q.get("since", ["0"])[0])
            to = q.get("to", [None])[0]
            deadline = time.time() + float(q.get("timeout", ["60"])[0])
            while time.time() < deadline:
                ms = load(since, to)
                if ms:
                    return self._send(200, {"messages": ms})
                time.sleep(0.4)
            return self._send(200, {"messages": []})
        if u.path == "/health":
            return self._send(200, {"ok": True, "ts": int(time.time() * 1000),
                                    "log": LOG})
        return self._send(404, {"error": "unknown path"})

    def do_POST(self):
        import urllib.parse
        u = urllib.parse.urlparse(self.path)
        if u.path != "/say":
            return self._send(404, {"error": "unknown path"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        frm = (body.get("from") or "").lower()
        if frm not in SIDES:
            return self._send(400, {"error": "from must be one of " + ",".join(SIDES)})
        text = (body.get("body") or "").strip()
        if not text:
            return self._send(400, {"error": "empty body"})
        msg = append({"from": frm, "to": (body.get("to") or "all").lower(),
                      "topic": body.get("topic") or "general", "body": text})
        return self._send(200, {"ok": True, "id": msg["id"]})


def fmt(m):
    t = time.strftime("%H:%M:%S", time.localtime(m["ts"] / 1000))
    return "[%s #%s %s→%s %s] %s" % (t, m["id"], m["from"], m["to"], m["topic"], m["body"])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8810)
    s2 = sub.add_parser("send")
    s2.add_argument("body")
    s2.add_argument("--from", dest="frm", default="dsh")
    s2.add_argument("--to", default="all")
    s2.add_argument("--topic", default="general")
    s3 = sub.add_parser("read")
    s3.add_argument("--since", type=int, default=0)
    s3.add_argument("--to", default=None)
    s3.add_argument("--topic", default=None)
    s4 = sub.add_parser("watch")
    s4.add_argument("--to", default=None)
    args = ap.parse_args()

    if args.cmd == "serve":
        print("bridge on http://127.0.0.1:%d  (log: %s)" % (args.port, LOG), flush=True)
        ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()
    elif args.cmd == "send":
        m = append({"from": args.frm, "to": args.to, "topic": args.topic, "body": args.body})
        print(json.dumps(m))
    elif args.cmd == "read":
        for m in load(args.since, args.to, args.topic):
            print(fmt(m))
    elif args.cmd == "watch":
        last = 0
        while True:
            for m in load(last, args.to):
                print(fmt(m), flush=True)
                last = m["id"]
            time.sleep(1.0)


if __name__ == "__main__":
    main()
