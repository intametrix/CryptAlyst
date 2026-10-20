# movers — P&L autopsy (Stage 0)

Span 2026-10-19 08:53 → 2026-10-21 01:57 local, 794 round trips. As of 2026-10-21 01:57 local; snapshot of an append-only, live ledger.
Re-run: `python3 tools/pnl_autopsy.py`

> **Data-trust caveat:** this engine marks exits on its own terms (e.g. reason profit_3s), so its win rate is scratch-optimistic and its PnL is NOT mark-to-market reality. Treat every number below as an upper bound; the trustworth loss surface for the system lives in the sentinel-monitored model reports.

## Headline

| metric | value |
|---|---|
| round trips | 794 (wins 794 / losses 0) |
| win rate | 100.0% |
| expectancy / trip | +11.245% |
| median trip | +8.370% |
| total | +10541978604011100251273904822501244928.000% |
| profit factor | inf |
| max drawdown | +0.000% |
| longest loss streak | 0 |
| avg hold | 282.5s |

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| clock | 0 | 60 |
| profit_3s | 0 | 734 |

## Loss clustering

- (no losses)

