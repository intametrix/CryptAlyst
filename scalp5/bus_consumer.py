#!/usr/bin/env python3
"""bus_consumer - the subscriber service.

One consumer per topic queue plus the legacy queues, all through lib.bus. It
counts what it sees and serves the tally on :8798 so the dashboard can prove the
bus is whole.
"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib import bus

STATS = {}
LOCK = threading.Lock()
LEGACY = []


def record(name, env):
    with LOCK:
        st = STATS.setdefault(name, {"count": 0, "last_ts": 0, "last": None})
        st["count"] += 1
        st["last_ts"] = int(time.time() * 1000)
        st["last"] = env if isinstance(env, dict) else {"data": env}


def consume_topic(topic):
    queue = "q." + topic

    def handler(env):
        record(topic, {"topic": env.topic, "key": env.key, "producer": env.producer,
                       "data": env.data, "ts": env.ts})
    while True:
        try:
            if bus.sub_queue(queue, handler):
                print("consuming %-18s (%s)" % (topic, queue), flush=True)
                while True:
                    time.sleep(30)
            else:
                raise RuntimeError("subscribe failed")
        except Exception as exc:
            print("consumer %s dropped: %r" % (topic, exc), flush=True)
            time.sleep(2)


def consume_queue(queue):
    def handler(env):
        record("legacy:" + queue, {"topic": env.topic, "key": env.key, "data": env.data})
    while True:
        try:
            if bus.sub_queue(queue, handler):
                print("consuming %-18s (queue)" % queue, flush=True)
                while True:
                    time.sleep(30)
            else:
                raise RuntimeError("subscribe failed")
        except Exception as exc:
            print("queue %s dropped: %r" % (queue, exc), flush=True)
            time.sleep(2)


def main(port):
    for topic in bus.topics():
        threading.Thread(target=consume_topic, args=(topic,), daemon=True).start()
        time.sleep(0.05)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/") in ("/matrix", ""):
                with LOCK:
                    payload = {"topics": STATS, "bus": bus.matrix(),
                               "ts": int(time.time() * 1000)}
            else:
                with LOCK:
                    payload = {"topics": STATS, "ts": int(time.time() * 1000)}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    print("BUS CONSUMER — %d topics + %d legacy queues on :%d"
          % (len(bus.topics()), len(LEGACY), port), flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8798)
