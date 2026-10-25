# cmd/microburst — micro-burst horizon-outcome trainer

Offline trainer for the micro-burst detector's horizon models. It reads the
live ledger produced by the flow engine + fastsample and fits, per horizon
(10/30/60s), two binary logistic models — **P(upside-first)** and
**P(reversal-first)** — with a **chronological walk-forward split**.

## Data it consumes (already produced live)

- `prediction` records — `{id, symbol, direction, ts_ms, features}` where
  `features` is the flat flow-feature map captured at alert time
  (`burst_multiple`, `burst_z`, `change_10s`, `taker_buy_ratio`, …).
- `label_barrier_10s/30s/60s` records — the symmetric up/reversal labels
  written by `fastsample` (see `internal/fastsample/sampler.go`).

A prediction with no barrier label at a horizon is treated as **NEITHER** (no
barrier was hit within that window).

## Run

```sh
cd backend
go run ./cmd/microburst -ledger /path/to/ledger.jsonl -horizons 10,30,60 -min-events 100 -out microburst_out
```

Flags: `-ledger` (required), `-horizons` (default `10,30,60`), `-train-frac`
(0.7), `-min-events` (100), `-out`.

## Output

Per horizon: base rates (up / reversal / neither), in-sample and holdout
Brier / log-loss, up-continuation precision/recall, false up-alerts per
coin-hour, and a `microburst_<N>s.json` model file.

## Why walk-forward here

The 15m and 10s calibrations in `cmd/learner` are deliberately in-sample
(their sample counts are too thin to split). The micro-burst horizon labels
accumulate far faster (one per detection per horizon), so a chronological
split is honest and the holdout numbers are real out-of-sample quality, not a
pretend number.

## Current limits

- The barrier band is still the fixed `±0.10%` from fastsample; volatility-
  scaling it by the flow engine's `sigma_10s` is a small follow-up (pass it
  through the prediction record).
- `NEITHER` is inferred from "no label" — if a prediction was never sampled by
  fastsample it would be misread as NEITHER. The live server runs fastsample
  on every detection, so this is rare, but the reader should be extended to
  require a `path_1s` record before accepting a NEITHER.
