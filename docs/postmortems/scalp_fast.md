# scalp_fast — P&L autopsy (Stage 0)

Span 2026-10-18 11:51 → 2026-10-21 01:55 local, 3,906 round trips. As of 2026-10-21 01:57 local; snapshot of an append-only, live ledger.
Re-run: `python3 tools/pnl_autopsy.py`

## Headline

| metric | value |
|---|---|
| round trips | 3,906 (wins 12 / losses 3,894) |
| win rate | 0.3% |
| expectancy / trip | -19.92 bps |
| median trip | -20.10 bps |
| total | -77810.02 bps |
| profit factor | 0.00 |
| max drawdown | +77810.02 bps |
| longest loss streak | 1655 |
| avg hold | 6.4s |

## Cost structure

mean gross +0.08 bps vs mean fees 20.00 bps per trip — fees are **252.0×** the mean gross move. A strategy whose round-trip cost is an order of magnitude larger than its average capture cannot be fixed by entries; it must trade less, hold longer, or capture more per trip.

## Exit reasons — losses vs wins

| reason | losses | wins |
|---|---|---|
| momentum_flip | 1651 | 3 |
| ema_cross_down+rsi<48 | 989 | 0 |
| max_hold | 739 | 9 |
| run_end | 280 | 0 |
| atr_stop | 235 | 0 |

## Loss clustering

- **USD1USDT** — 813 losses, -16328.00 bps
- **USDCUSDT** — 710 losses, -14262.80 bps
- **ZECUSDT** — 547 losses, -10993.54 bps
- **BTCUSDT** — 444 losses, -8719.65 bps
- **ETHUSDT** — 387 losses, -7656.31 bps

## Hypotheses for Stage 1 (counterfactual tests, ranked)

1. **Profit factor 0.00**: gross losses exceed gross wins. Stage 1: cut the loss distribution — tighter stops, lateness gate, or a session filter — and report expectancy delta per change.
2. **Fees exceed gross capture** (20.0 bps vs +0.08 bps): the model trades too often for what it captures. Stage 1: minimum-edge threshold at entry (only trade when expected move > k× fees); measure trades-per-day and expectancy jointly.
3. **Win rate 0.3%**: payoff asymmetry must carry the edge. Stage 1: stop/target asymmetry sweep.

## Sentinel reconciliation

not tracked by the sentinel (no control file key); propose coverage.

