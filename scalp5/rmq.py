#!/usr/bin/env python3
"""rmq - a tiny STOMP client for RabbitMQ (no pip dependencies).

RabbitMQ ships a STOMP plugin, which is a text protocol over TCP, so we can use
it from stdlib Python. Publishes to a topic exchange; consumes with a
subscription id so the same connection can fan out to many SSE clients.
"""
import json, socket, threading, time, uuid

HOST, PORT = "127.0.0.1", 61613
LOGIN, PASSCODE = "cryptalyst", "cryptalyst"
EXCHANGE = "/exchange/market"


class Stomp:
    def __init__(self, host=HOST, port=PORT):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.lock = threading.Lock()
        self.buf = b""
        self.handlers = {}
        self.connected = False

    # ---- framing
    def _frame(self, cmd, headers, body=b""):
        head = cmd + "\n" + "".join("%s:%s\n" % (k, v) for k, v in headers.items()) + "\n"
        return head.encode() + body + b"\x00"

    def connect(self):
        self.sock.sendall(self._frame("CONNECT", {
            "accept-version": "1.2", "host": "/", "login": LOGIN, "passcode": PASSCODE}))
        frame = self._read_frame(timeout=10)
        if not frame or not frame[0].startswith("CONNECTED"):
            raise RuntimeError("stomp connect failed: %r" % (frame,))
        self.connected = True
        threading.Thread(target=self._reader, daemon=True).start()
        return True

    def _read_frame(self, timeout=30):
        self.sock.settimeout(timeout)
        while b"\x00" not in self.buf:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self.buf += chunk
        raw, self.buf = self.buf.split(b"\x00", 1)
        raw = raw.lstrip(b"\n")
        if not raw:
            return None
        head, _, body = raw.partition(b"\n\n")
        lines = head.decode("utf-8", "replace").split("\n")
        cmd = lines[0]
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k] = v
        return cmd, headers, body

    def _reader(self):
        while True:
            try:
                frame = self._read_frame(timeout=None)
            except Exception:
                return
            if not frame:
                return
            cmd, headers, body = frame
            if cmd == "MESSAGE":
                sid = headers.get("subscription")
                h = self.handlers.get(sid)
                if h:
                    try:
                        h(headers, body)
                    except Exception:
                        pass
            elif cmd == "ERROR":
                print("stomp error:", headers, body[:200], flush=True)

    # ---- operations
    def publish(self, topic, payload, exchange=None):
        body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        dest = ("/exchange/" + exchange if exchange else EXCHANGE) + "/" + topic
        with self.lock:
            self.sock.sendall(self._frame("SEND", {
                "destination": dest,
                "content-type": "application/json",
                "content-length": str(len(body))}, body))

    def subscribe_queue(self, queue, handler):
        """Subscribe to a queue by name (competing consumer semantics).

        A STOMP /queue/ subscription creates a DURABLE queue unless told otherwise,
        so a reader that dies leaves a queue behind that fills at the publish rate -
        i.market.depth reached 200k messages that way. The intel family is declared
        bounded and self-expiring in the subscription itself, so a dead reader's queue
        disappears and a slow one keeps only the newest messages.
        """
        sid = "q-" + queue + "-" + uuid.uuid4().hex[:8]
        self.handlers[sid] = handler
        head = {"destination": "/queue/" + queue, "id": sid, "ack": "auto"}
        if queue.startswith("i."):
            head.update({
                "auto-delete": "true",
                "x-expires": "120000",       # 2 minutes without a consumer
                "x-max-length": "2000",      # never a backlog
                "x-overflow": "drop-head",   # newest win
                "x-message-ttl": "60000",    # nothing older than a minute
            })
        with self.lock:
            self.sock.sendall(self._frame("SUBSCRIBE", head))
        return sid

    def subscribe_topic(self, topic, exchange, handler):
        """Subscribe to a routing key on a named exchange."""
        sid = "sub-" + exchange + "-" + topic + "-" + uuid.uuid4().hex[:8]
        self.handlers[sid] = handler
        with self.lock:
            self.sock.sendall(self._frame("SUBSCRIBE", {
                "destination": "/exchange/" + exchange + "/" + topic,
                "id": sid, "ack": "auto"}))
        return sid

    def subscribe(self, topic, handler, sid=None):
        sid = sid or ("sub-" + topic + "-" + uuid.uuid4().hex[:8])
        self.handlers[sid] = handler
        with self.lock:
            self.sock.sendall(self._frame("SUBSCRIBE", {
                "destination": EXCHANGE + "/" + topic,
                "id": sid, "ack": "auto"}))
        return sid
