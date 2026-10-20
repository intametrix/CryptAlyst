# hft — P&L autopsy (Stage 0)

Span 2026-10-19 09:08 → 2026-10-21 01:48 local, 1,229 round trips. As of 2026-10-21 01:57 local; snapshot of an append-only, live ledger.
Re-run: `python3 tools/pnl_autopsy.py`

> **Data-trust caveat:** this engine marks exits on its own terms (e.g. reason profit_3s), so its win rate is scratch-optimistic and its PnL is NOT mark-to-market reality. Treat every number below as an upper bound; the trustworth loss surface for the system lives in the sentinel-monitored model reports.

## Headline

| metric | value |
|---|---|
| round trips | 1,229 (wins 1,226 / losses 3) |
| win rate | 99.8% |
| expectancy / trip | +4.906% |
| median trip | +2.360% |
| total | +517475145337578536644378624.000% |
| profit factor | inf |
| max drawdown | +0.000% |
| longest loss streak | 1 |
| avg hold | 597.4s |

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| clock | 3 | 956 |
| target | 0 | 270 |

## Loss clustering

- **NEARUSDT** — 3 losses, +0.000%

