#!/usr/bin/env python3
"""listen - print every new KIMI message the moment it lands, never skip one."""
import json, os, time
HERE = os.path.dirname(os.path.abspath(__file__))
# Default to the Go bridge's log next to this script; override with BRIDGE_LOG.
# (Was a hardcoded dated task dir under another user's home -- it happened to
# still exist, which is exactly what makes that kind of path dangerous.)
LOG = os.environ.get("BRIDGE_LOG", os.path.join(HERE, "bridge.log"))
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
