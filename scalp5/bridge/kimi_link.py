#!/usr/bin/env python3
"""kimi_link - drive KIMI directly, no human in the loop.

Two transport paths, preferred first:

  1. CLI     ~/.kimi-code/bin/kimi -p "..." --output-format text
             Fastest by far, but it needs a model configured for the user
             running it. When this was written the desktop app's credentials
             were 0600 under another account's home, so this path failed until
             `kimi` /login ran as the current user. Resolved dynamically via
             expanduser(~) so it follows whoever runs the script.

  2. GUI     activate Kimi.app, paste the prompt, press Return. Works today.
             Replies come back through the bridge, so we still read them
             programmatically.

    python3 kimi_link.py ask "question" --timeout 180
    python3 kimi_link.py send "note, no reply needed"
    python3 kimi_link.py check
"""

import argparse
import json
import os
import subprocess
import time
import urllib.request

KIMI_BIN = os.path.expanduser("~/.kimi-code/bin/kimi")
BRIDGE = "http://127.0.0.1:8810"


def jget(url, timeout=60.0):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "kimi-link/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def last_id():
    try:
        ms = jget(BRIDGE + "/log?since=0", timeout=8).get("messages") or []
        return ms[-1]["id"] if ms else 0
    except Exception:
        return 0


def cli_available():
    try:
        r = subprocess.run([KIMI_BIN, "-p", "say OK", "--output-format", "text"],
                           capture_output=True, text=True, timeout=90)
        return r.returncode == 0 and "No model configured" not in (r.stderr + r.stdout)
    except Exception:
        return False


def gui_send(text):
    """Paste the prompt into the Kimi window and hit Return."""
    script = '''
set the clipboard to %s
tell application "System Events"
  tell process "Kimi"
    set frontmost to true
    delay 0.5
    keystroke "v" using command down
    delay 0.4
    keystroke return
  end tell
end tell
''' % json.dumps(text)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()


def ask(text, timeout):
    """Send a prompt, then wait for the next bridge message from KIMI."""
    since = last_id()
    ok, err = gui_send(text)
    if not ok:
        # a GUI failure is usually accessibility permission; fall back to the log
        return {"sent": False, "error": err, "reply": None}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ms = jget(BRIDGE + "/wait?to=dsh&since=%d&timeout=15" % since, timeout=20).get("messages") or []
        except Exception:
            ms = []
        for m in ms:
            if m["from"] == "kimi":
                return {"sent": True, "reply": m["body"], "id": m["id"]}
        if ms:
            since = ms[-1]["id"]
    return {"sent": True, "reply": None, "note": "no bridge reply within %ds" % timeout}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("text")
    a = sub.add_parser("ask")
    a.add_argument("text")
    a.add_argument("--timeout", type=float, default=180)
    sub.add_parser("check")
    args = ap.parse_args()

    if args.cmd == "check":
        print(json.dumps({"cli": KIMI_BIN, "cli_configured": cli_available(),
                          "bridge": BRIDGE, "last_id": last_id()}, indent=1))
    elif args.cmd == "send":
        print(json.dumps({"sent": gui_send(args.text)[0]}))
    elif args.cmd == "ask":
        r = ask(args.text, args.timeout)
        print(json.dumps(r, indent=1)[:4000])


if __name__ == "__main__":
    main()
