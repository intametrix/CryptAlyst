"""config - one place for every tunable. Design pattern: Registry + lazy load."""
import json, os, threading, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "state")
OPTIONS = os.path.join(STATE, "options.json")

DEFAULTS = {
    "override_crazy": False, "allow_counter_trend": False, "forecast_gate": True,
    "forecast_min": 0.45, "max_positions": 8, "clock_s": 3600,
    "lock_pct": 1.0, "stop_pct": 1.5, "min_expected_move": 0.20,
}


class Config:
    """Single source of tunables, reloaded at most once every 2 seconds."""

    _lock = threading.Lock()
    _cache = {"ts": 0.0, "v": dict(DEFAULTS)}

    @classmethod
    def all(cls, force=False):
        with cls._lock:
            now = time.time()
            if force or now - cls._cache["ts"] > 2.0:
                v = dict(DEFAULTS)
                try:
                    with open(OPTIONS) as fh:
                        v.update(json.load(fh) or {})
                except Exception:
                    pass
                if v.get("override_crazy"):
                    v.update({"allow_counter_trend": True, "forecast_gate": False,
                              "min_expected_move": 0.0, "max_positions": 30,
                              "clock_s": 300, "lock_pct": 0.5, "stop_pct": 1.5})
                cls._cache = {"ts": now, "v": v}
            return dict(cls._cache["v"])

    @classmethod
    def get(cls, key, default=None):
        return cls.all().get(key, default)

    @classmethod
    def set(cls, key, value):
        v = cls.all(True)
        v[key] = value
        os.makedirs(STATE, exist_ok=True)
        with open(OPTIONS + ".t", "w") as fh:
            json.dump(v, fh)
        os.replace(OPTIONS + ".t", OPTIONS)
        return cls.all(True)
