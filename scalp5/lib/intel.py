"""intel - the shared, bus-fed view of what the platform knows.

Every model needs the same intelligence: the 20-field signal set, money flow,
technical confirmations, the forecast blend and the alert stream. Fetching that
over HTTP from each engine meant N requests per cycle and a different snapshot
per model.

This subscribes once, keeps the latest message per symbol, and hands the same
view to everyone.

    from lib import intel
    intel.get("BTCUSDT")        -> merged dict for one symbol
    intel.snapshot()            -> {"symbols": n, "counts": {...}, "age_s": ...}

Patterns: Singleton (one subscription per process), Observer (bus handlers),
Cache (latest-per-symbol), Facade (get/snapshot for callers).
"""
from __future__ import annotations

import threading
import time

import base64
import json as _json
import urllib.request as _url

from lib import bus

TOPIC_FIELDS = {
    "intel.signal": "signal",
    "intel.flow": "flow",
    "intel.ta": "ta",
    "intel.forecast": "forecast",
    "alert.pumpdump": "alert",
    "market.depth": "depth",
}


def _ensure_queue(queue, routing_key):
    """Declare a per-reader queue bound to the topic.

    It is auto-delete with an expiry: when the last reader disconnects the queue
    goes with it. Left durable, a stopped engine leaves a queue behind that fills
    at the full publish rate - i.market.depth reached a million messages at 300/s
    with nothing consuming it.
    """
    auth = {"Authorization": "Basic " + base64.b64encode(b"cryptalyst:cryptalyst").decode(),
            "Content-Type": "application/json"}
    exchange = routing_key.split(".")[0]
    base = "http://127.0.0.1:15672/api"
    for path, body in (("/queues/%2F/" + queue,
                        {"durable": False, "auto_delete": True,
                         "arguments": {"x-expires": 600000}}),
                       ("/bindings/%2F/e/" + exchange + "/q/" + queue,
                        {"routing_key": routing_key, "arguments": {}})):
        try:
            req = _url.Request(base + path, method="PUT" if "queues" in path else "POST",
                               data=_json.dumps(body).encode(), headers=auth)
            _url.urlopen(req, timeout=5).read()
        except Exception:
            pass


class Intel:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.rows = {}            # symbol -> {signal, flow, ta, forecast, alert, depth}
        self.counts = {kind: 0 for kind in TOPIC_FIELDS.values()}
        self.updated = {}         # kind -> last ts (ms)
        self.started = False
        self.errors = []
        self.subscribed = {}

    @classmethod
    def instance(cls) -> "Intel":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ---- observer
    def start(self):
        if self.started:
            return
        self.started = True
        self.subscribed = {}
        for topic, kind in TOPIC_FIELDS.items():
            # a dedicated queue per topic: every reader gets every message, and
            # a queue subscription proved more reliable than an exchange one
            queue = "i." + topic
            try:
                _ensure_queue(queue, topic)
                self.subscribed[topic] = bool(bus.sub_queue(queue, self._handler(kind)))
            except Exception as exc:
                self.subscribed[topic] = False
                self.errors.append("%s: %r" % (topic, exc))

    def _handler(self, kind):
        def handler(env):
            payload = env.data if isinstance(env.data, dict) else {}
            sym = (payload.get("symbol") or payload.get("s") or env.key or "").upper()
            if not sym or sym == "ALL":
                return
            with self._lock:
                row = self.rows.setdefault(sym, {})
                row[kind] = payload
                row["ts"] = env.ts or int(time.time() * 1000)
                self.counts[kind] = self.counts.get(kind, 0) + 1
                self.updated[kind] = int(time.time() * 1000)
        return handler

    # ---- facade
    def get(self, symbol: str) -> dict:
        """Merged view: signal fields, then flow, TA and forecast on top."""
        sym = (symbol or "").upper()
        with self._lock:
            row = dict(self.rows.get(sym) or {})
        out = {}
        sig = row.get("signal") or {}
        if isinstance(sig, dict):
            out.update(sig)
        flow = row.get("flow") or {}
        if flow:
            out.setdefault("buy_ratio", flow.get("buy_ratio"))
            out["flow_conf"] = flow.get("flow_conf", out.get("flow_conf"))
            out["flow_series"] = flow.get("flow_series")
        ta = row.get("ta") or {}
        if ta:
            sigs = ta.get("signals")
            if isinstance(sigs, list):
                out["ta_signals"] = sigs
                out["ta_count"] = ta.get("count", len(sigs))
            elif sigs is None and "count" in ta:
                out["ta_count"] = ta.get("count") or 0
            out["ta"] = ta.get("signal") or ta
        fc = row.get("forecast") or {}
        if fc:
            out["forecast_prob"] = fc.get("prob")
            out["forecast_move"] = fc.get("move_pct")
        alert = row.get("alert") or {}
        if alert:
            out["alert_kind"] = alert.get("kind")
            out["alert_score"] = alert.get("score")
        depth = row.get("depth") or {}
        if depth:
            out.setdefault("book_imbalance", depth.get("imbalance"))
            out["depth_spread_bps"] = depth.get("spread_bps")
        out["_ts"] = row.get("ts")
        return out

    def all(self) -> dict:
        """Merged view for every symbol we have heard about."""
        with self._lock:
            syms = list(self.rows)
        return {sym: self.get(sym) for sym in syms}

    def snapshot(self) -> dict:
        with self._lock:
            fresh = {k: int(time.time() * 1000) - v for k, v in self.updated.items()}
            return {"symbols": len(self.rows), "counts": dict(self.counts),
                    "age_ms": fresh, "errors": list(self.errors[-3:]),
                    "subscribed": dict(getattr(self, "subscribed", {}))}


def get(symbol: str) -> dict:
    inst = Intel.instance()
    inst.start()
    return inst.get(symbol)


def all() -> dict:
    inst = Intel.instance()
    inst.start()
    return inst.all()


def snapshot() -> dict:
    inst = Intel.instance()
    inst.start()
    return inst.snapshot()
