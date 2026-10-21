#!/usr/bin/env python3
"""Regression test: TA_LIVE shared-state (state/ta_live.json).

The structural bug: TA_LIVE lived in each process's memory, and only crypto_pack
filled it, so every other engine's ta_vote returned (0,0) forever and its
contradiction gate was inert. The fix moves the signal state to one atomic file.
This test drives the production writer and reader with an injected file path,
fake clock, and no network.

    python3 tools/test_ta_live.py      # exit 0 on pass
"""
import json
import os
import sys
import tempfile
import threading
import time as _real_time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tactics as T


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s

    strftime = staticmethod(_real_time.strftime)


def main():
    tmp = tempfile.mkdtemp()
    ta_file = os.path.join(tmp, "ta_live.json")
    clock = Clock(1000.0)
    logs = []

    T._TA_FILE = ta_file
    T.time = clock
    T._ta_log = lambda m: logs.append(m)
    T._TA_WRITE_MIN_S = 0.0
    T._TA_READ_TTL_S = 0.0
    T._TA_STALE_S = 60.0

    def reset():
        T._ta_cache = {"ts": 0.0, "symbols": None}
        T._ta_stale = False
        T._ta_fresh = True
        logs.clear()

    def write(doc):
        with open(ta_file + ".t", "w") as f:
            json.dump(doc, f)
        os.replace(ta_file + ".t", ta_file)

    checks = []

    # 1. fresh file with a symbol -> ta_vote counts for and against
    reset()
    write({"ts_ms": int(clock.t * 1000), "symbols": {"AAA": [{"side": "buy"}, {"side": "buy"}, {"side": "sell"}]}})
    checks.append(("fresh ta_vote", T.ta_vote("AAA", "buy"), (2, 1)))
    checks.append(("fresh logs nothing", list(logs), []))

    # 2. symbol missing from a fresh file -> no-vote, no log
    reset()
    write({"ts_ms": int(clock.t * 1000), "symbols": {"BBB": [{"side": "buy"}]}})
    checks.append(("missing symbol no-vote", T.ta_vote("ZZZ", "buy"), (0, 0)))
    checks.append(("missing symbol logs nothing", list(logs), []))

    # 3. stale file -> no-vote, staleness logged once, not per loop
    reset()
    write({"ts_ms": int((clock.t - 61) * 1000), "symbols": {"AAA": [{"side": "buy"}]}})
    checks.append(("stale no-vote", T.ta_vote("AAA", "buy"), (0, 0)))
    checks.append(("staleness logged", len(logs), 1))
    checks.append(("staleness says stale", logs[0].startswith("TA_LIVE STALE"), True))
    T.ta_vote("AAA", "buy")
    checks.append(("staleness not re-logged", len(logs), 1))

    # 4. file turns fresh again -> one recovery line, then quiet
    write({"ts_ms": int(clock.t * 1000), "symbols": {"AAA": [{"side": "buy"}]}})
    checks.append(("recovery vote", T.ta_vote("AAA", "buy"), (1, 0)))
    checks.append(("recovery logged once", len(logs), 2))
    checks.append(("recovery says fresh", logs[1].startswith("TA_LIVE FRESH"), True))
    T.ta_vote("AAA", "buy")
    checks.append(("recovery not re-logged", len(logs), 2))

    # 5. missing file -> no-vote
    reset()
    if os.path.exists(ta_file):
        os.remove(ta_file)
    checks.append(("missing file no-vote", T.ta_vote("AAA", "buy"), (0, 0)))

    # 6. writer -> reader sees the complete payload, never a partial one
    reset()
    for i in range(300):
        T.TA_LIVE = {"M%03d" % (i % 5): [{"side": "buy"}] * (i % 3)}
        T._write_ta_live()
        got = T._read_ta_live()
        if got != T.TA_LIVE:
            checks.append(("atomic read %d" % i, got, T.TA_LIVE))
            break
    else:
        checks.append(("atomic: reader never sees a partial", True, True))

    # 7. concurrent writer/reader race: no exceptions, only None or dict
    reset()
    T.TA_LIVE = {"RACE": [{"side": "sell"}]}
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            T.TA_LIVE = {"R%03d" % (i % 7): [{"side": "sell"}] * (i % 4)}
            T._write_ta_live()
            i += 1

    def reader():
        while not stop.is_set():
            try:
                r = T._read_ta_live()
                if r is not None and not isinstance(r, dict):
                    errors.append("non-dict: %r" % (r,))
            except Exception as e:
                errors.append("raised: %r" % (e,))

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start(); r.start()
    _real_time.sleep(0.6)
    stop.set()
    w.join(); r.join()
    checks.append(("race: no exceptions, no partial", errors, []))

    # 8. a fetch failure writes nothing: ts_ms untouched, so the file ages out
    import urllib.request as _ur
    _orig_urlopen = _ur.urlopen

    def failing_urlopen(*a, **k):
        raise OSError("ta bus down")
    _ur.urlopen = failing_urlopen
    try:
        reset()
        write({"ts_ms": int(clock.t * 1000), "symbols": {"AAA": [{"side": "buy"}]}})
        before = json.load(open(ta_file))["ts_ms"]
        out = T.ta_signals()
        checks.append(("fetch failure returns []", out, []))
        after = json.load(open(ta_file))["ts_ms"]
        checks.append(("fetch failure leaves ts_ms untouched", after, before))
        clock.t += 61
        checks.append(("aged-out file votes no-vote", T.ta_vote("AAA", "buy"), (0, 0)))
    finally:
        _ur.urlopen = _orig_urlopen

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
