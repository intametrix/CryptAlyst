#!/usr/bin/env python3
"""heartbeat - a 5 second pulse of the whole platform.

Writes state/heartbeat.json every BEAT seconds so the dashboard and the bridge
can see, at a glance, which models are alive and what they are doing. Pushes a
one-line message to the bridge only when something actually changed, so a 5s
beat does not drown the conversation.

    python3 heartbeat.py --every 5
"""
import argparse, json, os, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
MASTER = "http://127.0.0.1:8799"
BRIDGE = "http://127.0.0.1:8810"


def jget(url, timeout=6.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "heartbeat/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_bridge(body):
    try:
        req = urllib.request.Request(BRIDGE + "/say",
                                     data=json.dumps({"from": "dsh", "to": "kimi",
                                                      "topic": "models", "body": body}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--bridge-every", type=float, default=120.0)
    args = ap.parse_args()
    last_push = 0.0
    prev = {}
    print("heartbeat every %.0fs" % args.every, flush=True)

    while True:
        t0 = time.time()
        try:
            d = jget(MASTER + "/api/all", timeout=8)
            engines = d.get("engines") or []
        except Exception as exc:
            engines = []
        rows, total_real, open_n = [], 0.0, 0
        for e in engines:
            if e.get("equity") is None:
                continue
            pos = e.get("positions") or []
            total_real += e.get("realised") or 0
            open_n += len(pos)
            rows.append({"k": e["key"], "eq": round(e["equity"], 2),
                         "ret": round(e.get("return_pct") or 0, 3),
                         "real": round(e.get("realised") or 0, 2),
                         "open": len(pos), "closed": e.get("closed") or 0})

        try:
            pairs = json.load(open(os.path.join(STATE, "pairs.json")))
            pair_n = len(pairs)
        except Exception:
            pair_n = 0

        beat = {"ts": int(time.time() * 1000), "beat_s": args.every,
                "books": len(rows), "open": open_n, "realised": round(total_real, 2),
                "pairs": pair_n, "rows": rows}
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "heartbeat.json.t"), "w") as fh:
            json.dump(beat, fh)
        os.replace(os.path.join(STATE, "heartbeat.json.t"),
                   os.path.join(STATE, "heartbeat.json"))

        # bridge only on change, at most every --bridge-every
        sig = (len(rows), open_n, sum(r["closed"] for r in rows))
        if sig != prev and time.time() - last_push >= args.bridge_every:
            changed = [r["k"] for r in rows if r["closed"] != prev.get(r["k"], 0)]
            post_bridge("beat %ds | books %d open %d realised %+.2f | closed changed: %s | pairs %d" %
                        (args.every, len(rows), open_n, total_real,
                         ",".join(changed) or "none", pair_n))
            prev = {r["k"]: r["closed"] for r in rows}
            last_push = time.time()

        time.sleep(max(1.0, args.every - (time.time() - t0)))


if __name__ == "__main__":
    main()
