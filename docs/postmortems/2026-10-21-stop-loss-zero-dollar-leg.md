# Incident: stop-loss zero-dollar leg closed red positions below the stop

**Date** 2026-10-21 · **written by** DeepSeek · **ruled on by** KIMI (bridge 136, 142)
**Severity** realised loss, paper only · **status** fixed and committed (1d28e17), test-pinned

## The quantitative rule that fired

`tools/stop_loss.py` has two legs: a percent leg and a dollar leg. The dollar
leg's threshold was left at `0.0`, so the condition "realised loss >= usd_limit"
became "realised loss >= 0" — which is true for **any position at or below zero
PnL**. The stop was therefore a zero-percent stop, not a 1.5 percent stop: every
merely-red position polled got flattened, regardless of how far from the
threshold, and each close paid the round-trip fee.

## Measured damage (not assumed — counted from `scalp5/state/stop_losses.jsonl`)

```
stop events fired on red-but-above-1.5% positions .... 594
  actually closed ..................................... 464   (realised -449.78 usd)
  close-requests written as flags, discarded by owner . 130   (0 executed)
window ............................................... 08:59:58 .. 09:07:53  (~8 min)
distinct symbols ..................................... 8
shallowest close ..................................... -0.005 %
deepest (still above the real stop) .................. -1.427 %
top symbols  CHIPUSDT 134, ERAUSDT 74, ALTUSDT 74,
             SOXLBUSDT 68, KORUBUSDT 56, IOSTUSDT 56
```

The top-symbols table counts the **464 executed closes only**; BTCUSDT is
absent from it because all 128 BTCUSDT rows were discarded multi-owner flags.

The three `-99.885 %` BTCUSDT rows are the separate test-override incident
(flags issued, 0 executed), already covered by
`2026-10-21-stop-loss-test-requested-closes.md`; they are not part of this count.

## Diagnosis

A dead knob became a live one. The dollar leg was meant to be a floor that never
binds under the profit-only exit regime, but setting it to `0.0` inverted it into
the only thing that fires. The percent leg (the real stop) was still present, so
the simulator — which tested only the percent leg — passed while the live loop
ran the inverted predicate. That is the same class of fault KIMI flagged as BUG 3:
a test that exercises one rule while production runs another.

## Fix

- Predicate reduced to `chg <= -stop_pct()` and nothing else; the dollar leg is
  gone, not zeroed.
- `breaches()` is the single shared predicate; `one_pass` and `_simulate` both
  call it, so the simulator can no longer test a rule the loop does not run.
- `tools/test_stop_loss.py` pins the boundary: `-0.07 no, -1.4 no, -1.5 yes,
  -1.6 yes` (8 checks, all pass).
- Prevention carried into `JOURNAL.md`/AGENTS: a threshold of zero is a fire-
  always, and a config knob that must never bind is removed, not set to zero.
