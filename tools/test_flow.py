"""Offline validation of the flow engine and onset gates.

Builds synthetic 1-second ticker streams with a known baseline and a known
burst, then asserts the detector fires (or stays quiet) as it should. This
catches sign errors and window arithmetic bugs that a live smoke test would
only show by accident.

Run:  python tools/test_flow.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pumpdump.config import Config
from pumpdump.flow import FlowEngine, Tick
from pumpdump.onset import build_onset_event

BASE_TS = 1_800_000_000_000  # arbitrary fixed epoch
FAILURES: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def make_engine(**overrides) -> FlowEngine:
    cfg = Config()
    cfg.min_trades_10s = 5
    params = {
        "window_sec": cfg.flow_window_sec,
        "min_burst_multiple": cfg.min_burst_multiple,
        "min_burst_z": cfg.min_burst_z,
        "min_abs_flow": cfg.min_abs_flow_usd,
        "min_move_pct_10s": cfg.min_move_pct_10s,
        "min_sigma_multiple": cfg.min_sigma_multiple,
        "min_coverage": cfg.min_coverage,
        "max_spread_pct": cfg.max_spread_pct,
        "min_book_depth_usd": cfg.min_book_depth_usd,
        "max_late_pct_60s": cfg.max_move_pct_60s,
        "require_acceleration": cfg.require_acceleration,
        "max_24h_move_pct": cfg.max_24h_move_pct,
        "min_trades_10s": cfg.min_trades_10s,
    }
    params.update(overrides)
    return FlowEngine(**params)


#: Per-(engine, symbol) feed state: (next_second, cumulative_quote, cumulative_trades).
#: A real 24h counter is monotonic and the clock only moves forward, so the
#: harness carries both across successive calls automatically. Restarting
#: either one would synthesise data the exchange could never actually send.
_FEED_STATE: dict = {}


def feed(
    engine: FlowEngine,
    symbol: str,
    *,
    seconds: int,
    base_flow_per_sec: float,
    price_fn,
    burst_from: int | None = None,
    burst_multiple: float = 1.0,
    price: float = 1.0,
    start_price: float = 1.0,
    start_second: int | None = None,
) -> tuple:
    """Push ``seconds`` of 1s ticks. Returns (cumulative quote, cumulative trades, last price).

    The clock and cumulative counters continue automatically across calls for
    the same ``(engine, symbol)`` pair.
    """
    key = (id(engine), symbol)
    clock, quote, trades = _FEED_STATE.get(key, (0, 0.0, 0))
    if start_second is not None:
        clock = start_second
    last_price = start_price
    for second in range(seconds):
        ts = BASE_TS + (clock + second) * 1000
        flow = base_flow_per_sec
        if burst_from is not None and second >= burst_from:
            flow = base_flow_per_sec * burst_multiple
        quote += flow
        trades += max(1, int(flow / 250.0))
        last_price = price_fn(second, start_price, price)
        engine.ingest_ticker(
            {
                "e": "24hrTicker",
                "E": ts,
                "s": symbol,
                "c": f"{last_price:.10f}",
                "o": f"{start_price:.10f}",
                "h": f"{max(last_price, start_price):.10f}",
                "l": f"{min(last_price, start_price):.10f}",
                "v": f"{quote / max(last_price, 1e-9):.6f}",
                "q": f"{quote:.6f}",
                "n": str(trades),
                "P": f"{(last_price / start_price - 1) * 100:.3f}",
                "b": f"{last_price * 0.9999:.10f}",
                "a": f"{last_price * 1.0001:.10f}",
                "B": "5000",
                "A": "5000",
            }
        )
    _FEED_STATE[key] = (clock + seconds, quote, trades)
    return quote, trades, last_price


def test_quiet_market() -> None:
    print("\n[1] quiet market with normal noise must not fire")
    engine = make_engine()
    # 400s of steady $200/s flow, price wobbling +/-0.05%
    def price_fn(second, start, _):
        return start * (1.0 + (0.0005 if second % 2 else -0.0005))

    feed(
        engine, "QUIETUSDT",
        seconds=400, base_flow_per_sec=200.0, price_fn=price_fn,
    )
    state = engine.states["QUIETUSDT"]
    check("baseline is ready", state.baseline.ready,
          f"{state.baseline.count} samples, median={state.baseline.med:,.0f}")
    candidates = engine.detect(now=BASE_TS + 400_000)
    quiet = [c for c in candidates if c.symbol == "QUIETUSDT"]
    check("no candidate from noise alone", not quiet,
          f"got {len(quiet)} candidate(s)")


def test_pump_onset() -> None:
    print("\n[2] a genuine pump onset must fire, with the right direction")
    engine = make_engine()
    def quiet_price(second, start, _):
        return start * (1.0 + (0.0003 if second % 2 else -0.0003))

    # 400s quiet at $200/s ...
    feed(engine, "PUMPUSDT", seconds=400, base_flow_per_sec=200.0, price_fn=quiet_price)

    # ... then 10s of 40x flow and a +1.2% ramp.
    def pump_price(second, start, _):
        return start * (1.0 + (second + 1) * 0.0012)

    feed(
        engine, "PUMPUSDT",
        seconds=10, base_flow_per_sec=200.0, price_fn=pump_price,
        burst_from=0, burst_multiple=40.0, start_second=400,
    )
    now = BASE_TS + 409_000 + 1000
    candidates = engine.detect(now=now)
    found = [c for c in candidates if c.symbol == "PUMPUSDT"]
    check("pump candidate found", len(found) == 1, f"got {len(found)}")
    if not found:
        state = engine.states["PUMPUSDT"]
        flow, cov = state.flow_quote(10.0, now)
        print(
            f"      debug: flow={flow:,.0f} cov={cov:.2f} "
            f"median={state.baseline.med:,.0f} ref={state.baseline.reference:,.0f} "
            f"mult={state.baseline.multiple(flow):.2f} z={state.baseline.zscore(flow):.2f} "
            f"change={state.change_over(10.0, now)}"
        )
        return

    candidate = found[0]
    check("direction is up", candidate.direction == 1, f"direction={candidate.direction}")
    check("burst multiple clears the gate",
          candidate.burst_multiple >= engine.min_burst_multiple,
          f"{candidate.burst_multiple:.1f}x")
    check("flow is in the expected range",
          candidate.flow_quote > 50_000,
          f"${candidate.flow_quote:,.0f}")
    check("move is positive", (candidate.change_10s or 0) > 0,
          f"{(candidate.change_10s or 0) * 100:.3f}%")

    event = build_onset_event(candidate, Config())
    check("event builds and is a PUMP", event is not None and event.kind == "PUMP",
          f"score={event.score:.0f} severity={event.severity}" if event else "no event")
    if event:
        check("score is in range", 0 <= event.score <= 100, f"{event.score:.0f}")
        check("reasons were produced", len(event.reasons) >= 3,
              f"{len(event.reasons)} reasons")


def test_dump_onset() -> None:
    print("\n[3] a dump onset must fire in the opposite direction")
    engine = make_engine()
    def quiet_price(second, start, _):
        return start * (1.0 + (0.0003 if second % 2 else -0.0003))

    feed(engine, "DUMPUSDT", seconds=400, base_flow_per_sec=300.0, price_fn=quiet_price)

    def dump_price(second, start, _):
        return start * (1.0 - (second + 1) * 0.0013)

    feed(
        engine, "DUMPUSDT",
        seconds=10, base_flow_per_sec=300.0, price_fn=dump_price,
        burst_from=0, burst_multiple=35.0, start_second=400,
    )
    now = BASE_TS + 409_000 + 1000
    found = [c for c in engine.detect(now=now) if c.symbol == "DUMPUSDT"]
    check("dump candidate found", len(found) == 1, f"got {len(found)}")
    if found:
        check("direction is down", found[0].direction == -1,
              f"direction={found[0].direction}")
        event = build_onset_event(found[0], Config())
        check("event is a DUMP", event is not None and event.kind == "DUMP",
              f"kind={event.kind if event else None} score={event.score:.0f}" if event else "")


def test_illiquid_is_rejected() -> None:
    print("\n[4] a thin, illiquid burst must be rejected by the absolute floor")
    engine = make_engine()
    def price_fn(second, start, _):
        return start

    # $4/s baseline, 100x burst = $4,000 per 10s -- a huge multiple off a
    # near-zero divisor, but far too little money to be a real pump.
    feed(engine, "THINUSDT", seconds=400, base_flow_per_sec=4.0, price_fn=price_fn)

    def pump_price(second, start, _):
        return start * (1.0 + (second + 1) * 0.003)

    feed(
        engine, "THINUSDT",
        seconds=10, base_flow_per_sec=4.0, price_fn=pump_price,
        burst_from=0, burst_multiple=100.0, start_second=400,
    )
    now = BASE_TS + 409_000 + 1000
    state = engine.states["THINUSDT"]
    flow, coverage = state.flow_quote(10.0, now)
    found = [c for c in engine.detect(now=now) if c.symbol == "THINUSDT"]
    check("flow really is below the absolute floor",
          flow < engine.min_abs_flow,
          f"${flow:,.0f} vs floor ${engine.min_abs_flow:,.0f} "
          f"({state.baseline.multiple(flow):.0f}x baseline)")
    check("illiquid burst rejected", not found, f"got {len(found)} candidate(s)")


def test_late_move_is_rejected() -> None:
    print("\n[5] arriving late (60s already moved) must be rejected")
    engine = make_engine()
    def price_fn(second, start, _):
        return start * (1.0 + (0.0002 if second % 2 else -0.0002))

    feed(engine, "LATEUSDT", seconds=400, base_flow_per_sec=400.0, price_fn=price_fn)

    # 60 seconds of sustained heavy flow and a big move, then a burst on top:
    # the 60s move now dwarfs the 10s move, so this is a tail, not an onset.
    def runup(second, start, _):
        return start * (1.0 + (second + 1) * 0.004)

    feed(
        engine, "LATEUSDT",
        seconds=60, base_flow_per_sec=400.0, price_fn=runup,
        burst_from=0, burst_multiple=30.0, start_second=400,
    )
    now = BASE_TS + 459_000 + 1000
    found = [c for c in engine.detect(now=now) if c.symbol == "LATEUSDT"]
    check("late move rejected", not found, f"got {len(found)} candidate(s)")
    if found:
        print(
            f"      debug: 10s={found[0].change_10s} "
            f"60s={found[0].change_60s} "
            f"accel={found[0].acceleration}"
        )


def test_coverage_gate() -> None:
    print("\n[6] a burst measured from too few ticks must be rejected")
    engine = make_engine()
    def price_fn(second, start, _):
        return start

    feed(engine, "SPARSEUSDT", seconds=400, base_flow_per_sec=200.0, price_fn=price_fn)

    # Only two ticks in the final 10 seconds -> coverage far below the gate.
    state = engine.states["SPARSEUSDT"]
    state.ticks.append(
        Tick(ts=BASE_TS + 409_000, price=1.02, quote_vol=1e9, base_vol=1e9, trades=10**6)
    )
    state.last_tick = state.ticks[-1]
    state.last_price = 1.02
    now = BASE_TS + 409_000 + 500
    found = [c for c in engine.detect(now=now) if c.symbol == "SPARSEUSDT"]
    check("sparse burst rejected by the coverage gate", not found,
          f"got {len(found)}; truncated={engine.truncated}")


def test_baseline_contamination() -> None:
    print("\n[7] a sustained pump must not permanently inflate its own baseline")
    engine = make_engine()
    def price_fn(second, start, _):
        return start

    feed(engine, "LONGUSDT", seconds=300, base_flow_per_sec=1_000.0, price_fn=price_fn)
    clean_reference = engine.states["LONGUSDT"].baseline.reference

    # 10 minutes of 50x flow. Without the contamination cap -- and, critically,
    # without anchoring the cap on a LOW quantile -- this drags the baseline up
    # by the cap multiple on every round and blinds the symbol.
    feed(
        engine, "LONGUSDT",
        seconds=600, base_flow_per_sec=1_000.0, price_fn=price_fn,
        burst_from=0, burst_multiple=50.0, start_second=300,
    )
    after = engine.states["LONGUSDT"].baseline.reference
    ratio = after / clean_reference if clean_reference else 0
    check("baseline reference does not ratchet under a sustained burst",
          ratio < 2.0,
          f"before={clean_reference:,.0f} after={after:,.0f} ratio={ratio:.2f}x")

    # And the symbol must still see a fresh 50x burst as a burst.
    live_flow = 1_000.0 * 50.0 * 10
    multiple = engine.states["LONGUSDT"].baseline.multiple(live_flow)
    check("a live 50x burst is still measured as ~50x", multiple > 25.0,
          f"{multiple:.1f}x")


def main() -> int:
    print("=" * 72)
    print("flow engine / onset gate validation")
    print("=" * 72)
    test_quiet_market()
    test_pump_onset()
    test_dump_onset()
    test_illiquid_is_rejected()
    test_late_move_is_rejected()
    test_coverage_gate()
    test_baseline_contamination()

    print("\n" + "=" * 72)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
