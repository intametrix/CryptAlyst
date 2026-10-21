#!/usr/bin/env python3
"""Regression test: grid.py evaluates and writes on a clock, not per message.

The loop runs on every bookTicker message across up to 300 symbols, hundreds of
times a second. Before this it evaluated all grids and rewrote the pack on each
message (measured 0.275 ms write + 0.017 ms evaluate per message), which burned
most of a core. This test drives the production gate and the production evaluate
function - not a copy - with an injected clock and synthetic books.

    python3 tools/test_grid_throttle.py      # exit 0 on pass
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import grid as G


def fresh(levels=6, spacing_pct=0.25):
    G.BOOK.clear()
    G.GRIDS.clear()
    G.TRADES.clear()
    G.EQ = 5000.0
    G.START = 5000.0
    G.BOOK["AAAUSDT"] = {"bid": 100.0, "ask": 100.01}
    G.GRIDS["AAAUSDT"] = G.build_grid("AAAUSDT", 100.0, spacing_pct, levels, 200.0)
    return types.SimpleNamespace(levels=levels)


def main():
    checks = []
    # A3: tests never write live state. emit.fill would append synthetic fills to
    # state/fills.jsonl (the live ledger). Replace it with a recorder so the fill
    # path is still exercised but the write is captured, never persisted.
    emitted = []

    class _FakeEmit:
        def fill(self, engine, trade):
            emitted.append((engine, trade.get("symbol")))

    G.emit = _FakeEmit()

    # Gate: due on first call, then only once per interval
    g = G.Gate(1.0)
    checks.append(("gate first call", g.due(10.0), True))
    checks.append(("gate 0.5s later", g.due(10.5), False))
    checks.append(("gate 0.99s later", g.due(10.99), False))
    checks.append(("gate 1.0s later", g.due(11.0), True))
    checks.append(("gate 1.5s later", g.due(11.5), False))

    # should_write: a fill always writes, otherwise the gate decides
    g2 = G.Gate(1.0)
    checks.append(("write on first tick", G.should_write(g2, 0.0), True))
    checks.append(("no write until due", G.should_write(g2, 0.5), False))
    checks.append(("a fill writes now", G.should_write(g2, 0.6, fills=1), True))
    checks.append(("still quiet after fill", G.should_write(g2, 0.7), False))

    # the real shape: 1000 messages in one second at write-every 1.0 -> one write
    g3 = G.Gate(1.0)
    writes = 0
    for i in range(1000):
        if G.should_write(g3, i * 0.001):
            writes += 1
    checks.append(("1000 messages, 1 pack write", writes, 1))

    # ten seconds of stream at 1000 msg/s at the shipped defaults -> ten writes
    g4 = G.Gate(1.0)
    writes = 0
    for i in range(10000):
        if G.should_write(g4, i * 0.001):
            writes += 1
    checks.append(("10000 messages, 10 pack writes", writes, 10))

    # evaluate: a buy level fills when the ask trades down to it
    fresh()
    G.BOOK["AAAUSDT"] = {"bid": 99.70, "ask": 99.74}   # below the first buy level
    n = G.evaluate(1000.0, types.SimpleNamespace(levels=6))
    filled = [lv for lv in G.GRIDS["AAAUSDT"]["levels"] if lv["pos"]]
    checks.append(("dip fills a buy level", len(filled), 1))
    checks.append(("dip realises nothing", n, 0))
    checks.append(("no trades on a fill-only pass", len(G.TRADES), 0))

    # evaluate: price back up through the neighbouring level closes it for profit
    G.BOOK["AAAUSDT"] = {"bid": 101.0, "ask": 101.01}
    n = G.evaluate(1060.0, types.SimpleNamespace(levels=6))
    checks.append(("recovery closes one", n, 1))
    checks.append(("one trade recorded", len(G.TRADES), 1))
    checks.append(("grid trade reason", G.TRADES[0]["reason"], "grid"))
    checks.append(("grid trade is green", G.TRADES[0]["net_pct"] > 0, True))
    checks.append(("equity grew", G.EQ > 5000.0, True))
    checks.append(("level is free again",
                   G.GRIDS["AAAUSDT"]["levels"][0]["pos"], None))
    # the fill was emitted to the fake recorder, never to the live ledger
    checks.append(("fill emitted to the fake, not live", emitted, [("grid", "AAAUSDT")]))

    # evaluate never writes the pack itself: writing is the caller's decision
    hits = {"n": 0}
    G.write_book = lambda: hits.__setitem__("n", hits["n"] + 1)
    fresh()
    G.BOOK["AAAUSDT"] = {"bid": 101.0, "ask": 101.01}
    G.evaluate(1060.0, types.SimpleNamespace(levels=6))
    checks.append(("evaluate writes nothing", hits["n"], 0))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
