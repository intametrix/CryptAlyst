#!/usr/bin/env python3
"""Render each SVG figure to PNG through headless Chrome (same engine the app uses)."""
import base64, glob, json, os, socket, struct, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

def cdp(port=9222):
    pages = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port))
    page = [p for p in pages if p.get("type") == "page"][0]
    ws = page["webSocketDebuggerUrl"]
    hp = ws.split("//", 1)[1].split("/", 1)[0]
    host, prt = hp.split(":")
    s = socket.create_connection((host, int(prt)), timeout=20)
    import base64 as b64
    key = b64.b64encode(os.urandom(16)).decode()
    path = ws.split(hp, 1)[1]
    s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, hp, key)).encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += s.recv(4096)
    return s

def call(s, i, method, params=None):
    data = json.dumps({"id": i, "method": method, "params": params or {}}).encode()
    mask = os.urandom(4); n = len(data)
    frame = bytearray([0x81])
    if n < 126: frame.append(0x80 | n)
    elif n < 65536: frame.append(0x80 | 126); frame += struct.pack(">H", n)
    else: frame.append(0x80 | 127); frame += struct.pack(">Q", n)
    frame += mask + bytes(b ^ mask[k % 4] for k, b in enumerate(data))
    s.sendall(bytes(frame))
    while True:
        h = s.recv(2)
        ln = h[1] & 0x7F
        if ln == 126: ln = struct.unpack(">H", s.recv(2))[0]
        elif ln == 127: ln = struct.unpack(">Q", s.recv(8))[0]
        body = b""
        while len(body) < ln:
            body += s.recv(ln - len(body))
        msg = json.loads(body)
        if msg.get("id") == i:
            return msg.get("result", {})

def main():
    import re
    files = sorted(glob.glob(os.path.join(HERE, "fig", "*.svg")))
    s = cdp()
    i = 0
    for path in files:
        svg = open(path).read()
        w = int(re.search(r'width="(\d+)"', svg).group(1))
        h = int(re.search(r'height="(\d+)"', svg).group(1))
        i += 1; call(s, i, "Emulation.setDeviceMetricsOverride",
                     {"width": w, "height": h, "deviceScaleFactor": 2, "mobile": False})
        i += 1; call(s, i, "Page.navigate", {"url": "http://127.0.0.1:8811/fig/" + os.path.basename(path)})
        time.sleep(2.5)
        i += 1; r = call(s, i, "Page.captureScreenshot", {"format": "png"})
        out = path.replace(".svg", ".png")
        open(out, "wb").write(base64.b64decode(r["data"]))
        print("rendered", os.path.basename(out), w, "x", h)

if __name__ == "__main__":
    main()
