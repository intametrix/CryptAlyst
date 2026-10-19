# Research log — pump/dump detection

External sources, what they actually say, and what we did about it. Kept in the
repo because a signal whose provenance is lost cannot be re-evaluated, and this
project's rule is that a claim carries its basis.

## Sources reviewed

- Bolz & Bründler, *Machine Learning-Based Detection of Pump-and-Dump Schemes in
  Real-Time* — https://ui.adsabs.harvard.edu/abs/2024arXiv241218848B/abstract
  A **Z-score-based statistical model** over historical and short-term order-book
  and trade metrics. Confirms our approach of normalising against a symbol's own
  baseline rather than using absolute dollar thresholds.
- *The Anatomy of a Cryptocurrency Pump-and-Dump Scheme* —
  https://ar5iv.labs.arxiv.org/html/1811.10109
  Time series of hourly log returns from **48 hours before to 3 hours after** a
  pump. The pre-pump window is where the exploitable signal lives; the post window
  is where naive detectors buy the top.
- ACM, pump-day order statistics —
  https://dlnext.acm.org/doi/pdf/10.1145/3733815
  Reports **Average Order Size (7 days) vs Average Order Size (pump day)** as an
  **Order Size Increase Ratio**. THE MOST DIRECTLY ACTIONABLE FINDING and it is
  now implemented — see `AvgOrderSize` below.
- *Order-Flow Toxicity in Cryptocurrency Perpetual Futures* —
  https://www.sciencedirect.com/science/article/pii/S1059056026009901
  Notes a specification that **removes the two 30-second realised-volatility
  predictors**, which is a caution about volatility features: they can absorb the
  effect of the flow variables they sit beside and make a model look like it works
  when the volatility term is doing all of it. Relevant to us because volatility
  is one of the three factors the human asked for on dumps.
- Turtle in crypto — https://www.gateskills.ai/zh-tw/research/article/gate-research-turtle-trading-rules-classic-system-with-annual-returns-up-to-62-71
  Reports annualised returns around 62.71% for the classic rules applied to
  crypto. Treated as a CLAIM, not a result: no methodology, survivorship or fee
  assumptions were visible, and our own implementation reports an upper bound
  under perfect liquidity, so the two numbers are not comparable.

## What we took from it

### 1. Average order size, not just aggregate volume — IMPLEMENTED
The ACM ratio is the clearest leading indicator in the set. Our flow measurement
sums quote volume, which cannot distinguish **one whale order from five hundred
retail orders of the same total**. The ratio makes that distinction: large
individual prints mean few participants moving size, which is what accumulation
looks like before a pump. Added as `AvgOrderSize` and `OrderSizeRatio` on the
taker path, where prints are already recorded.

### 2. Z-scores over absolute thresholds — ALREADY OUR APPROACH
Every gate in this system is already a multiple of the symbol's own baseline. The
review confirms the choice rather than changing it.

### 3. The pre-pump window is the whole game — ALREADY OUR APPROACH
The 48-hours-before framing matches why this system measures onset at 3/5/10/15s
rather than reacting to the pump itself. The anatomy paper's post-pump window is
the failure mode our latency work exists to avoid.

### 4. A caution about volatility features — RECORDED, NOT YET ACTED ON
If volatility absorbs the flow variables beside it, a model can appear to work
while only the volatility term is doing anything. The six async models report
their factors **separately** and name which ones fired, which is the mitigation,
but no ablation has been run. Doing so is the right next experiment: fit with and
without the volatility term and compare.

## Open leads not yet pursued

- The Z-score model's specific feature list (behind a PDF we could not parse here).
- The unsupervised pre-listing anomaly framework —
  https://link.springer.com/article/10.1007/s42521-026-00227-x
  Stability-validated, compares pre-announcement against control windows. Its
  control-window discipline is directly applicable to our optimisation loop: any
  parameter set must be compared against a control window, not just against the
  previous parameter set.
