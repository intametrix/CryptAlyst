#!/usr/bin/env python3
"""ui_automation - drive the dashboard slowly and prove every view renders.

Design: each step clicks, then WAITS for the DOM to settle (content length
stable for N samples) before it judges or moves on. Nothing is evaluated
mid-render, which is what made earlier checks give false failures.

    python3 tools/ui_automation.py --slow 2.0 --shots uishots
"""
import argparse, base64, json, os, socket, struct, time, urllib.request

APP = "http://127.0.0.1:8799/app"
CDP = "http://127.0.0.1:9222/json"

TABS = ["dash", "thinking", "busview", "moversview", "marketlive", "forecast", "advisor",
        "signals", "tape", "models", "opt", "lb", "control", "review", "why", "health"]


class Chrome:
    def __init__(self):
        targets = [t for t in json.load(urllib.request.urlopen(CDP)) if t.get("type") == "page"]
        if not targets:
            raise SystemExit("no chrome page target — start Chrome with --remote-debugging-port=9222")
        self.sock = self._connect(targets[0]["webSocketDebuggerUrl"])
        self.mid = 0
        self.call("Runtime.enable")
        self.call("Page.enable")

    def _connect(self, url):
        rest = url[5:]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        s = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                   % (path, hostport, key)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += s.recv(4096)
        return s

    def _send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytearray([0x81])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126); head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127); head += struct.pack(">Q", n)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv(self):
        def rd(n):
            b = b""
            while len(b) < n:
                c = self.sock.recv(n - len(b))
                if not c:
                    raise EOFError
                b += c
            return b
        h = rd(2)
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", rd(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", rd(8))[0]
        return json.loads(rd(ln).decode("utf-8", "replace"))

    def call(self, method, params=None):
        self.mid += 1
        self._send({"id": self.mid, "method": method, "params": params or {}})
        while True:
            msg = self._recv()
            if msg.get("id") == self.mid:
                return msg.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.call("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(r["data"]))
        return path

    # ---- waiting helpers: never judge mid-render
    def wait_for(self, expr, predicate, timeout=25.0):
        """Poll until predicate(value) is true. Used to wait for real content."""
        t0 = time.time()
        val = None
        while time.time() - t0 < timeout:
            val = self.js(expr)
            try:
                if predicate(val):
                    return val
            except Exception:
                pass
            time.sleep(0.25)
        return val

    def wait_stable(self, expr, quiet=1.2, timeout=12.0):
        """Wait until expr stops changing for `quiet` seconds."""
        t0 = time.time()
        last, since = None, time.time()
        while time.time() - t0 < timeout:
            val = self.js(expr)
            if val != last:
                last, since = val, time.time()
            elif time.time() - since >= quiet:
                return val
            time.sleep(0.25)
        return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slow", type=float, default=2.0, help="seconds to let each view render")
    ap.add_argument("--shots", default="uishots")
    a = ap.parse_args()
    os.makedirs(a.shots, exist_ok=True)

    c = Chrome()
    c.call("Emulation.setDeviceMetricsOverride",
           {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    c.js("location.href='%s?v=auto%d'" % (APP, int(time.time())))
    time.sleep(4)
    c.wait_stable("document.body.innerText.length")

    report = []
    print("%-11s %-8s %-7s %-7s %s" % ("view", "content", "stable", "ms", "notes"))
    for tab in TABS:
        t0 = time.time()
        c.js("document.getElementById('nav-%s').click()" % tab)
        time.sleep(a.slow)                                  # let it render
        body = {"dash": "sections", "thinking": "thbody", "busview": "busbody",
                "moversview": "movers", "marketlive": "mlbody", "forecast": "fcbody",
                "advisor": "advboard", "signals": "signalsbody", "tape": "tapebody",
                "models": "modelsbody", "opt": "optbody", "lb": "lbbody",
                "control": "cbody", "review": "rbody", "why": "wbody",
                "health": "healthbody"}.get(tab, tab + "body")
        expr = "(function(){var e=document.getElementById('%s');return e?e.innerHTML.length:-1})()" % body
        # wait for actual content, not merely for the placeholder to stop changing
        size = c.wait_for(expr, lambda v: isinstance(v, (int, float)) and v > 400, timeout=20.0)
        size = c.wait_stable(expr, quiet=0.8, timeout=6.0)
        first_ms = (time.time() - t0) * 1000
        shown = c.js("(function(){var e=document.getElementById('%s');return e?getComputedStyle(e).display:'missing'})()" % tab)
        errors = c.js("(window.__errs||[]).length")
        c.shot(os.path.join(a.shots, "%s.png" % tab))
        ok = isinstance(size, (int, float)) and size > 200 and shown not in ("none", "missing") and (errors or 0) == 0
        report.append({"view": tab, "body": body, "size": size, "shown": shown,
                       "errors": errors, "ok": ok})
        print("%-11s %-8s %-7s %-7d %s" % (tab, size, shown, first_ms,
                                           "" if ok else "NEEDS ATTENTION"))

    passed = sum(1 for r in report if r["ok"])
    print("\n%d/%d views render cleanly" % (passed, len(report)))
    with open("ui_report.json", "w") as fh:
        json.dump({"ts": int(time.time() * 1000), "slow_s": a.slow, "views": report}, fh, indent=1)
    print("report: ui_report.json   screenshots: %s/" % a.shots)


if __name__ == "__main__":
    main()
