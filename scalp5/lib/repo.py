"""repo - the only reader of book state. Design pattern: Repository + Snapshot."""
import glob, json, os, threading, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")


class BookRepo:
    """Reads every engine's book once per second and hands out snapshots."""

    def __init__(self, ttl=1.0):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.ts = 0.0
        self.books = []

    def all(self, force=False):
        with self.lock:
            now = time.time()
            if not force and now - self.ts < self.ttl and self.books:
                return self.books
            books = []
            for path in sorted(glob.glob(os.path.join(STATE, "pack_*.json")) +
                               glob.glob(os.path.join(STATE, "alpha_*.json")) +
                               glob.glob(os.path.join(STATE, "slow_*.json"))):
                try:
                    with open(path) as fh:
                        books.append(json.load(fh))
                except Exception:
                    continue
            self.books, self.ts = books, now
            return books

    def summary(self):
        books = self.all()
        equity = sum(b.get("equity") or 0 for b in books)
        start = sum(b.get("start_equity") or 0 for b in books) or 1
        return {"count": len(books), "equity": equity, "start_equity": start,
                "return_pct": 100 * (equity / start - 1),
                "open": sum(len(b.get("positions") or []) for b in books),
                "closed": sum(b.get("trade_count") or 0 for b in books)}

    def get(self, key):
        for b in self.all():
            if b.get("engine") == key:
                return b
        return None
