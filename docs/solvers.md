# Solvers: how we fix the models without fooling ourselves

Companion to `docs/master-plan-back-analysis.md`. Stage 0 told us *what*
bled; this document defines *how* we search for fixes with statistical
honesty, and what compute each step actually needs. Every claim here is
grounded in numbers measured on this repo, not in generic best-practice
assertion.

---

## 1. The problem, stated precisely

Each strategy is a parameterized decision system: entry rule, exit rule,
sizing, and (for the ensemble models) vote gates. Fixing a model means
choosing parameters θ that maximize a noisy objective:

```
θ* = argmax_θ   E[ net P&L per trip(θ) ]          (expectancy, after fees)
      s.t.      max drawdown(θ) ≤ D
                n_trades(θ) ≥ N                    (statistical power)
                gross edge(θ) ≥ fees + margin      (economic viability)
```

Two things make this a *solver* problem rather than a tuning problem:

1. **The objective is stochastic and serially dependent.** Each evaluation
   is a backtest replay over a fixed tape; trip P&L is heavy-tailed and
   autocorrelated. Treating backtest output as a clean function value is
   the root cause of most strategy-tuning failures.
2. **The feasible region is defined by costs.** Stage-1 groundwork
   (docs/postmortems/stage1_replay.md) measured skim's gross expectancy
   at ≈ +0.04 bps/trip against a 20 bps round-trip fee — a ~500×
   cost-to-capture ratio. No optimizer escapes arithmetic: the constraint
   `gross edge ≥ fees + margin` fails for skim at *every* selection-mask
   setting we tested (worst-5-symbol veto: +0.22 bps; RSI bands: ±0.1 bps;
   hold-time floors: ±0.3 bps). The search space that matters is not
   "which trips to skip" but "how to raise gross from 0 to >20 bps" —
   entry signal strength or fee-tier/maker-fill structure. This
   pre-optimization **landscape survey** is step zero of any solver run:
   if the optimum over the cheap parameterization is flat, change the
   parameterization before buying compute.

## 2. Why naive optimization overfits

- **Multiple testing.** Evaluating 10,000 parameter combos and keeping the
  best inflates the winner's measured Sharpe by ~E[max of noise]. With
  n≈3,600 correlated trips, the noise floor on mean net bps is ≈ σ/√n.
  The **deflated Sharpe ratio** (Bailey & López de Prado) corrects for
  trial count: a "2σ" backtest after 10,000 trials is not significant.
  We compute DSR before accepting any candidate.
- **Temporal leakage.** Random k-fold CV on time series leaks the future
  into the past through overlapping windows and regime similarity. All CV
  here is **purged k-fold with embargo**: train/test splits are
  contiguous time blocks; any sample whose label window overlaps the test
  block is purged from training; an embargo gap (≥ max holding period)
  separates train from test.
- **In-sample selection.** Every counterfactual in stage1_replay.md was
  chosen on the data it is scored on. They are *hypotheses*. Promotion
  requires the hypothesis to survive purged walk-forward evaluation at
  its own stated threshold, out of sample.

## 3. The optimizer ladder

Spend cheap compute first; escalate only when the cheaper tier stalls.

| Tier | Method | When | Measured cost on this box |
|---|---|---|---|
| 0 | Landscape survey (flatness check over the cheap parameterization) | always first | seconds (63k mask-combos/s, n=3.6k rows) |
| 1 | Coarse grid + constraint filter | small discrete param spaces | 10k skim combos in 0.16 s |
| 2 | Bayesian opt (TPE) | ≤20 continuous/discrete dims, expensive evals | eval-bound, not search-bound |
| 3 | CMA-ES | correlated, ill-conditioned param landscapes | eval-bound |
| 4 | Gradient ascent via JAX autodiff | differentiable relaxation exists | requires the smoothed replay (§5) |

Constraint handling: penalized objective (softmax barrier on drawdown),
not repair operators — barriers compose with any tier.

## 4. The evaluation harness (what a solver call actually runs)

A candidate θ is scored by a replay function with a **contract**:

```
replay(tape, θ) -> {net expectancy, maxDD, n_trades, per-trip ledger}
```

- The tape is immutable and versioned (data provenance rule of the master
  plan).
- Equity is accumulated in **fp64** (master-plan Rule 3) even if the
  fast path uses fp32/fp16.
- **Bit-match acceptance** (master-plan Rule 2) gates everything: the
  Stage-1 harness proved ledger semantics by reproducing recorded
  `net_bps`/`cum_bps` from raw columns (including discovering that
  skim `cum_bps` is a per-~120 s-session counter, 330 sessions, all
  bases zero). A replay whose accounting doesn't reproduce the live
  ledger to float tolerance is a bug, not a result.
- Every result carries provenance: tape hash, code git SHA, θ, seed.

## 5. Where gradients come from (Tier 4)

Exit rules are discrete (stop hit / TP hit / time cap), so the replay is
piecewise-constant in θ — gradients are zero almost everywhere. The
standard resolution is a **straight-through smoothed relaxation**:

- Replace hard threshold comparisons with sigmoid/softplus windows
  (temperature τ): `hit_stop ≈ sigmoid((price − stop_level)/τ)`.
- P&L becomes a smooth function of θ; JAX autodiff gives exact gradients
  through the whole vectorized replay.
- Anneal τ → 0 and confirm the smooth optimum lands inside the same
  basin as the discrete replay's grid search (cross-check between
  Tier 1 and Tier 4 results on a small subproblem before trusting
  Tier 4 anywhere).

This is where the GPU actually earns its keep: the smooth replay
evaluates all n trips × path length simultaneously, and gradients let
second-order optimizers (L-BFGS, natural gradient) replace thousands of
black-box evals.

## 6. Compute: what we have, what it's for

Measured facts (2026-10-21):

- **This machine**: Apple M1, 8 CPU cores (4P+4E), 16 GB unified RAM.
  NumPy 2.4.4 on Apple's Accelerate BLAS. No JAX/PyTorch installed yet —
  Stage-2 dependency, gated by the timing rule below.
- **CPU throughput**: 63k selection-mask combos/s at skim scale
  (n=3.6k); 315 full-table mask+mean passes/s over the 809k-row backend
  table. For Tiers 0–2 at current data sizes, the CPU finishes any sane
  sweep in seconds-to-minutes. CPU is not the bottleneck; *evaluator
  fidelity* is.
- **M1 GPU**: Metal 4, 7–8 cores, shares the 16 GB. Invisible to NumPy;
  reachable via JAX (Metal plugin) or PyTorch MPS. Justified only when a
  single evaluation is O(n_trips × path_len × n_params_vectorized) — the
  path-level replay and Tier-4 gradients.
- **ATLAS (external GPU agent)**: used exclusively through the job
  contract (files-in/files-out, provenance, no shared workspace) defined
  in the master plan. The CPU runs the same code as the audit twin and
  bit-matches before any GPU result is believed.

**Timing rule (master-plan Rule 5, made concrete)**: install JAX and
open the GPU path only when the measured ratio
`t_path_replay_cpu / t_path_replay_setup` for the smallest useful sweep
exceeds ~10× the amortized setup cost. Selection-mask workloads (Tiers
0–1) never qualify — 0.02 ms/combo on CPU. The decision is revisited
when the path-level replay lands, with the numbers in
docs/postmortems/stage1_replay.md as the anchor.

## 7. Statistical acceptance before any code change

A candidate promotion must pass, in order:

1. **DSR** (deflated Sharpe, trial count from the actual sweep log) above
   a pre-registered threshold.
2. **Purged walk-forward**: positive net expectancy in ≥ k of m forward
   folds with the fold-wise confidence interval excluding the fee floor.
3. **PBO check** (probability of backtest overfitting, CSCV) below 50%.
4. **Human gate** (AGENTS.md Rule A6): models stay disabled until a
   human re-enables them; every disable ships with its post-mortem.

## 8. Status

- [x] Tier 0 landscape survey for skim/selection masks (flat — fee-bound)
- [x] Bit-match acceptance harness (tools/selection_counterfactuals.py)
- [x] npz export layer (tools/export_npz.py)
- [ ] Path-level tape reconstruction (unblocks exit-rule counterfactuals)
- [ ] Purged walk-forward harness
- [ ] JAX timing experiment (decides GPU opening)
- [ ] Tiers 2–4 on the first model chosen for repair
