#!/usr/bin/env python3
"""listen - print every new KIMI message the moment it lands, never skip one."""
import json, os, time
LOG = os.environ.get("BRIDGE_LOG", "/Users/users1/Documents/kimi/tasks/2026-10-17/11-08-11-ea1d54fe/bridge.log")
seen = 0
print("LISTENING for kimi messages", flush=True)
while True:
    try:
        with open(LOG) as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                m = json.loads(line)
                if m["id"] <= seen:
                    continue
                seen = m["id"]
                if m["from"] == "kimi":
                    print("KIMI #%s [%s] %s" % (m["id"], m["topic"], m["body"]), flush=True)
    except FileNotFoundError:
        pass
    time.sleep(0.5)
