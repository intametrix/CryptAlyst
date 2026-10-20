# grid — P&L autopsy (Stage 0)

Span 2026-10-19 09:01 → 2026-10-21 01:57 local, 10,484 round trips. As of 2026-10-21 01:57 local; snapshot of an append-only, live ledger.
Re-run: `python3 tools/pnl_autopsy.py`

> **Data-trust caveat:** this engine marks exits on its own terms (e.g. reason profit_3s), so its win rate is scratch-optimistic and its PnL is NOT mark-to-market reality. Treat every number below as an upper bound; the trustworth loss surface for the system lives in the sentinel-monitored model reports.

## Headline

| metric | value |
|---|---|
| round trips | 10,484 (wins 10,484 / losses 0) |
| win rate | 100.0% |
| expectancy / trip | +8.389% |
| median trip | +4.540% |
| total | +inf% |
| profit factor | inf |
| max drawdown | +0.000% |
| longest loss streak | 0 |
| avg hold | 768.2s |

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| grid | 0 | 10484 |

## Loss clustering

- (no losses)

