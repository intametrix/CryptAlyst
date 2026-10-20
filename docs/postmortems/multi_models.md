# multi_models — P&L autopsy (Stage 0)

Span 2026-10-19 09:24 → 2026-10-20 21:57 local, 175 round trips. As of 2026-10-21 01:57 local; snapshot of an append-only, live ledger.
Re-run: `python3 tools/pnl_autopsy.py`

## Headline

| metric | value |
|---|---|
| round trips | 175 (wins 3 / losses 172) |
| win rate | 1.7% |
| expectancy / trip | $-76.30 |
| median trip | $-65.76 |
| total | $-13,352.87 |
| profit factor | 0.01 |
| max drawdown | $+13,352.87 |
| starting paper equity | $5,000 (the fleet launches with --equity 5000) |
| return on that equity | -267.1% |
| longest loss streak | 112 |
| avg hold | 339.8s |

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| clock | 96 | 0 |
| lock_usd | 76 | 3 |

## Loss clustering

- **XRPUSDT** — 16 losses, $-1,307.22
- **HYPEUSDT** — 16 losses, $-1,058.07
- **SUIUSDT** — 8 losses, $-851.22
- **WLDUSDT** — 11 losses, $-805.80
- **SOXLBUSDT** — 12 losses, $-782.03

## Sub-model vote attribution (decision models)

Which component voted for the trades that lost. yes-in-loss / yes-in-win counts, and mean trip net when the sub-model voted yes vs no.

| sub-model | yes: losses | yes: wins | mean net when YES | mean net when NO |
|---|---|---|---|---|
| adaptive_donchian | 20 | 0 | $-84.21 | $-75.28 |
| adaptive_ma | 24 | 0 | $-76.97 | $-76.20 |
| adaptive_macd | 162 | 3 | $-75.42 | $-90.92 |
| adaptive_bbands | 142 | 3 | $-74.91 | $-83.01 |
| adaptive_rsi | 99 | 3 | $-71.55 | $-82.95 |

Confound to keep in view: when the whole book bleeds, every sub-model yes-vote bleeds — the yes/no gap above is descriptive, not causal. The Stage 1 counterfactual (replay with the component removed) is the only way to attribute drag to a component; treat the ranking as a search order, not a verdict.

## Hypotheses for Stage 1 (counterfactual tests, ranked)

1. **Sub-model `adaptive_donchian` drags the book**: its yes-votes average $-84.21 over 20 trips. Stage 1: veto or down-weight it; measure expectancy delta with it removed from the vote.
2. **Profit factor 0.01**: gross losses exceed gross wins. Stage 1: cut the loss distribution — tighter stops, lateness gate, or a session filter — and report expectancy delta per change.
3. **Win rate 1.7%**: payoff asymmetry must carry the edge. Stage 1: stop/target asymmetry sweep.

## Sentinel reconciliation

sentinel status: **live** since 2026-10-18 09:41, reason: "(none recorded)". Note per Rule A6: status live means the model IS influencing paper positions; any fix lands as a proposal, never an auto-promote.

