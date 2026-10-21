#!/usr/bin/env python3
"""stop_loss - the hard stop and the book backstop, enforced outside the engines.

Every engine settles only in profit, so the risk plane owns the losses:

  position stop   chg <= -STOP_PCT (1.5%)            close that position
  book backstop   equity <= peak * (1 - 5%)          flatten the book, disable it,
                                                     write the post-mortem

One predicate, used by both the live pass and the simulator, so the rule that is tested is
the rule that runs.
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
PEAKS = os.path.join(STATE, "equity_peaks.json")
POLL = float(os.environ.get("STOP_POLL", "5"))

# named constants: a retune is one line with its own ruling
STOP_PCT = 1.5
BOOK_BACKSTOP_PCT = 5.0

_PRICE_FAILS = {"n": 0}


def options():
    try:
        import tactics
        return tactics.options(True)
    except Exception:
        return {}


def stop_pct():
    """The one source of the threshold: options, falling back to the constant."""
    try:
        v = float(options().get("stop_loss_pct") or STOP_PCT)
    except Exception:
        v = STOP_PCT
    return abs(v) or STOP_PCT


def breaches(chg_pct):
    """THE predicate. A position is stopped when it is at or past the stop.

    The dollar leg was cut by ruling: it never bound, and a zero-dollar leg turned the
    stop into a 'close anything red' rule that flattened hundreds of positions at a loss.
    """
    return float(chg_pct) <= -stop_pct()


def prices():
    """Last trade per symbol. A failed feed is counted and reported, never silent."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8796/streamprices", timeout=3) as r:
            px = json.load(r).get("p") or {}
        if px:
            if _PRICE_FAILS["n"] >= 3:
                print("price feed recovered after %d failures" % _PRICE_FAILS["n"], flush=True)
            _PRICE_FAILS["n"] = 0
            return px
        raise RuntimeError("empty price map")
    except Exception as e:
        _PRICE_FAILS["n"] += 1
        n = _PRICE_FAILS["n"]
        if n == 1:
            print("STOP LOSS BLIND: price feed unavailable (%s) - no stops can fire" % e,
                  flush=True)
        if n == 3:
            print("STOP LOSS BLIND for %d polls - alerting" % n, flush=True)
            try:
                import subprocess
                subprocess.run(["python3", os.path.join(HERE, "lib", "bus.py"),
                                "alert", "stop_loss blind: price feed down"], check=False)
            except Exception:
                pass
        return {}


def rows():
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


def load_peaks():
    try:
        return json.load(open(PEAKS))
    except Exception:
        return {}


def save_peaks(peaks):
    tmp = PEAKS + ".t"
    with open(tmp, "w") as fh:
        json.dump(peaks, fh, sort_keys=True)
    os.replace(tmp, PEAKS)


def record(entry):
    with open(POST, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def one_pass(verbose=True):
    px = prices()
    if not px:
        return []
    import master
    peaks = load_peaks()
    closed, backstopped = [], []
    disabled = set()
    try:
        disabled = {str(b).replace("_", "").lower()
                    for b in json.load(open(os.path.join(STATE, "disabled.json"))).get("books") or []}
    except Exception:
        pass

    for engine, d, f in rows():
        start = float(d.get("start_equity") or 0)
        eq = float(d.get("equity") or start)
        key = engine.replace("_", "").lower()

        # book backstop first: it overrides the position stop
        peak = max(float(peaks.get(engine) or 0), eq)
        peaks[engine] = peak
        if peak > 0 and eq <= peak * (1 - BOOK_BACKSTOP_PCT / 100.0):
            for p in list(d.get("positions") or []):
                try:
                    master.flatten_position(engine, p.get("symbol"), reason="book_backstop", prices=px)
                except Exception as e:
                    closed.append("%s %s backstop error %s" % (engine, p.get("symbol"), e))
            if key not in disabled:
                try:
                    books = json.load(open(os.path.join(STATE, "disabled.json"))).get("books") or []
                    if engine not in books:
                        books.append(engine)
                    json.dump({"books": books, "ts": int(time.time() * 1000)},
                              open(os.path.join(STATE, "disabled.json"), "w"))
                except Exception:
                    pass
            with open(os.path.join(STATE, "postmortems.jsonl"), "a") as fh:
                fh.write(json.dumps({
                    "ts": int(time.time() * 1000), "engine": engine, "action": "disabled",
                    "by": "stop_loss/backstop",
                    "rule": "equity %.2f <= peak %.2f - %.1f%%" % (eq, peak, BOOK_BACKSTOP_PCT),
                    "equity": eq, "peak_equity": peak,
                    "positions": len(d.get("positions") or []),
                }) + "\n")
            backstopped.append("%s book down %.1f%% from peak %.2f" % (
                engine, 100.0 * (eq / peak - 1), peak))
            continue

        for p in list(d.get("positions") or []):
            sym = (p.get("symbol") or "").upper()
            entry = float(p.get("entry") or 0)
            mark = float(px.get(sym) or 0)
            if not entry or not mark:
                continue
            chg = (mark / entry - 1.0) * 100.0
            if str(p.get("side") or "buy").lower() in ("sell", "short"):
                chg = -chg
            if not breaches(chg):
                continue
            try:
                out = master.flatten_position(engine, sym, reason="stop_loss", prices=px)
            except Exception as e:
                out = {"error": str(e)}
            record({"ts": int(time.time() * 1000), "engine": engine, "symbol": sym,
                    "chg_pct": round(chg, 3), "rule": "position stop %.2f%%" % stop_pct(),
                    "result": out})
            closed.append("%s %s %.2f%%" % (engine, sym, chg))

    save_peaks(peaks)
    if verbose:
        for c in closed:
            print("%s  stop loss: %s" % (time.strftime("%H:%M:%S"), c), flush=True)
        for b in backstopped:
            print("%s  BACKSTOP: %s" % (time.strftime("%H:%M:%S"), b), flush=True)
    return closed + backstopped


def _simulate(only=None, price_map=None, verbose=True):
    """Same predicate, no side effects: what the live pass would do."""
    out = []
    for engine, d, f in rows():
        if only and engine != only:
            continue
        for p in (d.get("positions") or []):
            sym = (p.get("symbol") or "").upper()
            entry = float(p.get("entry") or 0)
            mark = float((price_map or {}).get(sym) or 0)
            if not entry or not mark:
                continue
            chg = (mark / entry - 1.0) * 100.0
            if str(p.get("side") or "buy").lower() in ("sell", "short"):
                chg = -chg
            if breaches(chg):
                out.append({"engine": engine, "symbol": sym, "chg_pct": round(chg, 3),
                            "would_close": True})
    if verbose:
        for c in out:
            print("  SIMULATED (no action): %s %s %.2f%%" % (
                c["engine"], c["symbol"], c["chg_pct"]), flush=True)
    return out


def main():
    if "--simulate" in sys.argv:
        _simulate()
        return
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
