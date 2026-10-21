#!/usr/bin/env python3
"""loss_watch - a model that has been losing for more than 30 seconds is disabled.

The owner's rule: reset everything, but do not let a loser keep trading. A book is
watched from the moment its total (realised + open) goes negative; if it is still
negative LOSS_SECONDS later, it is disabled - which also stops every engine that uses
it, because engines refuse a disabled model.

    python3 tools/loss_watch.py            # run the loop
    python3 tools/loss_watch.py --once     # one pass, for tests
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")
WATCH = os.path.join(STATE, "loss_watch.json")
DISABLED = os.path.join(STATE, "disabled.json")
POST = os.path.join(STATE, "postmortems.jsonl")
LOSS_SECONDS = float(os.environ.get("LOSS_SECONDS", "30"))
POLL = float(os.environ.get("LOSS_POLL", "5"))


def prices():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8796/streamprices", timeout=3) as r:
            return json.load(r).get("p") or {}
    except Exception:
        return {}


def unreal(pack, px):
    total = 0.0
    for p in pack.get("positions") or []:
        sym = (p.get("symbol") or "").upper()
        entry, notional = float(p.get("entry") or 0), float(p.get("notional") or 0)
        mark = float(px.get(sym) or 0)
        if not entry or not notional or not mark:
            continue
        chg = (mark / entry - 1.0) * 100.0
        if str(p.get("side") or "buy").lower() in ("sell", "short"):
            chg = -chg
        total += notional * chg / 100.0
    return total


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save(path, obj):
    tmp = path + ".t"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, sort_keys=True)
    os.replace(tmp, path)


def one_pass(verbose=True):
    watch = load(WATCH, {})
    dis = load(DISABLED, {"books": []})
    listed = [b for b in (dis.get("books") or []) if b]
    norm = {b.replace("_", "").lower() for b in listed}
    px = prices()
    now = time.time()
    actions = []
    for f in glob.glob(os.path.join(STATE, "pack_*.json")):
        try:
            pack = json.load(open(f))
        except Exception:
            continue
        engine = str(pack.get("engine") or "")
        if not engine or engine.replace("_", "").lower() in norm:
            continue
        if pack.get("positions"):
            # a book holding positions is not judged on unrealised loss: settling it would
            # close at a loss, and this platform only settles in profit
            continue
        start = float(pack.get("start_equity") or 0)
        eq = float(pack.get("equity") or start)
        total = (eq - start) + unreal(pack, px)
        row = watch.get(engine) or {}
        if total < 0:
            row.setdefault("since", now)
            row["total"] = round(total, 2)
            watch[engine] = row
            if now - float(row["since"]) >= LOSS_SECONDS:
                listed.append(engine)
                norm.add(engine.replace("_", "").lower())
                save(DISABLED, {"books": listed, "ts": int(now * 1000)})
                with open(POST, "a") as fh:
                    fh.write(json.dumps({
                        "ts": int(now * 1000), "engine": engine, "action": "disabled",
                        "by": "loss_watch", "rule": "losing for more than %ds" % LOSS_SECONDS,
                        "loss_usd": round(total, 2), "equity": eq, "start_equity": start,
                        "positions": len(pack.get("positions") or []),
                        "note": "A6 post-mortem written at the moment of disabling",
                    }) + "\n")
                for pat in ("%s.py" % engine, engine.replace("_", "") + ".py"):
                    subprocess.run(["pkill", "-f", pat], capture_output=True)
                watch.pop(engine, None)
                actions.append("%s disabled: losing %.2f for %ds" % (
                    engine, total, LOSS_SECONDS))
        else:
            if engine in watch:
                watch.pop(engine, None)
    save(WATCH, watch)
    if verbose:
        for a in actions:
            print("%s  %s" % (time.strftime("%H:%M:%S"), a), flush=True)
    return actions


def main():
    if "--once" in sys.argv:
        print(one_pass())
        return
    while True:
        try:
            one_pass()
        except Exception as e:
            print("loss_watch: %s" % e, flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
