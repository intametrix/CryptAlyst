"""feed - push subscription to the signal bus.

Design patterns:
  Observer   every engine gets a handler, the feed fans out to all of them
  Adapter    STOMP (RabbitMQ) behind a simple Python API
  Cache      the last value per symbol, so a slow consumer never blocks others

Engines call Feed.instance().start() once, then latest()/for_symbol().
Nothing here polls: messages arrive as RabbitMQ pushes them.
"""
import json, os, sys, threading, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


class Feed:
    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = Feed()
        return cls._inst

    def __init__(self):
        self.signals = {}          # symbol -> latest signal dict
        self.ticks = {}            # symbol -> price
        self.counts = {"signals": 0, "ticks": 0}
        self.started = False
        self.connected = False
        self._lock = threading.Lock()
        self._handlers = []

    # ---- observer registration
    def on_signal(self, fn):
        self._handlers.append(fn)

    # ---- adapters
    def _on_message(self, destination, body):
        try:
            payload = json.loads(body.decode() if isinstance(body, bytes) else body)
        except Exception:
            return
        topic = destination.rsplit("/", 1)[-1]
        with self._lock:
            if topic == "signals":
                sym = payload.get("symbol")
                if sym:
                    self.signals[sym] = payload
                    self.counts["signals"] += 1
                    for fn in self._handlers:
                        try:
                            fn(payload)
                        except Exception:
                            pass
            elif topic == "ticks":
                for sym, px in (payload.get("p") or {}).items():
                    self.ticks[sym] = px
                self.counts["ticks"] += 1

    def start(self):
        if self.started:
            return self
        self.started = True
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run_sse(self):
        """Push from the event bus over SSE: one long-lived request, no polling."""
        import urllib.request
        url = "http://127.0.0.1:8797/subscribe?topics=signals,ticks"
        while True:
            try:
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=None) as r:
                    self.connected = True
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            env = json.loads(line[5:].strip())
                        except Exception:
                            continue
                        self._on_message("/exchange/market/" + (env.get("topic") or "signals"),
                                         json.dumps(env.get("data") or {}).encode())
            except Exception:
                self.connected = False
                time.sleep(3)

    def _run(self):
        threading.Thread(target=self._run_sse, daemon=True).start()
        from rmq import Stomp
        while True:
            try:
                c = Stomp()
                c.connect()
                c.subscribe("signals", self._on_message)
                c.subscribe("ticks", self._on_message)
                self.connected = True
                while True:                 # reader thread does the work
                    time.sleep(5)
            except Exception:
                self.connected = False
                time.sleep(5)

    # ---- readers
    def latest(self):
        with self._lock:
            return dict(self.signals)

    def for_symbol(self, sym):
        with self._lock:
            return self.signals.get(sym)

    def price(self, sym):
        with self._lock:
            return self.ticks.get(sym)

    def health(self):
        return {"connected": self.connected, "signals": self.counts["signals"],
                "ticks": self.counts["ticks"], "symbols": len(self.signals)}
