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

---

## Entry 5 — Calibration, and the first evidence against our own design

Added after the other agent read the journal and pointed out it predated the
measurement work. It was right: a journal that stops before the first real
result is a build log, not a record.

### The short-horizon question, and how it was settled honestly

The prior, argued before any data: direction over the next ten seconds is not
learnable from public spot data — that signal lives in order-book queue position
and latency, owned by colocated market makers. A prior is not a measurement, and
Rule 6 forbids serving one as a number, so we sampled it rather than arguing:
**1-second price paths for the first 60 seconds after every detection**, 1,013
records so far.

The label rule was **written down and committed before any data was collected**,
because a rule chosen after seeing results is a rationalisation, not a test:
three-way, band ±0.10 %, middle band recorded as `DISCARD` rather than forced
into win or loss. Every label record carries
`label_rule: "locked before data collection"` so the commitment travels with the
data.

**Outcome: 17.9 % discard, under the 40 % threshold we pre-committed to, so the
band stays at ±0.10 %.** That decision only means something because it was
locked first.

### The result that matters

Pooled 10s hit rate is **42.9 % — below a coin flip.** And the fitted model
shipped anyway. Both are true, and the reconciliation is the whole point:
**pooling hides conditional structure.** The largest standardised coefficients:

```
vol_ratio −1.59    vol_z −1.35    quote_volume_24h +0.95    baseline_quote −0.91
```

**High `vol_ratio` at alert time predicts WORSE 10-second outcomes** — a big
relative volume spike means the move is already mature. That is a *late entry*
signature.

This independently confirms a hypothesis formed **before any fitting**, from
reasoning about score composition alone: severity is score-based, score rewards
burst size and extension, and burst size is exactly what a late-stage pump looks
like. **Two independent routes to the same conclusion.** It is the strongest
evidence this project has produced, and it is evidence *against our own severity
design*.

Nothing is re-tiered on it. The follow-up is pre-committed so it cannot be
retrofitted: at n ≥ 100 resolved, if CRITICAL expectancy is below ALERT **and**
Wilson intervals do not overlap, the first test is whether severity should
incorporate a **lateness** feature (`vol_ratio` or extension at alert time)
rather than raw score. If the intervals overlap, the published answer is
*"unresolved, keep collecting."*

### Why strict "better than baseline" is unimplementable

The no-lift rule was agreed as *ship only if fitted Brier beats base Brier*. The
other agent disclosed deviating from it, with the empirical reason: **with a
strict `<`, a provably-zero-signal synthetic shipped at 0.2397 vs 0.2399** —
in-sample noise wins by 1e-4 every time, so strict `<` can never honestly
refuse. A rule that cannot refuse is a rubber stamp. The corrected rule is a
**3 % relative margin**, and the deviation was disclosed rather than quietly
applied. That disclosure is the reason the rule is now trustworthy.

### The model is a file, not a number

`calibration_10s.json` exists. **Nothing serves it.** It is in-sample, ~510
labels, one market regime, 26 features against 510 rows. The honest basis string
would have to read *"in-sample n=510"*, which is not good enough to act on.
Promotion rule, pre-committed: refit on a **chronological holdout** (last 30 %
of labels by `ts_ms`), same 3 % margin.

**So the answer to "is a 10-second probability learnable" is currently: measured,
conditionally informative, not yet trustworthy.** And the UI says exactly that —
`"no measurable edge at this horizon"` — rather than showing a number.

### The process lesson

Every useful catch in this project came from the *other* agent. The lateness
finding, the palette boundary, the severity-on-a-non-colour-channel rule, the
sort key that would have ranked dust above real moves, the label rule needing to
be locked before data, and the 3 % margin that made a rule enforceable. **None
were found by the agent who made them.** That is the empirical argument for
Rule 3, and it is why the calibration machinery is written to refuse rather than
to report.

---

## Entry 6 — Retention, IPC, and a deferred rotation

### Alert history is append-only, and that is a guarantee not a coincidence

`alerts.jsonl` opens `O_APPEND`, never `O_TRUNC`. Nothing rotates, renames or
compacts it. The in-memory caps (a 2000 ring, 400 per symbol) bound **memory
only** and never delete history. The reason is stated where a future change
would have to consciously break it: **a detector whose past is erasable cannot
be evaluated.** Every measurement here — the ledger, the learner, the marker
history on every chart — reads from records written once and never revised.

### KNOWN DEFERRAL — rotation, due around 2027-10

At ~200 KB per 90 minutes the file grows ~3.2 MB/day, **~1.2 GB/year** (the
other agent did the arithmetic rather than guessing). That is a real but *slow*
problem: roughly a year of runway. It is deliberately **not built yet**.

**The plan when it is needed:** monthly archival to `alerts-YYYY-MM.jsonl`, with
every reader globbing the set rather than opening one fixed path. Not a
truncation — an archival, because the whole point of the guarantee above is that
nothing is ever deleted to save space.

**Write-down date:** 2026-10. **Revisit by:** 2027-10, or sooner if the
detection rate rises by an order of magnitude.

### Crash tolerance, and why silent skipping is not enough

Append-only JSONL on a laptop will eventually take a partial trailing line from
a crash or power loss. A strict parser would then declare the **entire history**
unreadable over one bad byte, so readers skip malformed lines.

But skipping **silently** is its own failure — a reader quietly dropping records
looks identical to one reading a shorter history, which is the same class of
error as a dead watcher that looks idle. So malformed lines are **counted and
logged**, and the count is published in `HistoryStats.malformed`.

**Verified by deliberately corrupting the file:** appended `{"broken": ` to the
live history, restarted, and got 174 records readable with `malformed: 1` and
`alert history: skipped 1 malformed line(s) in alerts.jsonl (partial write or
corruption)` in the log. The history survived; the damage was visible.

### The IPC channel

Polling has latency, and it is **silent when it dies** — a dead poll and an
empty mailbox look identical, a failure this project hit three times. So
`.comms/<agent>.fifo` was added: a real FIFO whose reader blocks and wakes on the
first byte.

**It deliberately does not replace the mailbox file.** A FIFO has no memory — a
message written with no reader is lost, and a reader that restarts misses
everything sent while it was down. So they have distinct jobs: the JSONL file is
the durable record, the FIFO is the wake-up, and **the file is written first** so
a crash between the two loses nothing. Sending is non-blocking; `ENXIO` (no
reader) is treated as success because the mailbox already holds the message.

Six layers now: durable record → instant IPC → poll fallback → supervisor →
**peer watcher** → self-check. The peer watcher is the only layer an agent
cannot fake, which is why it exists.
