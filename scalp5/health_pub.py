#!/usr/bin/env python3
"""health_pub - the system.health producer.

Every service already exposes a health endpoint. This collects them once per
cycle and publishes one message on the bus, so no view has to poll for it.

    python3 health_pub.py --every 5
"""
import argparse, json, os, socket, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from lib import bus
except Exception:
    bus = None

SERVICES = [
    ("backend",     "http://127.0.0.1:8787/health", 6),
    ("market_data", "http://127.0.0.1:8796/health", 6),
    ("forecast",    "http://127.0.0.1:8790/health", 8),
    ("signal_bus",  "http://127.0.0.1:8793/health", 6),
    ("optimiser",   "http://127.0.0.1:8795/health", 6),
    ("master",      "http://127.0.0.1:8799/api/health", 8),
]


def probe(name, url, timeout):
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(4000).decode()
        ms = int((time.time() - t0) * 1000)
        return {"name": name, "up": True, "ms": ms, "ok": True, "detail": body[:200]}
    except Exception as exc:
        ms = int((time.time() - t0) * 1000)
        return {"name": name, "up": False, "ms": ms, "ok": False, "detail": str(exc)[:120]}


def port_open(host, port):
    try:
        s = socket.create_connection((host, port), timeout=0.4)
        s.close()
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=5.0)
    a = ap.parse_args()
    print("HEALTH PUBLISHER — %d services every %.0fs" % (len(SERVICES), a.every), flush=True)

    while True:
        rows = [probe(n, u, t) for n, u, t in SERVICES]
        # the tcp view catches a service whose http handler is wedged
        for r in rows:
            r["port"] = port_open("127.0.0.1", {
                "backend": 8787, "market_data": 8796,
                "forecast": 8790, "signal_bus": 8793, "optimiser": 8795,
                "master": 8799}.get(r["name"], 0) or 1)
        payload = {"services": rows,
                   "up": sum(1 for r in rows if r["up"]),
                   "total": len(rows),
                   "worst_ms": max((r["ms"] for r in rows), default=0),
                   "ts": int(time.time() * 1000)}
        if bus:
            try:
                bus.pub("system.health", "ALL", payload, "master")
            except Exception as exc:
                print("publish failed: %r" % (exc,), flush=True)
        print("%s  %d/%d up  worst %dms  %s" % (
            time.strftime("%H:%M:%S"), payload["up"], payload["total"], payload["worst_ms"],
            " ".join(("+" if r["up"] else "-") + r["name"] for r in rows)), flush=True)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
