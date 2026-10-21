#!/usr/bin/env python3
"""Regression test: the seven tactics functions e1a574e deleted and we restored.

They were removed by the envelope refactor while five call sites kept calling
them, so executor and crypto_pack crash-looped on AttributeError. This test pins
the semantics the call sites expect, with no network, no clock and no live book:
ext_signals is stubbed and TA_LIVE is set directly.

    python3 tools/test_tactics_restored.py      # 25 checks, exit 0 on pass
"""
import importlib
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tactics as T

importlib.reload(T)

SIG = {"AAA": {"score": -50.0}, "BBB": {"score": 40.0}, "CCC": {}}
T.ext_signals = lambda sym=None: SIG if sym is None else SIG.get(sym)
# ta_vote now reads the shared state/ta_live.json file, not in-process TA_LIVE.
_ta_dir = tempfile.mkdtemp()
T._TA_FILE = os.path.join(_ta_dir, "ta_live.json")
T._TA_READ_TTL_S = 0.0
T._ta_cache = {"ts": 0.0, "symbols": None}
T._ta_stale = False
T._ta_fresh = True
with open(T._TA_FILE + ".t", "w") as _fh:
    json.dump({"ts_ms": int(time.time() * 1000),
               "symbols": {"AAA": [{"side": "buy"}] * 2 + [{"side": "sell"}] * 3}}, _fh)
os.replace(T._TA_FILE + ".t", T._TA_FILE)


def main():
    checks = [
        # ext_gate: the extended signal set vetoes a trade that fights it
        ("ext_gate buy vs -50", T.ext_gate("AAA", "buy"), (False, -50.0)),
        ("ext_gate sell vs -50", T.ext_gate("AAA", "sell"), (True, -50.0)),
        ("ext_gate buy vs +40", T.ext_gate("BBB", "buy"), (True, 40.0)),
        ("ext_gate sell vs +40", T.ext_gate("BBB", "sell"), (False, 40.0)),
        ("ext_gate unknown sym", T.ext_gate("ZZZ", "buy"), (True, 0.0)),
        ("ext_gate empty row", T.ext_gate("CCC", "buy"), (True, 0.0)),
        # ta_vote: (for, against) over the live TA stream
        ("ta_vote", T.ta_vote("AAA", "buy"), (2, 3)),
        ("ta_vote unknown", T.ta_vote("ZZZ", "buy"), (0, 0)),
        # consensus_dead: blend AND a majority of agents, never one dip
        ("consensus_dead none", T.consensus_dead(None), (False, 0)),
        ("consensus_dead dead", T.consensus_dead({"prob": 0.38, "views": [{"prob": 0.2}] * 3}), (True, 3)),
        ("consensus_dead alive", T.consensus_dead({"prob": 0.55, "views": [{"prob": 0.2}] * 3}), (False, 3)),
        ("consensus_dead two against", T.consensus_dead({"prob": 0.38, "views": [{"prob": 0.2}] * 2}), (False, 2)),
        # risk_for: conviction sizing, capped at four confirmations
        ("risk_for base 1 conf 0", T.risk_for(1.0, 0), 0.6),
        ("risk_for base 1 conf 2", T.risk_for(1.0, 2), 1.0),
        ("risk_for base 1 conf 9", T.risk_for(1.0, 9), 1.4),
        # exit_reason: winners skim, losers get room until the hard stop
        ("exit lock", T.exit_reason(2.0, 2.0, 1.0, 0.5, 0.15, 0.45, 10, 1800), "lock"),
        ("exit skim", T.exit_reason(0.7, 0.7, 1.0, 0.5, 0.15, 0.45, 10, 1800, skim=0.6, skim_after=2.5), "skim"),
        ("exit no skim yet", T.exit_reason(0.7, 0.7, 1.0, 0.5, 0.15, 0.45, 1, 1800, skim=0.6, skim_after=2.5), None),
        ("exit trail", T.exit_reason(0.8, 1.0, 2.0, 0.5, 0.15, 0.45, 10, 1800), "trail"),
        ("exit hard_stop", T.exit_reason(-1.6, 0.0, 1.0, 0.5, 0.15, 0.45, 10, 1800, hard_stop=1.5), "hard_stop"),
        ("exit stop (no hard_stop)", T.exit_reason(-0.5, 0.0, 1.0, 0.5, 0.15, 0.45, 10, 1800), "stop"),
        ("exit clock", T.exit_reason(0.0, 0.0, 1.0, 0.5, 0.15, 0.45, 1900, 1800), "clock"),
        ("exit none", T.exit_reason(0.1, 0.1, 1.0, 0.5, 0.15, 0.45, 10, 1800), None),
        ("exit signal_flip", T.exit_reason(-0.2, 0.0, 1.0, 0.5, 0.15, 0.45, 10, 1800, opposing=True), "signal_flip"),
        # grade/post never raise when the supervisor is absent
        ("post to nothing", T.post("http://127.0.0.1:1/x", {}, timeout=0.2), False),
        ("grade with no views", T.grade(None, True), None),
    ]
    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
