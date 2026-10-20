# stats_models — P&L autopsy (Stage 0)

Span 2026-10-19 09:07 → 2026-10-20 21:57 local, 168 round trips. As of 2026-10-21 01:57 local; snapshot of an append-only, live ledger.
Re-run: `python3 tools/pnl_autopsy.py`

## Headline

| metric | value |
|---|---|
| round trips | 168 (wins 5 / losses 163) |
| win rate | 3.0% |
| expectancy / trip | $-27.60 |
| median trip | $-26.41 |
| total | $-4,637.56 |
| profit factor | 0.01 |
| max drawdown | $+4,637.56 |
| starting paper equity | $5,000 (the fleet launches with --equity 5000) |
| return on that equity | -92.8% |
| longest loss streak | 85 |
| avg hold | 489.4s |

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| clock | 131 | 0 |
| lock_usd | 31 | 5 |
| stop | 1 | 0 |

## Loss clustering

- **SOXLBUSDT** — 14 losses, $-436.94
- **WLDUSDT** — 10 losses, $-320.37
- **SPCXBUSDT** — 11 losses, $-315.15
- **SNDKBUSDT** — 13 losses, $-306.74
- **INJUSDT** — 7 losses, $-299.45

## Sub-model vote attribution (decision models)

Which component voted for the trades that lost. yes-in-loss / yes-in-win counts, and mean trip net when the sub-model voted yes vs no.

| sub-model | yes: losses | yes: wins | mean net when YES | mean net when NO |
|---|---|---|---|---|
| logistic | 101 | 2 | $-27.94 | $-27.07 |
| autocorr | 109 | 3 | $-27.72 | $-27.38 |
| ols_trend | 135 | 4 | $-27.61 | $-27.58 |
| squeeze_break | 4 | 0 | $-25.63 | $-27.65 |
| zscore_revert | 11 | 1 | $-25.19 | $-27.79 |

Confound to keep in view: when the whole book bleeds, every sub-model yes-vote bleeds — the yes/no gap above is descriptive, not causal. The Stage 1 counterfactual (replay with the component removed) is the only way to attribute drag to a component; treat the ranking as a search order, not a verdict.

## Hypotheses for Stage 1 (counterfactual tests, ranked)

1. **Sub-model `logistic` drags the book**: its yes-votes average $-27.94 over 103 trips. Stage 1: veto or down-weight it; measure expectancy delta with it removed from the vote.
2. **Profit factor 0.01**: gross losses exceed gross wins. Stage 1: cut the loss distribution — tighter stops, lateness gate, or a session filter — and report expectancy delta per change.
3. **Win rate 3.0%**: payoff asymmetry must carry the edge. Stage 1: stop/target asymmetry sweep.

## Sentinel reconciliation

sentinel status: **live** since 2026-10-18 09:41, reason: "(none recorded)". Note per Rule A6: status live means the model IS influencing paper positions; any fix lands as a proposal, never an auto-promote.

