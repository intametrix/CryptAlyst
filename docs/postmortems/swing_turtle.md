# swing_turtle — P&L autopsy (Stage 0)

323 round trips. As of 2026-10-21 01:57 local.
Re-run: `python3 tools/pnl_autopsy.py`

## Headline

| metric | value |
|---|---|
| round trips | 323 (wins 86 / losses 237) |
| win rate | 26.6% |
| expectancy / trip | $-6.20 |
| median trip | $-2.47 |
| total | $-2,001.31 |
| profit factor | 0.19 |
| max drawdown | $+2,001.31 |
| starting paper equity | $5,000 (the fleet launches with --equity 5000) |
| return on that equity | -40.0% |
| longest loss streak | 25 |
| avg hold | 596.9s |

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| time_limit | 164 | 18 |
| lock_usd | 37 | 57 |
| channel_exit | 36 | 0 |
| scrape_1.0 | 0 | 11 |

## Loss clustering

- **RLUSDUSDT** — 3 losses, $-962.36
- **TRXUSDT** — 44 losses, $-303.86
- **ETHUSDT** — 22 losses, $-149.08
- **BTCUSDT** — 17 losses, $-134.54
- **XRPUSDT** — 14 losses, $-101.21

## Hypotheses for Stage 1 (counterfactual tests, ranked)

1. **Profit factor 0.19**: gross losses exceed gross wins. Stage 1: cut the loss distribution — tighter stops, lateness gate, or a session filter — and report expectancy delta per change.
2. **Win rate 26.6%**: payoff asymmetry must carry the edge. Stage 1: stop/target asymmetry sweep.

## Sentinel reconciliation

sentinel status: **live** since 2026-10-18 09:41, reason: "(none recorded)". Note per Rule A6: status live means the model IS influencing paper positions; any fix lands as a proposal, never an auto-promote.

