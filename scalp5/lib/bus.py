"""bus - publish/subscribe over RabbitMQ.

Patterns in play
----------------
Registry      TopicRegistry owns the topic table: which producers may write a
              topic, how long the broker should keep it, which exchange carries
              it. Nothing else knows those rules.
Envelope      Every message is one shape: version, sequence, timestamp, topic,
              routing key, producer, payload. Consumers never guess at the shape.
Adapter       Transport is an interface. StompTransport is the production one;
              InMemoryTransport is what the tests speak to. The Bus depends on
              the interface, not on RabbitMQ.
Proxy         Bus.publish is a guard in front of the transport: it refuses
              unknown topics and unauthorised producers and counts both.
Observer      Subscriber attaches handlers to destinations and republishes
              decoded envelopes; it owns reconnect and backoff.
Facade        pub(), sub(), matrix() are the whole public surface for services.

Only these services produce. Everything else consumes.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, Iterable, List, Optional, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------- registry
@dataclass(frozen=True)
class Topic:
    name: str
    producers: Tuple[str, ...]
    retention: str
    exchange: str

    def allows(self, producer: str) -> bool:
        return producer in self.producers


class TopicRegistry:
    """One table, one owner. Adding a topic is a single line here."""

    _TABLE: Dict[str, Topic] = {
        "market.price":   Topic("market.price", ("market_data",), "drop", "market"),
        "market.candle":  Topic("market.candle", ("market_data",), "drop", "market"),
        "market.mini24h": Topic("market.mini24h", ("market_data",), "drop", "market"),
        "market.depth":   Topic("market.depth", ("market_data",), "drop", "market"),
        "ticks":          Topic("ticks", ("market_data",), "drop", "market"),
        "intel.signal":   Topic("intel.signal", ("signals",), "60s", "intel"),
        "intel.flow":     Topic("intel.flow", ("signals",), "60s", "intel"),
        "intel.movers":   Topic("intel.movers", ("market_data", "signals"), "60s", "intel"),
        "intel.forecast": Topic("intel.forecast", ("forecast",), "60s", "intel"),
        "intel.ta":       Topic("intel.ta", ("tad",), "60s", "intel"),
        "alert.pumpdump": Topic("alert.pumpdump", ("backend", "signal_bus"), "300s", "alert"),
        "model.decision": Topic("model.decision", ("alpha_rank", "models10", "crypto_pack",
                                                   "executor", "alpha_signals", "skim_pump",
                                                   "curve_rider", "hft", "movers", "grid"),
                              "5m", "model"),
        "model.fill":     Topic("model.fill", ("alpha_rank", "models10", "crypto_pack",
                                               "executor", "alpha_signals", "skim_pump",
                                               "curve_rider", "hft", "movers", "grid"),
                              "5m", "model"),
        "model.pnl":      Topic("model.pnl", ("alpha_rank", "models10", "crypto_pack",
                                              "executor", "alpha_signals", "skim_pump",
                                              "curve_rider", "hft", "movers", "grid",
                                              "supervisor"),
                              "5m", "model"),
        "model.rank":     Topic("model.rank", ("alpha_rank",), "5m", "model"),
        "risk.state":     Topic("risk.state", ("risk",), "5m", "risk"),
        "review.verdict": Topic("review.verdict", ("portfolio_review",), "5m", "review"),
        "exec.quote":     Topic("exec.quote", ("maker",), "60s", "exec"),
        "system.health":  Topic("system.health", ("master",), "10m", "system"),
    }

    @classmethod
    def get(cls, name: str) -> Optional[Topic]:
        return cls._TABLE.get(name)

    @classmethod
    def all(cls) -> Iterable[Topic]:
        return cls._TABLE.values()

    @classmethod
    def names(cls) -> List[str]:
        return list(cls._TABLE)

    @classmethod
    def exchanges(cls) -> List[str]:
        return sorted({t.exchange for t in cls._TABLE.values()})


# --------------------------------------------------------------------- envelope
@dataclass
class Envelope:
    topic: str
    key: str
    producer: str
    data: object
    version: int = 1
    seq: int = 0
    ts: int = 0

    _counter = 0
    _lock = threading.Lock()

    @classmethod
    def make(cls, topic: str, key: str, data, producer: str) -> "Envelope":
        with cls._lock:
            cls._counter += 1
            seq = cls._counter
        return cls(topic=topic, key=key, producer=producer, data=data, seq=seq,
                   ts=int(time.time() * 1000))

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict()).encode()

    @classmethod
    def parse(cls, blob) -> Optional["Envelope"]:
        try:
            d = json.loads(blob.decode() if isinstance(blob, (bytes, bytearray)) else blob)
        except Exception:
            return None
        if not isinstance(d, dict):
            return None
        return cls(topic=d.get("topic", ""), key=d.get("key", ""),
                   producer=d.get("producer", ""), data=d.get("data"),
                   version=d.get("v", 1), seq=d.get("seq", 0), ts=d.get("ts", 0))


# --------------------------------------------------------------------- transport
class Transport:
    """Adapter interface. Nothing above this line knows about STOMP."""

    def publish(self, exchange: str, routing_key: str, body: bytes) -> bool:
        raise NotImplementedError

    def subscribe_queue(self, queue: str, handler: Callable[[dict, bytes], None]) -> bool:
        raise NotImplementedError

    def subscribe_exchange(self, exchange: str, routing_key: str,
                           handler: Callable[[dict, bytes], None]) -> bool:
        raise NotImplementedError


class StompTransport(Transport):
    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    def client(self):
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                import rmq
                c = rmq.Stomp()
                c.connect()
                self._client = c
            except Exception:
                self._client = None
            return self._client

    def _drop(self):
        with self._lock:
            self._client = None

    def publish(self, exchange, routing_key, body):
        c = self.client()
        if c is None:
            return False
        try:
            c.publish(routing_key, body, exchange=exchange)
            return True
        except Exception:
            self._drop()
            return False

    def subscribe_queue(self, queue, handler):
        c = self.client()
        if c is None:
            return False
        try:
            c.subscribe_queue(queue, handler)
            return True
        except Exception:
            self._drop()
            return False

    def subscribe_exchange(self, exchange, routing_key, handler):
        c = self.client()
        if c is None:
            return False
        try:
            c.subscribe_topic(routing_key, exchange, handler)
            return True
        except Exception:
            self._drop()
            return False


class InMemoryTransport(Transport):
    """Used by the tests: same interface, no broker."""

    def __init__(self):
        self.published: List[Tuple[str, str, bytes]] = []
        self.handlers: Dict[str, Callable[[dict, bytes], None]] = {}

    def publish(self, exchange, routing_key, body):
        self.published.append((exchange, routing_key, body))
        h = self.handlers.get(routing_key)
        if h:
            h({"destination": "/exchange/%s/%s" % (exchange, routing_key)}, body)
        return True

    def subscribe_queue(self, queue, handler):
        self.handlers[queue] = handler
        return True

    def subscribe_exchange(self, exchange, routing_key, handler):
        self.handlers[routing_key] = handler
        return True


# --------------------------------------------------------------------- metrics
@dataclass
class TopicStats:
    published: int = 0
    rejected: int = 0
    last_ts: int = 0
    last_key: str = ""


# --------------------------------------------------------------------- the bus
class Bus:
    """Facade over registry + transport + metrics."""

    _instance: Optional["Bus"] = None

    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or StompTransport()
        self.stats: Dict[str, TopicStats] = {}
        self._lock = threading.Lock()

    @classmethod
    def instance(cls) -> "Bus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def use(cls, transport: Transport) -> "Bus":
        cls._instance = cls(transport)
        return cls._instance

    # -- publish (Proxy over the transport, with a guard)
    def publish(self, topic: str, key: str, data, producer: str) -> bool:
        spec = TopicRegistry.get(topic)
        st = self.stats.setdefault(topic, TopicStats())
        if spec is None or not spec.allows(producer):
            with self._lock:
                st.rejected += 1
            return False
        env = Envelope.make(topic, key, data, producer)
        ok = self.transport.publish(spec.exchange, topic, env.to_json())
        if ok:
            with self._lock:
                st.published += 1
                st.last_ts = env.ts
                st.last_key = key
        return ok

    # -- observe
    def subscribe_topic(self, topic: str, handler: Callable[[Envelope], None]) -> bool:
        spec = TopicRegistry.get(topic)
        if spec is None:
            return False
        return self.transport.subscribe_exchange(
            spec.exchange, topic, _decoding(handler, topic))

    def subscribe_queue(self, queue: str, handler: Callable[[Envelope], None]) -> bool:
        topic = queue.split(".", 1)[-1]
        return self.transport.subscribe_queue(queue, _decoding(handler, topic))

    def matrix(self) -> List[dict]:
        rows = []
        for spec in TopicRegistry.all():
            st = self.stats.get(spec.name, TopicStats())
            rows.append({"topic": spec.name, "exchange": spec.exchange,
                         "retention": spec.retention, "producers": list(spec.producers),
                         "published": st.published, "rejected": st.rejected,
                         "last_ts": st.last_ts, "last_key": st.last_key})
        rows.sort(key=lambda r: -r["published"])
        return rows


def _decoding(handler: Callable[[Envelope], None], default_topic: str = ""):
    """Adapter: raw (headers, body) -> Envelope -> handler.

    Python services send a full envelope; the Go services send the payload on
    its own. Both are wrapped into one shape here.
    """
    def wrapped(headers, body):
        env = Envelope.parse(body)
        if env is None:
            return
        if not env.topic and default_topic:
            try:
                raw = json.loads(body.decode() if isinstance(body, (bytes, bytearray)) else body)
            except Exception:
                return
            env = Envelope(topic=default_topic, key=str(raw.get("symbol", "")),
                           producer="", data=raw, ts=int(time.time() * 1000))
        handler(env)
    return wrapped


# --------------------------------------------------------------------- facade
def pub(topic: str, key: str, data, producer: str) -> bool:
    return Bus.instance().publish(topic, key, data, producer)


def sub(topic: str, handler: Callable[[Envelope], None]) -> bool:
    return Bus.instance().subscribe_topic(topic, handler)


def sub_queue(queue: str, handler: Callable[[Envelope], None]) -> bool:
    return Bus.instance().subscribe_queue(queue, handler)


def topics() -> List[str]:
    return TopicRegistry.names()


def matrix() -> List[dict]:
    return Bus.instance().matrix()


# backwards-compatible names used across the services
TOPICS = {t.name: (list(t.producers), t.retention, t.exchange) for t in TopicRegistry.all()}
