# binance-turtle

Binance SPOT Turtle system — **paper / detection only**. Public endpoints, no API
keys, no order placement, stdlib-only Python 3.9+.

Implements the specification in
`/Users/andrewpreston/Dev/Binance/KIMI_TRADING_PLAN.md` (the plan) and
`/Users/andrewpreston/Dev/Binance/KIMI_ANSWERS2.md` (the answers).

```
binance_turtle/
  config.py        every parameter from plan §4 / answers §3, with CONFLICTS
  indicators.py    Wilder ATR, Donchian (prior-bar), ADX/DMI, RSI, SMA,
                   realized vol, ATR% percentile rank, volume confirmation
  binance_api.py   public spot/fapi client: klines w/ pagination + disk cache,
                   exchangeInfo lot steps, 24h tickers, funding, open interest,
                   sliding-window weight budget with 429/418 backoff
  strategy.py      entries §1.3, exits + ratchet §1.4, sizing §1.5,
                   portfolio governor §1.6, BTC regime latch (answers §4),
                   correlation clustering, Position state machine
  live.py          the runnable detector (scan loop + JSONL ledger)
  dashboard.py     local read-only web dashboard over the ledger (stdlib http)
tests/
  test_smoke.py    17 tests, no network: indicators, sizing, ratchet, gates
```

## Dashboard

```bash
./dashboard.sh --port 8787      # then open http://127.0.0.1:8787
```

Read-only view over `ledger/signals.jsonl`; it never talks to Binance itself.
Auto-refreshes every 20s and shows detector liveness (based on scan age), the
BTC regime pill, the latest setups with score bars, scan history, and the most
frequently recurring setups. Endpoints: `/` (page), `/api/summary` (JSON),
`/api/ledger` (raw scans), `/health`.

## Run it

```bash
./run.sh --once --top 20            # one scan, table of setups
./run.sh --once --top 20 --explain  # + why each symbol was rejected
./run.sh --interval 900 --top 40    # the live loop (default)
```

Output goes to stdout, `ledger/signals.jsonl` (append-only, one line per scan)
and `state/detector_state.json`. Klines are cached under `cache/`, so the second
scan of a symbol is a disk read, not a request.

### What a scan does

1. Universe screen — every USDT spot pair with ≥ **$5M** 24h quote volume,
   leveraged tokens (`*UPUSDT`, `*DOWNUSDT`, …) and stables excluded, capped at
   the top 400 by volume (plan §1.1 / answers §3).
2. BTC regime latch — 55-day Donchian breakout/breakdown with hysteresis
   (answers §4). While risk-off, **no new alt longs** are reported.
3. Per-symbol indicators on the daily (primary) and 4h (trigger) frames.
4. Entry evaluation, both layers:
   - **primary** — close > prior 55-day high
   - **trigger** — close > prior 120-bar (20-day) high
   each requiring volume confirmation, the 1.5–25% ATR band, no blow-off, and
   a risk-on BTC regime.
5. Sizing hint at a $10,000 reference equity (0.5%/unit, halved when 2N > 30%,
   participation-capped).

## Deliberate spec decisions

Plan and answers disagree in five places. The answers were written later and
explicitly supersede; each override is recorded in `config.CONFLICTS` and echoed
at startup:

| Parameter | Plan | Adopted |
|---|---|---|
| ADX | hard gate ≥ 20 | **soft score only, never a veto** (answers §1) |
| Liquidity floor | $750k implied | **$5M** (answers §3) |
| Risk per unit | 1% mentioned | **0.5%** (answers §3) |
| Units per correlation cluster | 6 mentioned | **2** (answers §3) |
| Notional cap | prose (§1.5) | participation form: 5% of median hourly volume × (20 min/60) |

Two further judgement calls, both documented in code:

* **Entry channels use the plan's own recommendation** (primary 55/20 daily,
  trigger 120/60 on 4h) rather than classic 20/10 on 4h — the plan calls the
  latter "a 1.5-day system and far noisier".
* **`highest fill − 2N`** in the ratchet tracks *fills*, not the highest price,
  exactly as §1.4 words it; the trailing channel is what lifts the stop in a
  trend.

## Not implemented yet

Five spec pieces remain. In the plan's priority order (§6):

1. **Volume-confirmed Donchian gate inside `survey.py`** — done here as
   `indicators.volume_confirmed` + `strategy.evaluate_entry`; the original
   `survey.py` this was meant to patch is not on this machine.
2. **Event-driven backtest harness** (§5) — the `Position` state machine,
   sizing and governor it needs exist; the bar-replay engine, slippage/fee
   model, R-multiple/MAR/DD metrics, regime breakdown, sensitivity grid and
   walk-forward blocks do not.
3. **Live position ledger / paper-trade fills** — `live.py` logs *setups*;
   it does not yet carry positions across scans, so the intended-fill ledger is
   pending the backtest engine (they share the state machine).
4. **Onset → Turtle bridge** — `flow.py` is not present; the veto/exit hooks
   exist (`onset_veto`, `onset_exit_on_critical`) and are inert.
5. **Funding / OI crowded-long veto** — `binance_api.funding_rate` /
   `open_interest` are implemented; the score wiring is not.

## Environment note

This project lives in the DSH session workspace
(`/Users/users1/Documents/deepseek-harness/default-workspace/binance-turtle`)
because the harness process runs as `users1` and **cannot write into
`~/Dev`** — the block is the Unix account, not the DSH sandbox policy.
Two files in the Binance folder (`KIMI_BRIEF.md`, `KIMI_TASK2.md`) are mode 600
owned by `andrewpreston` and were unreadable, so this build follows the two
documents that could be read.

## Safety posture

Detection only: no keys are read, no signed endpoint is ever called, no order is
ever placed. `live.py` only issues `GET`s against
`api.binance.com`, `fapi.binance.com` and `data-api.binance.vision`.
