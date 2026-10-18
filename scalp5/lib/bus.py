"""bus - the platform event bus.

Design patterns:
  Envelope      every message carries v/seq/ts/topic/key/producer
  Registry      topics declare their allowed producers and retention
  Adapter       STOMP to RabbitMQ, SSE mirror for the UI, one API for both
  Observer      subscribe(pattern, handler); wildcards are routing keys
  Guard         a producer may only publish to topics it owns

Only these services are allowed to produce. Everything else consumes.
"""
import json, os, sys, threading, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# topic -> (allowed producers, retention hint, exchange)
TOPICS = {
    "market.price":        (["market_data"], "drop", "market"),
    "market.candle":       (["market_data"], "drop", "market"),
    "market.mini24h":      (["market_data"], "drop", "market"),
    "market.depth":        (["market_data"], "drop", "market"),
    "intel.signal":        (["signals"], "60s", "intel"),
    "intel.flow":          (["signals"], "60s", "intel"),
    "intel.movers":        (["market_data", "signals"], "60s", "intel"),
    "intel.forecast":      (["forecast"], "60s", "intel"),
    "intel.ta":            (["tad"], "60s", "intel"),
    "alert.pumpdump":      (["backend"], "300s", "intel"),
    "model.decision":      (["alpha_rank", "models10", "crypto_pack", "executor",
                             "alpha_signals", "skim_pump", "curve_rider"], "5m", "model"),
    "model.fill":          (["alpha_rank", "models10", "crypto_pack", "executor",
                             "alpha_signals", "skim_pump", "curve_rider"], "5m", "model"),
    "model.pnl":           (["alpha_rank", "models10", "crypto_pack", "executor",
                             "alpha_signals", "skim_pump", "curve_rider"], "5m", "model"),
    "model.rank":          (["alpha_rank"], "5m", "model"),
    "risk.state":          (["risk"], "5m", "risk"),
    "review.verdict":      (["portfolio_review"], "5m", "risk"),
    "exec.quote":          (["maker"], "60s", "model"),
    "system.health":       (["market_data", "signals", "forecast", "master"], "10m", "system"),
}

_seq = {"n": 0}
_lock = threading.Lock()
STATS = {t: {"published": 0, "last_ts": 0, "rejected": 0} for t in TOPICS}


class Bus:
    _inst = None

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = Bus()
        return cls._inst

    def __init__(self):
        self.client = None
        self.lock = threading.Lock()
        self.connected = False

    # ---- connection
    def _client(self):
        with self.lock:
            if self.client is not None:
                return self.client
            try:
                from rmq import Stomp
                c = Stomp()
                c.connect()
                self.client = c
                self.connected = True
            except Exception:
                self.client = None
                self.connected = False
            return self.client

    # ---- publish
    def publish(self, topic, key, data, producer):
        spec = TOPICS.get(topic)
        if spec is None:
            STATS.setdefault(topic, {"published": 0, "last_ts": 0, "rejected": 0})
            STATS[topic]["rejected"] += 1
            return False
        if producer not in spec[0]:
            STATS[topic]["rejected"] += 1
            return False
        with _lock:
            _seq["n"] += 1
            env = {"v": 1, "seq": _seq["n"], "ts": int(time.time() * 1000),
                   "topic": topic, "key": key, "producer": producer, "data": data}
        ok = False
        c = self._client()
        if c is not None:
            try:
                c.publish(topic, env, exchange=spec[2])
                ok = True
            except Exception:
                self.client = None
                self.connected = False
        # SSE mirror so browser views and light consumers do not need STOMP
        try:
            import urllib.request as _u
            _u.urlopen(_u.Request("http://127.0.0.1:8797/publish?topic=" + topic,
                                  data=json.dumps(env).encode(), method="POST",
                                  headers={"Content-Type": "application/json"}), timeout=1.5).read()
            ok = True
        except Exception:
            pass
        if ok:
            STATS[topic]["published"] += 1
            STATS[topic]["last_ts"] = env["ts"]
            STATS[topic]["last_key"] = key
        return ok

    # ---- subscribe
    def subscribe(self, pattern, handler):
        c = self._client()
        if c is None:
            return False
        def wrap(destination, body):
            try:
                env = json.loads(body.decode() if isinstance(body, bytes) else body)
            except Exception:
                return
            try:
                handler(env)
            except Exception:
                pass
        c.subscribe(pattern, wrap)
        return True

    def matrix(self):
        out = []
        for t, spec in TOPICS.items():
            s = STATS.get(t, {})
            out.append({"topic": t, "exchange": spec[2], "retention": spec[1],
                        "producers": spec[0], "published": s.get("published", 0),
                        "rejected": s.get("rejected", 0), "last_ts": s.get("last_ts", 0),
                        "last_key": s.get("last_key")})
        out.sort(key=lambda r: -r["published"])
        return out


def pub(topic, key, data, producer):
    return Bus.instance().publish(topic, key, data, producer)


def sub(pattern, handler):
    return Bus.instance().subscribe(pattern, handler)
