"""Debug: replicate the synthetic pump and print each gate's verdict."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_flow import BASE_TS, feed, make_engine

engine = make_engine()
state = engine.ensure("PUMPUSDT")

print("engine gates:", {
    "min_burst_multiple": engine.min_burst_multiple,
    "min_burst_z": engine.min_burst_z,
    "min_abs_flow": engine.min_abs_flow,
    "min_move_pct": engine.min_move_pct_10s,
    "min_sigma_multiple": engine.min_sigma_multiple,
    "min_coverage": engine.min_coverage,
    "require_acceleration": engine.require_acceleration,
    "min_trades": engine.min_trades_10s,
    "max_spread": engine.max_spread_pct,
    "min_depth": engine.min_book_depth_usd,
    "max_late_60s": engine.max_late_pct_60s,
    "max_24h": engine.max_24h_move_pct,
})


def quiet(second, start, _):
    return start * (1.0 + (0.0003 if second % 2 else -0.0003))


def pump(second, start, _):
    return start * (1.0 + (second + 1) * 0.0012)


feed(engine, "PUMPUSDT", seconds=400, base_flow_per_sec=200.0, price_fn=quiet)
feed(engine, "PUMPUSDT", seconds=10, base_flow_per_sec=200.0, price_fn=pump,
     burst_from=0, burst_multiple=40.0, start_second=400)

now = BASE_TS + 410_000
print("\nlast_tick.ts =", state.last_tick.ts, " (now-15000 =", now - 15_000, ")")
print("baseline ready:", state.baseline.ready, "count:", state.baseline.count)
print("tracked:", engine.tracked)

flow, coverage = state.flow_quote(engine.window_sec, now)
print(f"\nflow={flow:,.0f} coverage={coverage:.3f}")
print(f"burst_multiple={state.baseline.multiple(flow):.2f} "
      f"burst_z={state.baseline.zscore(flow):.2f}")
print(f"change_10s={state.change_over(engine.window_sec, now)}")
print(f"change_30s={state.change_over(30.0, now)}")
print(f"change_60s={state.change_over(60.0, now)}")
print(f"sigma_1s={state.sigma_1s:.6f} sigma_10s={state.sigma_window(engine.window_sec):.6f}")
print(f"acceleration={state.flow_acceleration(now)}")
trades, cov = state.flow_trades(engine.window_sec, now)
print(f"trades_10s={trades} (cov={cov:.2f})  trades_multiple={trades / max(state.baseline.med, 1e-9):.2f}")
print(f"spread={state.spread_pct:.6f} depth={state.book_depth_usd:,.0f}")
print(f"range_position={state.range_position(engine.window_sec, now):.3f}")
print(f"price_change_pct_24h={state.price_change_pct_24h}")

print("\n--- _evaluate direct ---")
result = engine._evaluate(state, now)
print("result:", result.summary() if result else None)

print("\n--- detect() ---")
found = engine.detect(now=now)
print("detect returned", len(found), "candidates")
for c in found:
    print("  ", c.summary())
