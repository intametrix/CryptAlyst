#!/usr/bin/env python3
"""bus_purge - clear backlogs that nothing is consuming.

Market data is a snapshot, not a queue: a queue with no consumer that fills at
publish rate is storage waste and a lie about health. This deletes the intel
queues that have no reader, and reports anything else that is deep.

    python3 tools/bus_purge.py            # report and purge readerless queues
    python3 tools/bus_purge.py --dry      # report only
"""
import base64, json, sys, urllib.request

BASE = "http://127.0.0.1:15672/api"
AUTH = {"Authorization": "Basic " + base64.b64encode(b"cryptalyst:cryptalyst").decode()}
DRY = "--dry" in sys.argv
LIMIT = 1000


def api(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method, headers=AUTH)
    with urllib.request.urlopen(req, timeout=8) as r:
        body = r.read()
        return json.loads(body) if body else None


def main():
    queues = api("/queues")
    deep = sorted(((q.get("messages", 0), q["name"], q.get("consumers", 0)) for q in queues),
                  reverse=True)
    total = sum(q[0] for q in deep)
    print("queues: %d | messages: %d" % (len(queues), total))
    purged = deleted = 0
    for msgs, name, consumers in deep:
        if name.startswith("stomp-subscription-"):
            continue
        if consumers == 0 and (msgs >= LIMIT or name.startswith("i.")):
            if DRY:
                print("  would delete %-30s %8d messages, no consumer" % (name, msgs))
                continue
            api("/queues/%%2F/%s" % name, "DELETE")
            print("  deleted %-30s %8d messages, no consumer" % (name, msgs))
            deleted += 1
            purged += msgs
        elif msgs >= LIMIT:
            print("  BACKLOG %-30s %8d messages, %d consumers" % (name, msgs, consumers))
    print("deleted %d queues, %d messages" % (deleted, purged))


if __name__ == "__main__":
    main()
