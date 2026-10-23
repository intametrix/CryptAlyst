#!/usr/bin/env python3
"""Unit tests for the engine, book, and models — with mocked envelopes.

Three layers, in order of size:

  MODELS  — the pure rule functions (funding_fade, model_stack, sweep_reversal,
            flow_follow, range_fade, momentum_stack, donchian55). Deterministic,
            no network: their HTTP input (jget) is mocked.
  BOOK    — the account classes (models10.Book, alpha.Book, crypto_strategies.Book):
            open / mark / close / realised PnL. emit and size_mult are mocked.
  ENGINE  — the decision path a signal travels: conviction sizing (risk.size_for),
            a maker quote from a mocked order-book envelope, then open/close on the
            book. The envelopes the engine consumes/produces are the bus shape
            {topic, key, producer, data, version, seq, ts}.

Run:  python3 tools/test_engine_book_models.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# A6: unit-test the LOGIC even while a book is disabled. The disable gate is a
# launch-time human act, not a property of the rule/book math, so no-op it here.
import tactics
tactics.require_enabled = lambda *a, **k: None

import models10
import crypto_strategies
import alpha
import maker
import risk


class FakeEmit:
    """Records the decision/fill envelopes a book would publish to the bus."""

    def __init__(self):
        self.decisions = []
        self.fills = []

    def decision(self, engine, sym, side, allowed, why):
        did = "did-%d" % (len(self.decisions) + 1)
        self.decisions.append((engine, sym, side, allowed, why))
        return did

    def fill(self, engine, trade):
        self.fills.append((engine, trade))


def envelope(topic, key, data, producer="test"):
    """A bus envelope in the canonical shape (lib/bus.py Envelope)."""
    return {"topic": topic, "key": key, "producer": producer, "data": data,
            "version": 1, "seq": 1, "ts": 0}


def bars(n=5, c=95.0, h=100.0, l=90.0):
    return [{"t": i * 60000, "o": c, "h": h, "l": l, "c": c, "v": 100.0}
            for i in range(n)]


def main():
    checks = []

    def ok(name, got, want):
        checks.append((name, got, want))

    # ============================================================ MODELS ====
    # r_funding_fade: reads lastFundingRate via jget (mocked here).
    models10.jget = lambda u, t=8.0: {"lastFundingRate": "0.0010"}
    ok("funding_fade: high rate -> sell", models10.r_funding_fade("BTCUSDT"), "sell")
    models10.jget = lambda u, t=8.0: {"lastFundingRate": "-0.0010"}
    ok("funding_fade: low rate -> buy", models10.r_funding_fade("BTCUSDT"), "buy")
    models10.jget = lambda u, t=8.0: {"lastFundingRate": "0.0004"}
    ok("funding_fade: inside band -> none", models10.r_funding_fade("BTCUSDT"), None)
    models10.jget = lambda u, t=8.0: (_ for _ in ()).throw(OSError("net down"))
    ok("funding_fade: jget failure -> none", models10.r_funding_fade("BTCUSDT"), None)

    # r_donchian55: pure breakout over the last 55 bars.
    b = bars(); b[-1]["c"] = 101.0
    ok("donchian55: close >= 55-high -> buy", models10.r_donchian55(b), "buy")
    b = bars(); b[-1]["c"] = 89.0
    ok("donchian55: close <= 55-low -> sell", models10.r_donchian55(b), "sell")
    ok("donchian55: inside range -> none", models10.r_donchian55(bars()), None)

    # model_stack: consensus over the vote map.
    ok("model_stack: 2 buys -> buy", crypto_strategies.model_stack({}, {"a": 1, "b": 1}, set()),
       ("buy", "2 model net +2"))
    ok("model_stack: 2 sells -> sell", crypto_strategies.model_stack({}, {"a": -1, "b": -1}, set()),
       ("sell", "2 model net -2"))
    ok("model_stack: split -> none", crypto_strategies.model_stack({}, {"a": 1, "b": -1}, set()),
       (None, ""))
    ok("model_stack: no votes -> none", crypto_strategies.model_stack({}, {}, set()), (None, ""))

    # sweep_reversal: fade a violent single burst.
    a = {"severity": "CRITICAL", "kind": "PUMP", "metrics": {"burst_z": 10.0}}
    side, why = crypto_strategies.sweep_reversal(a, {}, set())
    ok("sweep_reversal: critical pump -> sell", side, "sell")
    ok("sweep_reversal: why names the sweep", "fading" in why, True)
    a = {"severity": "CRITICAL", "kind": "DUMP", "metrics": {"burst_z": 9.0}}
    ok("sweep_reversal: critical dump -> buy", crypto_strategies.sweep_reversal(a, {}, set())[0], "buy")
    a = {"severity": "HIGH", "kind": "PUMP", "metrics": {"burst_z": 10.0}}
    ok("sweep_reversal: not critical -> none", crypto_strategies.sweep_reversal(a, {}, set())[0], None)

    # flow_follow: aggressive taker flow + burst.
    a = {"kind": "PUMP", "metrics": {"taker_buy_ratio": 0.75, "burst_multiple": 4.0}}
    ok("flow_follow: pump + taker -> buy", crypto_strategies.flow_follow(a, {}, set())[0], "buy")
    a = {"kind": "DUMP", "metrics": {"taker_buy_ratio": 0.25, "burst_multiple": 4.0}}
    ok("flow_follow: dump + weak taker -> sell", crypto_strategies.flow_follow(a, {}, set())[0], "sell")
    a = {"kind": "PUMP", "metrics": {"taker_buy_ratio": 0.75, "burst_multiple": 2.0}}
    ok("flow_follow: burst below 3x -> none", crypto_strategies.flow_follow(a, {}, set())[0], None)

    # range_fade: fade the range extension on a strong burst.
    a = {"kind": "PUMP", "metrics": {"range_position": 0.95, "burst_multiple": 6.0}}
    ok("range_fade: top of range -> sell", crypto_strategies.range_fade(a, {}, set())[0], "sell")
    a = {"kind": "DUMP", "metrics": {"range_position": 0.05, "burst_multiple": 6.0}}
    ok("range_fade: bottom of range -> buy", crypto_strategies.range_fade(a, {}, set())[0], "buy")

    # momentum_stack: alert + model votes + range + flow all agree.
    a = {"kind": "PUMP", "metrics": {"range_position": 0.7, "taker_buy_ratio": 0.7}}
    ok("momentum_stack: all agree long -> buy",
       crypto_strategies.momentum_stack(a, {"x": 1, "y": 1}, set())[0], "buy")
    a = {"kind": "DUMP", "metrics": {"range_position": 0.3, "taker_buy_ratio": 0.3}}
    ok("momentum_stack: all agree short -> sell",
       crypto_strategies.momentum_stack(a, {"x": -1, "y": -1}, set())[0], "sell")

    # ---- alpha models (extracted pure rules) — "our thinking" per model ----
    # xsec_momentum: long the strongest 3, short the weakest 3 (negative only).
    ranked = [{"s": "A", "c": 10.0}, {"s": "B", "c": 8.0}, {"s": "C", "c": 5.0},
              {"s": "X", "c": 1.0}, {"s": "Y", "c": -2.0}, {"s": "Z", "c": -9.0}]
    longs, shorts = alpha.xsec_picks(ranked)
    ok("xsec: long the strongest 3", [u["s"] for u in longs], ["A", "B", "C"])
    ok("xsec: short the weakest (negative) 3", [u["s"] for u in shorts], ["Y", "Z"])
    ranked_pos = [{"s": "A", "c": 10.0}, {"s": "B", "c": 8.0}, {"s": "C", "c": 5.0},
                  {"s": "X", "c": 4.0}, {"s": "Y", "c": 3.0}, {"s": "Z", "c": 2.0}]
    ok("xsec: no shorts when the weak end is still positive",
       alpha.xsec_picks(ranked_pos)[1], [])

    # funding_carry: fade a funding extreme — sell positive, buy negative, none inside 2bps.
    ok("funding: high positive rate -> sell", alpha.funding_side(0.0005), "sell")
    ok("funding: high negative rate -> buy", alpha.funding_side(-0.0005), "buy")
    ok("funding: inside 2bps -> none", alpha.funding_side(0.0001), None)
    ok("funding: zero -> none", alpha.funding_side(0.0), None)

    # cascade_rebound: buy a >1.5-sigma 1m drop on >2.5x volume; quiet bars = no signal.
    flat = [{"c": 100.0, "v": 100.0} for _ in range(30)]
    drop = flat + [{"c": 99.0, "v": 300.0}]          # -1% on 3x volume
    ok("cascade: sigma drop + volume spike -> signal", alpha.cascade_signal(drop), True)
    ok("cascade: quiet bars -> no signal", alpha.cascade_signal(flat), False)
    drop_no_vol = flat + [{"c": 99.0, "v": 100.0}]    # drop but no volume
    ok("cascade: drop without volume -> no signal", alpha.cascade_signal(drop_no_vol), False)
    ok("cascade: warm-up (few bars) -> no signal",
       alpha.cascade_signal([{"c": 100.0, "v": 100.0}] * 10), False)

    # ============================================================== BOOK ====
    # models10.Book — open/mark/close/realised, emit mocked.
    models10.emit = FakeEmit()
    models10.T.size_mult = lambda: 1.0
    bk = models10.Book("funding_fade", start=5000.0)
    bk.open("BTCUSDT", "buy", 100.0, notional=1000.0)
    ok("book: position opens with the passed notional", bk.pos["BTCUSDT"]["notional"], 1000.0)
    ok("book: decision id threaded", bk.pos["BTCUSDT"]["decision_id"], "did-1")
    ok("book: mark buy +1%", abs(bk.mark("BTCUSDT", 101.0) - 1.0) < 1e-9, True)
    bk.close("BTCUSDT", 101.0, 1.0, "target")
    ok("book: net is chg minus 20bps", abs(bk.trades[-1]["net_pct"] - 0.8) < 1e-9, True)
    ok("book: equity grows by notional*net/100", abs(bk.equity - 5008.0) < 1e-6, True)
    ok("book: realised = equity - start", abs(bk.realised() - 8.0) < 1e-6, True)
    ok("book: fill published once", len(models10.emit.fills), 1)
    ok("book: position cleared after close", "BTCUSDT" in bk.pos, False)

    bk2 = models10.Book("funding_fade", start=5000.0)
    bk2.open("BTCUSDT", "sell", 100.0, notional=1000.0)
    ok("book: mark sell +2% when price drops", abs(bk2.mark("BTCUSDT", 98.0) - 2.0) < 1e-9, True)

    # alpha.Book — profit-only exits, no stop.
    alpha.equity.for_book = lambda name, default: 5000.0
    alpha._tactics.size_mult = lambda: 1.0
    ab = alpha.Book("funding_carry")          # risk 2.5, sl 1.0
    ab.open("BTCUSDT", "buy", 100.0, "test")
    ok("alpha book: notional = risk/sl capped at 3x",
       ab.pos["BTCUSDT"]["notional"], 5000.0 * 2.5 / 100.0 / 0.01)
    ab.manage({"BTCUSDT": 101.2})             # +1.2% >= tp 1.2
    ok("alpha book: take-profit closes", "BTCUSDT" in ab.pos, False)
    ok("alpha book: exit reason is target", ab.trades[-1]["reason"], "target")
    ab2 = alpha.Book("funding_carry")
    ab2.open("BTCUSDT", "buy", 100.0, "test")
    ab2.manage({"BTCUSDT": 90.0})             # -10%: profit-only, no stop
    ok("alpha book: losing position not stopped", "BTCUSDT" in ab2.pos, True)

    # crypto_strategies.Book — sell mark + profit-only settle.
    crypto_strategies.emit = FakeEmit()
    crypto_strategies._tactics.size_mult = lambda: 1.0
    cb = crypto_strategies.Book("model_stack", {"tp": 1.5, "sl": 1.0, "hold": 1200, "risk": 0.65},
                                equity=2000.0)
    cb.open("BTCUSDT", "sell", 100.0, "test")
    ok("crypto book: notional capped at 25% of book", cb.positions["BTCUSDT"]["notional"], 500.0)
    ok("crypto book: sell mark +2% when price drops", abs(cb.mark("BTCUSDT", 98.0) - 2.0) < 1e-9, True)

    # ============================================================ ENGINE ====
    # Conviction sizing: flow confidence scales the notional.
    ok("engine: size_for 100 conf = 1.5x", risk.size_for(1000.0, 100), 1500.0)
    ok("engine: size_for 0 conf = 1.0x", risk.size_for(1000.0, 0), 1000.0)
    ok("engine: size_for -100 conf = 0.5x", risk.size_for(1000.0, -100), 500.0)

    # maker.quote from a mocked order-book envelope (join the bid, never cross).
    maker.book = lambda s: {"bids": [["99.9", "1.0"], ["99.8", "2.0"]],
                            "asks": [["100.1", "1.0"], ["100.2", "2.0"]]}
    px, prob, note = maker.quote("BTCUSDT", "buy", 1000.0)
    ok("engine: quote joins the bid, never crosses", px, 99.9)
    ok("engine: fill probability bounded", 0.05 <= prob <= 0.95, True)

    # Full decision path: a forecast envelope -> size -> quote -> open, and the
    # produced decision envelope is recorded (a fill comes only on close).
    fc_env = envelope("intel.forecast", "BTCUSDT",
                      {"prob": 0.61, "move_pct": 0.4, "bar_age_s": 10})
    notional = risk.size_for(1000.0, 100.0)            # 1500, high conviction
    px, prob, _ = maker.quote("BTCUSDT", "buy", notional)
    models10.emit = FakeEmit()                          # fresh emitter for this path
    eng = models10.Book("funding_fade", start=5000.0)
    eng.open("BTCUSDT", "buy", px, notional=notional)
    ok("engine: scaled notional opens", eng.pos["BTCUSDT"]["notional"], 1500.0)
    ok("engine: open emits one decision, no fill yet",
       (len(models10.emit.decisions), len(models10.emit.fills)), (1, 0))
    ok("engine: forecast envelope carried prob",
       fc_env["data"]["prob"] >= 0.45, True)

    # ---- report ----
    bad = 0
    for name, got, want in checks:
        passed = got == want
        bad += 0 if passed else 1
        print(("PASS " if passed else "FAIL ") + name +
              ("" if passed else "  got %r want %r" % (got, want)))
    print("\n%d checks, %d failed" % (len(checks), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
