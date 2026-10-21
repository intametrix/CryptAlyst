#!/usr/bin/env python3
"""selection_counterfactuals.py — Stage-1 action 2 of the master plan.

Three jobs, all read-only against data/npz/:

1. BIT-MATCH ACCEPTANCE. Recompute derived quantities from the exported raw
   columns and compare against the values the live system recorded:
     skim:       net_bps == gross_bps - fee_bps ; cum_bps == cumsum(net_bps)
     scalp_fast: net_bps == gross_bps - fees_bps - spread_bps
     fills:      side-aware gross_pct == (exit-entry)/entry*100
   Any nonzero residual beyond float tolerance means the export or the
   ledger semantics are misunderstood — sweeps are forbidden until this
   passes (master-plan Rule 2).

2. SELECTION-LEVEL COUNTERFACTUALS. Honest scope: the skim/scalp_fast
   ledgers record per-trip results but no intra-trade price path and no
   per-trip feature vector for skim, so exit-rule counterfactuals are NOT
   computable yet. What IS computable: trip-selection counterfactuals —
   "what would expectancy have been if trips with property X had been
   vetoed". These are hypothesis generators, not validated improvements:
   every veto here was chosen on the same data it is evaluated on.

3. CPU TIMING BASELINE (master-plan Rule 5 input). Times a dummy sweep of
   10,000 parameter combinations over the vectorized skim pass, and a
   mask+mean pass over the 809k-row backend table. These numbers feed the
   cpu-vs-gpu ratio that decides whether Stage-2 GPU sweeps are justified.

Writes docs/postmortems/stage1_replay.md and prints the same to stdout.
"""
import json
import time
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
NPZ = ROOT / "data" / "npz"
REPORT = ROOT / "docs" / "postmortems" / "stage1_replay.md"


def load(name):
    z = np.load(NPZ / f"{name}.npz")
    return {k: z[k] for k in z.files if k != "n_rows"}, int(z["n_rows"])


def check(label, residual, tol, lines):
    worst = float(np.nanmax(np.abs(residual))) if residual.size else 0.0
    ok = worst <= tol
    lines.append(f"- {label}: max |residual| = {worst:.6g} (tol {tol:g}) "
                 f"-> {'PASS' if ok else 'FAIL'}")
    return ok


def bit_match(lines):
    lines.append("## 1. Bit-match acceptance")
    all_ok = True

    skim, n = load("skim")
    fee = skim["fee_bps"]
    net_rec = skim["net_bps"]
    gross = skim["gross_bps"]
    all_ok &= check("skim net_bps == gross_bps - fee_bps",
                    net_rec - (gross - fee), 1e-9, lines)
    # skim cum_bps is a PER-SESSION counter: the skim bot runs in ~120 s
    # windows and resets the cumulative at each window boundary (verified:
    # 330 sessions, every base == first net, max within-session drift 0.03
    # = 2dp rounding over <=13 rows). Acceptance = step identity holds
    # within sessions at rounding tolerance, bases are ~0, and every row
    # belongs to exactly one session.
    cum_rec = skim["cum_bps"]
    STEP_TOL = 0.011   # recorded values are rounded to 2dp
    breaks = np.where(np.abs(np.diff(cum_rec) - net_rec[1:]) > STEP_TOL)[0]
    bounds = np.concatenate(([0], breaks + 1, [len(net_rec)]))
    seg_resid, bad_base = [], 0
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = cum_rec[a:b] - np.cumsum(net_rec[a:b])
        seg_resid.append(float(np.max(np.abs(seg))))
        if abs(cum_rec[a] - net_rec[a]) > STEP_TOL:
            bad_base += 1
    worst_seg = max(seg_resid) if seg_resid else 0.0
    n_sessions = len(seg_resid)
    ok = worst_seg <= 0.05 and bad_base == 0
    lines.append(f"- skim cum_bps == per-session cumsum(net_bps): "
                 f"{n_sessions} sessions (~120s windows), worst in-session "
                 f"|resid| = {worst_seg:.3f}, non-zero bases = {bad_base} "
                 f"-> {'PASS' if ok else 'FAIL'}")
    lines.append(f"  (session stats: median len {int(np.median([b - a for a, b in zip(bounds[:-1], bounds[1:])]))} trips, "
                 f"max len {max(b - a for a, b in zip(bounds[:-1], bounds[1:]))} trips)")
    all_ok &= ok

    sf, _ = load("scalp_fast")
    resid_plain = sf["net_bps"] - (sf["gross_bps"] - sf["fees_bps"])
    resid_spread = sf["net_bps"] - (sf["gross_bps"] - sf["fees_bps"] - sf["spread_bps"])
    if np.nanmax(np.abs(resid_spread)) <= np.nanmax(np.abs(resid_plain)):
        all_ok &= check("scalp_fast net_bps == gross - fees - spread",
                        resid_spread, 1e-6, lines)
    else:
        all_ok &= check("scalp_fast net_bps == gross - fees",
                        resid_plain, 1e-6, lines)

    fills, _ = load("fills")
    side = json.loads((NPZ / "fills.categories.json").read_text())["side"]
    buy_code = side.index("buy")
    sell_code = side.index("sell")
    side_codes = fills["side__code"]
    entry, exit_, gross_pct = fills["entry"], fills["exit"], fills["gross_pct"]
    fwd = np.where(side_codes == buy_code, exit_ - entry, entry - exit_)
    implied = np.where(entry != 0, fwd / np.where(entry != 0, entry, 1.0) * 100.0, np.nan)
    all_ok &= check("fills gross_pct == side-aware (exit-entry)/entry*100",
                    gross_pct - implied, 1e-2, lines)
    lines.append("")
    return all_ok, skim, sf, fills


def selection_counterfactuals(lines, skim, sf):
    lines.append("## 2. Selection-level counterfactuals (hypothesis generators)")
    lines.append("")
    lines.append("All deltas are in-sample: the veto was chosen on the same "
                 "trips it is scored on. Treat every row below as a candidate "
                 "hypothesis to validate with purged walk-forward CV before "
                 "any code change.")
    lines.append("")

    net = skim["net_bps"]
    sym_codes = skim["symbol__code"]
    syms = json.loads((NPZ / "skim.categories.json").read_text())["symbol"]
    base_n, base_mean = net.size, float(np.mean(net))
    lines.append(f"### skim baseline: n={base_n:,}, mean net {base_mean:.2f} bps/trip")
    lines.append("")

    # (a) per-symbol expectancy
    lines.append("### (a) Per-symbol expectancy (n >= 30)")
    lines.append("")
    lines.append("| symbol | n | mean net bps | share of trips |")
    lines.append("|---|---:|---:|---:|")
    rows = []
    for c, name in enumerate(syms):
        m = sym_codes == c
        nn = int(m.sum())
        if nn >= 30:
            rows.append((name, nn, float(np.mean(net[m])), nn / base_n))
    rows.sort(key=lambda r: r[2])
    for name, nn, mean, share in rows[:10]:
        lines.append(f"| {name} | {nn} | {mean:.2f} | {share:.1%} |")
    lines.append("")
    for k in (5, 10):
        veto = {r[0] for r in rows[:k]}
        vcodes = {syms.index(v) for v in veto}
        keep = ~np.isin(sym_codes, list(vcodes))
        if keep.sum():
            m = float(np.mean(net[keep]))
            lines.append(f"- veto worst-{k} symbols {sorted(veto)}: "
                         f"n={int(keep.sum()):,}, mean {m:.2f} bps "
                         f"(delta {m - base_mean:+.2f})")
    lines.append("")

    # (b) per-reason veto
    lines.append("### (b) Per-reason veto")
    lines.append("")
    reason_codes = skim["reason__code"]
    reasons = json.loads((NPZ / "skim.categories.json").read_text())["reason"]
    lines.append("| reason | n | mean net bps | P(exit via reason) |")
    lines.append("|---|---:|---:|---:|")
    for c, name in enumerate(reasons):
        m = reason_codes == c
        nn = int(m.sum())
        if nn:
            lines.append(f"| {name} | {nn} | {float(np.mean(net[m])):.2f} | {nn / base_n:.1%} |")
    lines.append("")
    for c, name in enumerate(reasons):
        m = reason_codes == c
        if m.sum() > 100 and float(np.mean(net[m])) < base_mean - 1:
            keep = ~m
            mm = float(np.mean(net[keep]))
            lines.append(f"- veto reason '{name}': n={int(keep.sum()):,}, "
                         f"mean {mm:.2f} bps (delta {mm - base_mean:+.2f})")
    lines.append("")

    # (c) min hold-time filter
    lines.append("### (c) Hold-time floor")
    lines.append("")
    held = skim["held_s"]
    for floor in (3.0, 5.0, 10.0, 30.0):
        keep = held >= floor
        if keep.sum():
            m = float(np.mean(net[keep]))
            lines.append(f"- skip trips held < {floor:.0f}s: n={int(keep.sum()):,}, "
                         f"mean {m:.2f} bps (delta {m - base_mean:+.2f}, "
                         f"kept {keep.sum() / base_n:.0%})")
    lines.append("")

    # (d) scalp_fast entry-RSI band
    lines.append("### (d) scalp_fast entry-RSI band")
    lines.append("")
    snet = sf["net_bps"]
    rsi = sf["entry_rsi"]
    valid = ~np.isnan(rsi)
    sbase = float(np.mean(snet[valid]))
    sn = int(valid.sum())
    lines.append(f"baseline (rows with entry_rsi): n={sn:,}, mean {sbase:.2f} bps")
    lines.append("")
    lines.append("| RSI band | n kept | mean net bps | delta |")
    lines.append("|---|---:|---:|---:|")
    for lo, hi in ((0, 40), (0, 50), (0, 60), (20, 60), (30, 70), (40, 80), (50, 90), (0, 90)):
        keep = valid & (rsi >= lo) & (rsi <= hi)
        if keep.sum():
            m = float(np.mean(snet[keep]))
            lines.append(f"| {lo}-{hi} | {int(keep.sum()):,} | {m:.2f} | {m - sbase:+.2f} |")
    lines.append("")


def timing_baseline(lines):
    lines.append("## 3. CPU timing baseline (Rule-5 input)")
    lines.append("")
    skim, n = load("skim")
    net, held, rsi = skim["net_bps"], skim["held_s"], skim["gross_bps"]
    rng = np.random.default_rng(7)

    combos = 10_000
    t0 = time.perf_counter()
    for i in range(combos):
        floor = rng.uniform(1.0, 30.0)
        rmin = rng.uniform(-10.0, 10.0)
        keep = (held >= floor) & (rsi >= rmin)
        if keep.any():
            _ = float(np.mean(net[keep]))
    dt = time.perf_counter() - t0
    lines.append(f"- skim-scale dummy sweep: {combos:,} combos over n={n:,} rows "
                 f"in {dt:.2f}s -> {combos / dt:,.0f} combos/s "
                 f"({dt / combos * 1e3:.2f} ms/combo)")

    backend, bn = load("backend")
    key = "prediction.features.burst_multiple"
    if key in backend:
        x = backend[key]
        y = backend["mae_pct"] if "mae_pct" in backend else x
        t0 = time.perf_counter()
        reps = 200
        thr = np.nanmean(x)
        for _ in range(reps):
            m = x > thr
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                _ = float(np.nanmean(np.where(m, y, np.nan)))
        dt2 = time.perf_counter() - t0
        lines.append(f"- backend-scale vectorized pass: {reps} mask+mean over "
                     f"n={bn:,} rows in {dt2:.2f}s -> {reps / dt2:,.0f} passes/s")
    else:
        lines.append(f"- backend-scale pass skipped: '{key}' not in backend npz")
    lines.append("")
    lines.append("Interpretation: at skim scale the CPU already does ~"
                 f"{combos / dt:,.0f} selection-mask combos/s. GPU sweeps "
                 "(Stage 2) are justified only for path-level replay where a "
                 "single combo costs O(n_trips x path_len), not O(n_trips). "
                 "That ratio is measured when the path-level replay lands; "
                 "these numbers are the selection-mask anchor.")
    lines.append("")


def main():
    lines = ["# Stage 1 groundwork: bit-match + selection counterfactuals + CPU timing", ""]
    lines.append("Generated by tools/selection_counterfactuals.py against data/npz/. "
                 "Re-run after any ledger export refresh.")
    lines.append("")
    ok, skim, sf, fills = bit_match(lines)
    selection_counterfactuals(lines, skim, sf)
    timing_baseline(lines)
    lines.append(f"**Bit-match acceptance: {'PASS — sweeps unblocked' if ok else 'FAIL — sweeps forbidden'}**")
    text = "\n".join(lines)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
