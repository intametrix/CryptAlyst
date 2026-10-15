# Ledger & offline learner — design

## What this is

`internal/ledger` turns every directional detection (`Kind == "PUMP"` or
`Kind == "DUMP"`) into an open prediction, tracks its price path at a fixed
sample interval, and assigns a label at a fixed horizon. Everything is
appended to a JSONL file (`{"type":"prediction"|"path"|"resolution"}`) so the
record survives restarts and can be analysed offline by `cmd/learner` and by
`ledger.Report`.

## Labelling: fixed horizon, never mid-path verdicts

Labels are assigned at fixed horizon only, never at sample time. A verdict
taken mid-path flips with noise: a price that is +1.4% at minute 5 and −0.9%
at minute 9 would read as "working" then "failed" depending on when you
looked. Sampling only updates MFE/MAE; the label is decided once, at
`TSMS + Horizon` (or at the stop-out, whichever comes first):

- **CORRECT** — MFE ≥ TargetPct (default +1.5%) reached before MAE hit
  StopPct (default −1.0%).
- **WRONG** — MAE ≥ StopPct before MFE ≥ TargetPct.
- **EXPIRED** — horizon reached with MFE < TargetPct.

After a stop-out the label is frozen, but the MFE/MAE path continues to be
sampled until horizon for diagnostics. AWR is direction-adjusted: for
`Direction = −1` favourable means the price *fell*.

## The label rule defines every accuracy number

Precision, expectancy, median MFE/MAE — all of it is a function of
(H, S, T) = (horizon, stop, target). Changing any of them re-labels history
in spirit and silently invalidates comparability with older reports. That is
why the constants are stamped into every prediction and resolution record
(`horizon_ms`, `target_pct`, `stop_pct`): the label rule that produced a
number travels with the number. Do not change the defaults without renaming
the ledger file.

## Reporting: cohorts, never blends; unresolved always published

`ledger.Report` reports precision per severity, per direction, and per
detector — never blended into one headline number, because the base rates
differ wildly across those cohorts and blending hides which model or
direction is actually failing. The unresolved count (predictions with no
resolution record yet) is always published explicitly: silence about open
predictions would bias any accuracy number upward. The learner trains only on
labelled rows, and says so.

## Learner: offline-only, chronological, min-sample-constrained

`cmd/learner` reads the ledger, builds feature rows (the same flat feature
map stored at alert time), sorts them by `TSMS`, and splits chronologically —
no shuffling; this is a time series and random splits leak the future into
the training set. The champion is the incumbent gates (no gate applied);
the challenger is a threshold coordinate search over `-param name=min:max:step`
grids, evaluated on the **training** set only, keeping the value that
maximizes expectancy subject to a minimum-sample constraint (≥ 30 predictions
in the evaluated subset, else the cell is skipped), two passes over the
parameter list. Expectancy is in R terms: CORRECT = +TargetPct, WRONG =
−StopPct, EXPIRED = MFE capped at TargetPct.

**Promotion rule (verbatim):** the challenger promotes only if its holdout
expectancy is strictly greater than the champion's holdout expectancy AND the
challenger's holdout sample count is at least 30. Otherwise the champion is
kept. The decision and the reason are printed explicitly; the exit code is 0
unless the ledger is unreadable.

The sweep is honest about what it is: a threshold like
`-param burst_multiple=2:16:1` filters training rows to those with that
feature ≥ the value and measures how the surviving rows' precision and
expectancy change. It learns which gate values would have selected the
winners — it does not fit a response surface.

## Why no logistic regression (this pass)

Labels are sparse — a few hundred resolutions across a handful of detectors —
and high-dimensional relative to that. Logistic regression on this data would
mostly fit the prior. Threshold coordinate search is the interpretable
baseline: every promoted value is a gate a human can read and sanity-check
against the detector's own params. Crucially, the feature rows are already
stored in exactly the shape a future learner needs (flat numeric map per
prediction, joined to a label), so upgrading the learner later does not
require re-instrumenting the scanner.

## Integration surface

```go
type Recorder interface {
    Record(sig detect.Signal)
    Track(ctx context.Context)
}
```

The scanner calls `Record` for every emitted signal (non-PUMP/DUMP kinds pass
unrecorded) and runs `Track` in one background goroutine. `New(path, market,
cfg)` takes a `ledger.PriceFetcher` (`Price(ctx, symbol) (float64, error)`)
so any market-data client can be adapted without this package knowing its
signature.

## Calibration (I-11): logistic regression over alert-time features

`calibrate.go` fits plain gradient-descent logistic regression (L2
lambda=1e-3, 2000 iterations, lr 0.1, standardized features) on the feature
rows captured at alert time, using resolution labels with an explicit,
deliberate mapping: **CORRECT=1, WRONG=0, EXPIRED=0**. This differs from the
expectancy mapping on purpose: expectancy treats EXPIRED as a soft partial
win (MFE capped at target), but the classifier predicts the binary event
"did it hit target before stop" — and EXPIRED means the target was not hit.
The model's output is P(hit target), not expected R; `base_rate` is the hit
rate.

**In-sample caveat:** Brier score, log loss, and the 10-bucket reliability
table are computed on the FITTED rows, not a holdout. With 100–300 rows a
split would leave the holdout too thin to judge; honest provenance beats a
pretend-out-of-sample number. The label rule string is stamped into the file
so no one mistakes the numbers for holdout quality.

**Refusal:** fewer than `MinCalibrationN = 100` resolved rows means no
calibration file is written at all — the learner prints
`insufficient sample: N resolved, need 100 for calibration` and exits 0. A
reliability table on 30 rows is noise shaped like a table.

**Replacement rule:** a new fit replaces the existing calibration file
wholesale — no coefficient merging, no incremental updates. The write is
atomic (temp file + rename) so a live reader never sees a torn document.

Features missing from a scoring map are imputed at the train-set mean
(standardized value 0), exactly as at fit time; heterogeneous detectors with
different metric keys are therefore usable in one model.

## Short-horizon (10s) calibration

`cmd/learner -short` fits the same logistic machinery against fastsample
`label_10s` records: CORRECT=1, WRONG=0, DISCARD excluded (the ±0.1% 10s band
produces DISCARD when neither side of the band is touched). Usable minimum is
300 labels. The no-lift rule ships `calibration_10s.json` only if the
in-sample Brier beats the pooled base-rate Brier `p*(1-p)` by at least 3%
relative — a strict `<` cannot refuse honestly, since a zero-signal fit wins
by ~1e-4 of in-sample noise. All metrics remain in-sample; the label_rule
field in the shipped file records the 10s band rule.
