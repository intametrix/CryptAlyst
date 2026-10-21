"""equity - per-book capital allocation read from capital.json.

The supervisor recomputes state/capital.json (one row per book with an
`allocated` figure). Books read their own allocation here instead of hardcoding
a start, so freed equity from closed books actually lands on the kept ones.
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = os.path.join(HERE, "state", "capital.json")


def for_book(engine, default=5000.0):
    """Return the book's allocated equity, else default."""
    try:
        with open(CAP) as fh:
            d = json.load(fh)
        row = d.get(engine)
        if row:
            alloc = float(row.get("allocated") or 0.0)
            if alloc > 0:
                return alloc
            start = float(row.get("start_equity") or row.get("start") or 0.0)
            if start > 0:
                return start
    except Exception:
        pass
    return default
