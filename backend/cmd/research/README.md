# cmd/research — horizon-outcome model (offline trainer)

The first vertical slice of the rally/continuation research system. It builds
the training side of the loop in the same offline, network-isolated discipline
as `cmd/learner`:

```
historical klines -> features + ATR-scaled labels -> walk-forward train
                  -> saved model -> report
```

It **never trades**, never touches the live event bus, and reuses the existing
`binance` client (spot OHLCV + taker buy/sell) and the existing
`ledger.FitLogistic` logistic machinery so nothing here rewrites the core.

## What it does

1. Downloads `-limit` hourly (or other interval) candles per coin via
   `binance.Client.Klines`.
2. Forms **one training example per volatility-scaled breakout**: a bar whose
   close is beyond the recent `-range-bars` high/low by `-breakout-atr` × ATR.
3. Captures features **up to that bar only** (returns, EMA slopes, RSI, ATR%,
   volume acceleration, taker-buy ratio, range position, BTC 24-bar trend).
4. Labels each example with ATR-scaled barriers over `-horizon` bars:
   `up = close × (1 + up-atr × ATR%)`, `down = close × (1 − down-atr × ATR%)`;
   the outcome is **UP** (upside hit first), **DOWN**, or **NEITHER**.
5. Splits events **chronologically** (no shuffling) with a **one-horizon gap**
   between train and holdout so a train label cannot leak into the holdout.
6. Trains two binary logistic models — P(up first) and P(down first) — reusing
   `ledger.FitLogistic`; `P(neither) = 1 − P(up) − P(down)`.
7. Reports base rates, in-sample and holdout **Brier / log-loss**, rally
   **precision/recall**, and a 10-band **calibration** table, and writes a
   versioned `calibration_horizon_<interval>_<horizon>b.json`.

## Reproduce

```sh
cd backend
go run ./cmd/research \
  -coins BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,DOTUSDT \
  -interval 1h -limit 1000 -horizon 24 -min-events 40 -out research_out
```

Flags (defaults in brackets):

| flag | meaning |
|---|---|
| `-coins` | comma-separated universe |
| `-interval` | kline interval (`1h`) |
| `-limit` | candles per coin, max 1000 per Binance request (`1000`) |
| `-horizon` | outcome horizon in bars (`24`) |
| `-up-atr` / `-down-atr` | ATR-scaled barriers (`2.0` / `2.0`) |
| `-atr-period` | ATR lookback (`14`) |
| `-range-bars` | breakout range lookback (`20`) |
| `-breakout-atr` | breakout distance beyond range, in ATR (`0.5`) |
| `-train-frac` | chronological train fraction (`0.7`) |
| `-min-events` | refuse to fit below this many events (`100`) |
| `-out` | output directory (`research_out`) |

The test suite (`main_test.go`) exercises the label/feature/ATR/RSI logic on
synthetic candles with no network, per the repository rule that tests run
deterministically.

## Current limits (stated, not hidden)

- **Spot only.** Funding, open interest and futures liquidation data are not in
  the `binance` client, so those features are unsupported for now and simply
  absent from the feature map.
- **Logistic baseline only.** LightGBM/CatBoost are not in the toolchain; the
  honest inspectable baseline is what ships. The `-min-events` floor and the
  in-sample label are stamped into the output so the numbers are not mistaken
  for something they are not.
- **Single interval.** One interval per run; multi-timeframe features are a
  later stage.
- **Weak signal on small samples.** A first-pass logistic over a few hundred
  events is expected to be near the base rate (see the sample report). That is
  the honest answer, and it is exactly what a baseline is for.
