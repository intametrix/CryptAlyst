# Development journal

A running record of what was built, what broke, and what was learned. Written
for whoever picks this up next — including us, in a week.

---

## Entry 1 — Why the detection is shaped the way it is

**The ask:** track pump and dumps on Binance spot, and *signal the beginning* of
a sharp move at roughly ten-second resolution, not confirm it afterwards.

That framing drove everything. Confirmation is easy and worthless; onset is hard
and is the only thing worth paying for. Every threshold in the system exists to
answer "is this the start, or is this the tail?"

**Onset design.** Binance's `@ticker` stream carries *cumulative* 24h counters,
so differencing consecutive messages yields exact per-second quote volume. The
10-second window sum is the flow measurement.

Four decisions came out of getting this wrong first:

1. **Baseline is the p25, floored — not the median.** A median baseline ratchets.
   A stored burst pushes the median up; the next cap is a multiple of the new
   median; the ceiling climbs until the symbol can never fire again. It goes
   quietly blind. The lower quartile is robust to the very spikes we are
   detecting. Samples are additionally capped at 6x the reference.
2. **Liquidity floor on the baseline**, `quote_volume_24h / 8640 * 0.25`. Not the
   divisor — a sanity guard, so a symbol that has not ticked yet cannot produce
   an infinite multiple.
3. **Coverage gate.** Refuse to score a window less than 70% covered by received
   ticks. A burst measured from two ticks is not a measurement.
4. **Window bounds on both sides.** The upper bound matters because the
   acceleration check queries a *past* window; without it the "previous" window
   swallows current ticks and the ratio is meaningless.

**The bug that cost the most time.** `messages: 0` while `stream_messages: 2604`.
Every ticker failed to unmarshal. Go's `encoding/json` matches struct tags
**case-insensitively**, so `"e"` (event type, a string) collided with `E` (event
time, int64). Separately, `"Q"` (best-bid quantity) silently overwrote `"q"`
(24h quote volume): `QuoteVol: 0.00008` instead of `1835539603.39`. Fixed by
exact-key map decoding. *Lesson: never bind a wire format to a tagged struct
when keys differ only by case.*

---

## Entry 2 — Measuring instead of arguing

Every threshold above was an argued opinion. That is fine to start and
unacceptable to keep.

The **prediction ledger** records each signal with its feature vector, samples
the price path every 3s, labels at a fixed horizon, and grades offline via
`cmd/learner` with champion/challenger promotion. Three rules, all of which came
from a review of the design:

- **UNRESOLVED is published unconditionally.** Silence about open predictions is
  the easiest way to fake accuracy.
- **Labels come only from fixed horizons**, never mid-path verdicts.
- **The learner never touches live gates.** It proposes; a regression test
  promotes.

The first real report, on 10 resolved predictions:

```
overall:  n=35 resolved=10 hit=10.0% exp=-0.03%
CRITICAL: n=13 resolved=3  hit= 0.0% exp=-0.67%
ALERT:    n=12 resolved=1  hit= 0.0% exp=-1.00%
WATCH:    n=10 resolved=6  hit=16.7% exp=+0.46%
```

Too small to conclude anything, and the learner says so. But the *shape* is
worth watching: our highest-confidence tier has the worst expectancy. If that
survives 30+ resolutions, **the severity ordering is inverted**, and that is a
finding worth more than any feature.

---

## Entry 3 — Working with other agents

Work was split across two agents plus a human, coordinated through a file-backed
mailbox (`tools/comms.sh`) with a blocking `wait` so neither agent idles at a
prompt. This produced the single most valuable process artifact of the project.

**`ITERATIONS.md`:** scope each change small, time-box it, and *prove it with a
command, not by reading the code*.

That rule caught a real production deadlock within one round:
`AlertBuffer.Add` took the write lock and then called `appendToDisk`, which took
the same lock again. `sync.RWMutex` is not reentrant, so the scanner froze and
`/api/alerts` hung with zero bytes. Reading the code would not have found it —
the symptom was a timeout, not an error.

**The corollary, learned twice:** when a test says "broken", suspect the test
first. A malformed synthetic fixture made a working learner look broken, and a
wrong JSON key made working forecasts look absent. Both times the code was fine.

---

## Entry 4 — Forecast, structure and confidence

**Reversion forecast.** Projects exponential decay from current price toward the
pre-move anchor. Three rules, because a line drawn on a chart is the most
persuasive thing in the UI and therefore the easiest place to mislead by
accident:

- **No forecast when nothing is displaced.** A flat line through a flat market
  teaches people to ignore the line.
- **Every forecast states its basis.** The half-life is *measured from this
  symbol's own prior excursions* (typically 100–220 of them) or it is a
  documented prior, and the caller is told which, with the count.
- **Every forecast carries a band**, widening with the square root of the
  horizon.

**Support/resistance** from swing pivots — places where price actually turned,
which is evidence of resting interest — clustered, with strength from touch
count and recency. Not round numbers, not moving averages.

**Event outlook** reports lifecycle state (`onset`/`active`/`exhausting`), an
ETA to reversal **derived from the forecast half-life** so the chart line and the
ETA cannot disagree, and a 10-second distribution.

The 10-second block is the most dangerous output in the system, so it is
deliberately unexciting: it currently reports **p_up = 0.500** and
`"no measurable edge at this horizon"`, with the basis naming the sample
shortfall. A fitted model from the learner overrides it automatically once the
ledger supports one. **There is no code path that emits a bare probability.**

---

## Current state

**Working and verified:** 285-symbol spot universe · pluggable detectors · flow
onset at 10s · heat glow with two-threshold hysteresis · bar-aligned P/D/R/B
markers · durable alert history · per-tick SSE push (~24 frames/5s) · reversion
forecast with measured half-lives · support/resistance · event outlook with ETA ·
prediction ledger → path tracking → offline learner · inter-agent comms.

**Open:** the ledger needs ~30+ resolutions before the learner's first honest
expectancy number · the 10s probability awaits a fitted model · CRITICAL-vs-WATCH
inversion is unconfirmed and is the thing to check next · the `window.open`
pop-out variant was deliberately not built (the in-page overlay cannot be
popup-blocked).

**Not built, on purpose:** any order placement. This system detects, measures
and explains. It does not trade.
