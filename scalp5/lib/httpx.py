"""httpx - one HTTP client for the platform.

Design patterns:
  Adapter      every caller gets the same get()/post() surface
  Circuit      a failing dependency is skipped for a cool-down instead of retried
  Cache        TTL cache per URL so identical reads are served once
  Rate limit   token bucket, shared by all callers in this process
"""
import json, threading, time, urllib.error, urllib.request

CIRCUIT_OPEN_S = 20.0


class Circuit:
    def __init__(self):
        self.failures = {}
        self.opened_at = {}
        self.lock = threading.Lock()

    def is_open(self, host):
        with self.lock:
            t = self.opened_at.get(host)
            if t and time.time() - t < CIRCUIT_OPEN_S:
                return True
            if t:
                self.opened_at.pop(host, None)
            return False

    def fail(self, host):
        with self.lock:
            self.failures[host] = self.failures.get(host, 0) + 1
            if self.failures[host] >= 3:
                self.opened_at[host] = time.time()
                self.failures[host] = 0

    def ok(self, host):
        with self.lock:
            self.failures.pop(host, None)


class RateLimit:
    def __init__(self, per_second=20.0):
        self.capacity = per_second
        self.tokens = per_second
        self.ts = time.time()
        self.lock = threading.Lock()

    def take(self):
        while True:
            with self.lock:
                now = time.time()
                self.tokens = min(self.capacity, self.tokens + (now - self.ts) * self.capacity)
                self.ts = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return True
            time.sleep(0.01)


CIRCUIT = Circuit()
LIMIT = RateLimit()


class Client:
    def __init__(self, timeout=8.0, ttl=2.0, headers=None):
        self.timeout = timeout
        self.ttl = ttl
        self.headers = headers or {}
        self.cache = {}
        self.stats = {"calls": 0, "cached": 0, "errors": 0, "circuit_skips": 0}
        self.lock = threading.Lock()

    def get(self, url, ttl=None, timeout=None, as_json=True):
        ttl = self.ttl if ttl is None else ttl
        now = time.time()
        with self.lock:
            hit = self.cache.get(url)
            if hit and now - hit[0] < ttl:
                self.stats["cached"] += 1
                return hit[1]
        host = url.split("/")[2] if "//" in url else url
        if CIRCUIT.is_open(host):
            self.stats["circuit_skips"] += 1
            with self.lock:
                hit = self.cache.get(url)
            if hit:
                return hit[1]
            raise RuntimeError("circuit open for %s" % host)
        LIMIT.take()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cryptalyst/1", **self.headers})
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read().decode()
            val = json.loads(raw) if as_json else raw
            with self.lock:
                self.cache[url] = (now, val)
                self.stats["calls"] += 1
            CIRCUIT.ok(host)
            return val
        except Exception:
            self.stats["errors"] += 1
            CIRCUIT.fail(host)
            with self.lock:
                hit = self.cache.get(url)
            if hit:
                return hit[1]          # stale beats failure
            raise

    def post(self, url, payload=None, timeout=None):
        body = json.dumps(payload or {}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "cryptalyst/1", **self.headers})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read().decode())
