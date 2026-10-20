# skim — P&L autopsy (Stage 0)

Span 2026-10-18 11:51 → 2026-10-21 01:55 local, 3,591 round trips. As of 2026-10-21 01:57 local; snapshot of an append-only, live ledger.
Re-run: `python3 tools/pnl_autopsy.py`

> **Data-trust caveat:** skim books a 20 bps round-trip fee per trip; its ledger is honest about costs even when the entry logic is not the problem.

## Headline

| metric | value |
|---|---|
| round trips | 3,591 (wins 17 / losses 3,574) |
| win rate | 0.5% |
| expectancy / trip | -19.96 bps |
| median trip | -20.00 bps |
| total | -71684.23 bps |
| profit factor | 0.00 |
| max drawdown | +71684.23 bps |
| longest loss streak | 2023 |
| avg hold | 8.4s |

## Cost structure

mean gross +0.04 bps vs mean fees 20.00 bps per trip — fees are **529.0×** the mean gross move. A strategy whose round-trip cost is an order of magnitude larger than its average capture cannot be fixed by entries; it must trade less, hold longer, or capture more per trip.

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| time_cap | 3293 | 10 |
| closed_at_end | 249 | 0 |
| stop | 32 | 0 |
| skim_tp | 0 | 7 |

## Loss clustering

- **ZECUSDT** — 929 losses, -18983.29 bps
- **NEARUSDT** — 788 losses, -15711.08 bps
- **BTCUSDT** — 655 losses, -13161.44 bps
- **ETHUSDT** — 460 losses, -9163.90 bps
- **SOLUSDT** — 450 losses, -9004.56 bps

## Hypotheses for Stage 1 (counterfactual tests, ranked)

1. **Profit factor 0.00**: gross losses exceed gross wins. Stage 1: cut the loss distribution — tighter stops, lateness gate, or a session filter — and report expectancy delta per change.
2. **Fees exceed gross capture** (20.0 bps vs +0.04 bps): the model trades too often for what it captures. Stage 1: minimum-edge threshold at entry (only trade when expected move > k× fees); measure trades-per-day and expectancy jointly.
3. **Win rate 0.5%**: payoff asymmetry must carry the edge. Stage 1: stop/target asymmetry sweep.

## Sentinel reconciliation

not tracked by the sentinel (no control file key); propose coverage.

