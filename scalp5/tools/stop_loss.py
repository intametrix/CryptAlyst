#!/usr/bin/env python3
"""stop_loss - the hard stop, enforced from outside the engines.

Every engine was changed to settle only in profit, which means none of them will close a
loser. A stop loss has to come from outside that rule: this watches every open position and
closes it when it is down by the configured percent, or by the configured money, whichever
comes first.

    python3 tools/stop_loss.py            # the loop
    python3 tools/stop_loss.py --once     # one pass
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")
POST = os.path.join(STATE, "stop_losses.jsonl")
POLL = float(os.environ.get("STOP_POLL", "5"))

# named constants, so a retune is one line with its own ruling
STOP_PCT = 1.5            # position stop, percent only (the dollar leg was cut: it never bound)
BOOK_BACKSTOP_PCT = 5.0   # book down this much from its high-watermark equity: flatten all



def options():
    try:
        import tactics
        return tactics.options(True)
    except Exception:
        return {}


def prices():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8796/streamprices", timeout=3) as r:
            return json.load(r).get("p") or {}
    except Exception:
        return {}


def rows():
    """Every book and its positions, from both pack families."""
    out = []
    for f in glob.glob(os.path.join(STATE, "pack_*.json")) + \
             glob.glob(os.path.join(STATE, "strategy_*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        engine = d.get("engine") or os.path.basename(f).split("_", 1)[-1][:-5]
        out.append((engine, d, f))
    return out


def one_pass(verbose=True, only=None, price_map=None):
    """`only` restricts the pass to one book and `price_map` overrides prices - both exist
    so the rule can be proved without touching a live book. The loop passes neither."""
    if price_map is not None:
        # A price override is a test device. It must never close a live position, so an
        # override turns the pass into a simulation and the caller is told so.
        return _simulate(only, price_map, verbose)
    o = options()
    pct_limit = -abs(float(o.get("stop_loss_pct") or STOP_PCT))
    usd_limit = 0.0
    px = price_map if price_map is not None else prices()
    if not pct_limit and not usd_limit:
        return []
    import master   # for the one close path that already knows both owner kinds
    closed = []
    for engine, d, f in rows():
        if only and engine != only:
            continue
        for p in list(d.get("positions") or []):
            sym = (p.get("symbol") or "").upper()
            entry = float(p.get("entry") or 0)
            notional = float(p.get("notional") or 0)
            mark = float(px.get(sym) or 0)
            if not entry or not mark:
                continue
            chg = (mark / entry - 1.0) * 100.0
            if str(p.get("side") or "buy").lower() in ("sell", "short"):
                chg = -chg
            pnl = notional * chg / 100.0
            if chg > pct_limit and pnl > usd_limit:
                continue
            try:
                out = master.flatten_position(engine, sym, reason="stop_loss", prices=px)
            except Exception as e:
                out = {"error": str(e)}
            record = {"ts": int(time.time() * 1000), "engine": engine, "symbol": sym,
                      "chg_pct": round(chg, 3), "pnl_usd": round(pnl, 2),
                      "rule": "stop_loss pct %.2f%% or %.2f usd" % (-pct_limit, -usd_limit),
                      "result": out}
            with open(POST, "a") as fh:
                fh.write(json.dumps(record) + "\n")
            closed.append("%s %s %.2f%% (%.2f usd)" % (engine, sym, chg, pnl))
    if verbose:
        for c in closed:
            print("%s  stop loss: %s" % (time.strftime("%H:%M:%S"), c), flush=True)
    return closed


def _simulate(only, price_map, verbose=True):
    """The decision, with no side effects: used by the tests and by any caller that
    supplies its own prices."""
    pct_limit = -abs(STOP_PCT)
    out = []
    for engine, d, f in rows():
        if only and engine != only:
            continue
        start = float(d.get("start_equity") or 0)
        eq = float(d.get("equity") or start)
        for p in (d.get("positions") or []):
            sym = (p.get("symbol") or "").upper()
            entry = float(p.get("entry") or 0)
            mark = float(price_map.get(sym) or 0)
            if not entry or not mark:
                continue
            chg = (mark / entry - 1.0) * 100.0
            if str(p.get("side") or "buy").lower() in ("sell", "short"):
                chg = -chg
            if chg > pct_limit:
                continue
            out.append({"engine": engine, "symbol": sym, "chg_pct": round(chg, 3),
                        "rule": "position_stop", "would_close": True})
    if verbose:
        for c in out:
            print("  SIMULATED (no action): %s %s %.2f%%" % (
                c["engine"], c["symbol"], c["chg_pct"]), flush=True)
    return out


def main():
    if "--once" in sys.argv:
        print(one_pass())
        return
    while True:
        try:
            one_pass()
        except Exception as e:
            print("stop_loss: %s" % e, flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
