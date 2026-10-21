"""universe - the ceiling on symbols a model may choose from, in one place.

Every model may select up to MAX_SYMBOLS symbols. A model that asks for more is
capped here rather than silently getting whatever it asked for, and a model that
asks for fewer keeps its own number: this is a ceiling, not a floor.
"""
import json
import os

MAX_SYMBOLS = 300
_CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "state", "universe.json")


def ceiling():
    """The configured ceiling, so it can be raised without touching every engine."""
    try:
        with open(_CFG) as fh:
            return min(int(json.load(fh).get("max_symbols", MAX_SYMBOLS)), MAX_SYMBOLS)
    except Exception:
        return MAX_SYMBOLS


def cap(n, default=None):
    """What a model actually gets: min(requested, ceiling)."""
    want = int(n) if n else int(default or MAX_SYMBOLS)
    return max(1, min(want, ceiling()))


def select(symbols, n):
    """Take up to n symbols from a ranked list, preserving the ranking."""
    return list(symbols)[:cap(n)]
