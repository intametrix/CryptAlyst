#!/usr/bin/env python3
"""stop_reset - the STOP the owner defined.

STOP means: clear the books, clear the messages, start fresh. It does NOT touch the
enable/disable state of any engine or model - that list belongs to the human and a reset
is not a decision to re-enable anything.

    python3 tools/stop_reset.py            # do it
    python3 tools/stop_reset.py --dry      # say what it would do
"""
import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")
DRY = "--dry" in sys.argv
RMQ = "http://127.0.0.1:15672/api"
AUTH = {"Authorization": "Basic " + base64.b64encode(b"cryptalyst:cryptalyst").decode()}


def say(msg):
    print(("  would " if DRY else "  ") + msg)


def stop_flag():
    p = os.path.join(STATE, "STOP")
    say("write %s so nothing trades while the reset runs" % p)
    if not DRY:
        with open(p, "w") as fh:
            json.dump({"stopped_at": int(time.time() * 1000), "actor": "stop_reset"}, fh)


def kill_engines():
    import re
    src = open(os.path.join(HERE, "supervisor.py")).read()
    scripts = sorted(set(re.findall(r'"([a-z0-9_]+\.py)"', src)))
    for s in scripts:
        say("stop %s" % s)
        if not DRY:
            subprocess.run(["pkill", "-f", s], capture_output=True)
    if not DRY:
        time.sleep(2)


def archive_and_reset():
    ad = os.path.join(STATE, "archive")
    os.makedirs(ad, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for f in sorted(glob.glob(os.path.join(STATE, "pack_*.json"))):
        eng = os.path.basename(f)[len("pack_"):-5]
        dst = os.path.join(ad, "stop_%s_%s.json" % (eng, stamp))
        say("archive %s -> %s" % (eng, os.path.basename(dst)))
        if DRY:
            continue
        try:
            shutil.copy2(f, dst)
            d = json.load(open(f))
            start = d.get("start_equity") or d.get("equity") or 5000
            d["equity"] = start
            d["positions"] = []
            d["trades"] = []
            d["ts"] = int(time.time() * 1000)
            d["reset_at"] = int(time.time() * 1000)
            with open(f + ".t", "w") as fh:
                json.dump(d, fh)
            os.replace(f + ".t", f)
        except Exception as e:
            print("    ! %s: %s" % (eng, e))


def clear_messages():
    purged = 0
    try:
        req = urllib.request.Request(RMQ + "/queues", headers=AUTH)
        queues = json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        print("    ! broker unreachable: %s" % e)
        return
    for q in queues:
        name = q.get("name") or ""
        if name.startswith("stomp-subscription-"):
            continue
        say("purge %s (%d messages)" % (name, q.get("messages", 0)))
        purged += 1
        if DRY:
            continue
        try:
            body = json.dumps({"vhost": "/", "name": name, "mode": "purge"}).encode()
            req = urllib.request.Request(RMQ + "/queues/%2F/" + name + "/contents",
                                         data=body, method="DELETE",
                                         headers={**AUTH, "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).read()
        except Exception as e:
            print("    ! purge %s: %s" % (name, e))
    return purged


def restart_enabled():
    say("start the supervisor again (it skips every disabled book - the list is untouched)")
    if DRY:
        return
    subprocess.run("nohup python3 -u supervisor.py --every 60 --hours 8 "
                   "> logs/supervisor.log 2>&1 &", shell=True, cwd=HERE)


def clear_stop():
    p = os.path.join(STATE, "STOP")
    say("remove %s so the fresh books can trade" % p)
    if not DRY:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def main():
    before = []
    try:
        before = json.load(open(os.path.join(STATE, "disabled.json"))).get("books") or []
    except Exception:
        pass
    print("STOP: clear books, clear messages, start fresh. Disables are NOT touched.")
    print("  disabled before: %d books" % len(before))
    stop_flag()
    kill_engines()
    books = len(glob.glob(os.path.join(STATE, "pack_*.json")))
    archive_and_reset()
    purged = clear_messages()
    if not DRY:
        try:
            with open(os.path.join(STATE, "stop_reset.json"), "w") as fh:
                json.dump({"ts": int(time.time() * 1000), "books": books,
                           "queues": purged or 0, "disabled_before": len(before)}, fh)
        except Exception:
            pass
    say("start loss_watch (a model losing for more than 30s is disabled)")
    if not DRY:
        subprocess.run("pgrep -f loss_watch.py >/dev/null || "
                       "nohup python3 -u tools/loss_watch.py > logs/loss_watch.log 2>&1 &",
                       shell=True, cwd=HERE)
    restart_enabled()
    clear_stop()
    try:
        after = json.load(open(os.path.join(STATE, "disabled.json"))).get("books") or []
    except Exception:
        after = before
    print("  disabled after : %d books (%s)" % (
        len(after), "unchanged" if after == before else "CHANGED — investigate"))


if __name__ == "__main__":
    main()
