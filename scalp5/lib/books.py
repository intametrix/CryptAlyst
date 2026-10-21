"""books - one place that answers "what do the books hold right now".

The dashboard, the heartbeat and the session monitor each used to sum a different
set of files, so they disagreed: +22 on one, -742 on another. This module is the
single reader they all use.
"""
import glob
import json
import os

STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")

PACKS = ("pack_*.json", "strategy_*.json", "edge_*.json", "slow_*.json", "alpha_*.json")


def files():
    out = []
    for pat in PACKS:
        out.extend(glob.glob(os.path.join(STATE, pat)))
    return sorted(set(out))


PRICES = {}


def _load_prices():
    """Last trade per symbol, from the feed. Empty on failure: marks are optional."""
    global PRICES
    try:
        import json as _j, urllib.request as _u
        with _u.urlopen("http://127.0.0.1:8796/streamprices", timeout=3.0) as r:
            PRICES = _j.load(r).get("p") or {}
    except Exception:
        PRICES = {}
    return PRICES


def read_all():
    _load_prices()
    """Every book record on disk, with the fields the callers need."""
    rows = []
    for path in files():
        try:
            with open(path) as fh:
                b = json.load(fh)
        except Exception:
            continue
        equity = float(b.get("equity") or 0)
        start = float(b.get("start_equity") or 0)
        trades = b.get("trades") or []
        closed = b.get("trade_count")
        if closed is None:
            closed = len(trades)
        wins = sum(1 for t in trades if (t.get("net_pct") or 0) > 0)
        losses = sum(1 for t in trades if (t.get("net_pct") or 0) <= 0)
        # mark the open positions at the last trade, so a book's result is
        # realised + unrealised rather than realised alone
        unreal = 0.0
        for p in (b.get("positions") or []):
            entry = float(p.get("entry") or 0)
            notional = float(p.get("notional") or 0)
            px = float(PRICES.get((p.get("symbol") or "").upper()) or 0)
            if not entry or not notional or not px:
                continue
            chg = (px / entry - 1.0) * 100.0
            if (p.get("side") or "buy").lower() in ("sell", "short"):
                chg = -chg
            unreal += notional * chg / 100.0

        rows.append({
            "engine": b.get("engine") or os.path.basename(path).replace(".json", ""),
            "file": path,
            "equity": equity,
            "start": start,
            "pnl": equity - start,
            "unrealised": round(unreal, 2),
            "total": round(equity - start + unreal, 2),
            "open": len(b.get("positions") or []),
            "closed": int(closed or 0),
            "wins": wins,
            "losses": losses,
            "ts": b.get("ts"),
            "mtime": os.path.getmtime(path),
        })
    return rows


def totals(rows=None):
    rows = rows if rows is not None else read_all()
    equity = sum(r["equity"] for r in rows)
    start = sum(r["start"] for r in rows)
    return {
        "books": len(rows),
        "equity": round(equity, 2),
        "start": round(start, 2),
        "pnl": round(equity - start, 2),
        "unrealised": round(sum(r.get("unrealised", 0.0) for r in rows), 2),
        "total": round(equity - start + sum(r.get("unrealised", 0.0) for r in rows), 2),
        "pnl_pct": round(100 * (equity - start) / start, 4) if start else 0.0,
        "open": sum(r["open"] for r in rows),
        "closed": sum(r["closed"] for r in rows),
        "wins": sum(r["wins"] for r in rows),
        "losses": sum(r["losses"] for r in rows),
    }
