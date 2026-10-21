#!/usr/bin/env python3
"""Regression test: decision_id/fill_id instrumentation in lib/emit.py.

Pins the decision->fill->ledger chain: a decision gets an id and a durable
ledger row, a fill references its parent decision_id, and export_decisions()
flags orphans (fill naming a decision that never existed) vs untraced (legacy
fill with no decision_id). No network, no live bus, no live state — ledgers are
redirected to a temp dir and the bus is disabled.

    python3 tools/test_decision_id.py      # exit 0 on pass
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import emit


def main():
    checks = []

    with tempfile.TemporaryDirectory() as td:
        emit.STATE = td
        emit.FILLS = os.path.join(td, "fills.jsonl")
        emit.DECISIONS = os.path.join(td, "decisions.jsonl")
        emit.bus = None  # deterministic: no broker

        # decision returns an id and writes a ledger row
        did = emit.decision("grid", "BTCUSDT", "buy", True, "test", 0.7)
        checks.append(("decision id prefixed", did.startswith("d-"), True))

        # a fill referencing the decision carries both ids and the parent link
        rec = emit.fill("grid", {"symbol": "BTCUSDT", "side": "buy",
                                 "entry": 100.0, "exit": 100.8,
                                 "net_pct": 0.8, "decision_id": did})
        checks.append(("fill links decision", rec.get("decision_id") == did, True))
        checks.append(("fill id prefixed", rec.get("fill_id", "").startswith("f-"), True))

        # an orphan fill (names a decision that never existed)
        emit.fill("grid", {"symbol": "ETHUSDT", "decision_id": "d-bogus"})
        # a legacy fill (no decision_id)
        emit.fill("grid", {"symbol": "SOLUSDT"})

        e = emit.export_decisions()
        checks.append(("decisions counted", e["decisions"] == 1, True))
        checks.append(("matched", e["matched"] == 1, True))
        checks.append(("orphan flagged", e["orphans"] == 1, True))
        checks.append(("legacy untraced", e["untraced"] == 1, True))
        checks.append(("fills counted", e["fills"] == 3, True))

    # ids are unique
    ids = {emit.new_id("x") for _ in range(1000)}
    checks.append(("1000 ids unique", len(ids) == 1000, True))

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
