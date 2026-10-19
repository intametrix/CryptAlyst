# Trading Plan — Binance SPOT Turtle + Pump/Dump Detection

**Status:** detection is built and running. This document is the plan the code
implements, with the numbers that are actually in the binary.

**Authorship:** the parameter framework, failure modes and validation design
come from the design review with KIMI (`~/Dev/Binance/KIMI_TRADING_PLAN.md`);
the corrections in §5 come from its follow-up
(`~/Dev/Binance/KIMI_ANSWERS2.md`).

---

## 1. What this system is

Two detection layers with different jobs, deliberately not merged:

| layer | timeframe | question it answers | code |
|---|---|---|---|
| **Onset** | 1 second | "has volume *suddenly started* flowing, and is price beginning to move?" | `backend/internal/flow` |
| **Pump/dump screen** | 5/15/60 min | "what is pumping or dumping right now?" | `backend/internal/screen` |
| **Turtle survey** | 1d / 4h | "is there a structural breakout worth risking money on?" | `backend/internal/turtle` |

The onset layer is the early-warning system and the circuit breaker. The Turtle
layer is the position-taking system. The screen is the situational picture.

Why the split matters: a Turtle breakout on a daily chart and the terminal leg
of a pump-and-dump look **identical** on a daily bar. Only the 1-second layer
can tell them apart in time to matter, and only the bar layer can size a
position.

---

## 2. Market selection

- All Binance SPOT USDT-quoted pairs, `TRADING` status only.
- **Liquidity floor: $5,000,000 24h quote volume** (`--min-liq`). Below roughly
  this level, expected slippage on a 2N stop exit runs 0.3–1% and thin books
  manufacture false flow bursts. On the live market this selects ~109 symbols.
- Excluded: leveraged tokens (`*UPUSDT`, `*DOWNUSDT`, `*BULLUSDT`, `*BEARUSDT`).
- Universe is re-ranked every 60s by 24h volume and the websocket subscription
  is resized to match, so a symbol that goes quiet loses its slot.

## 3. Onset detection (the "suddenly" layer)

Binance's `@ticker` stream carries cumulative 24h counters, so differencing them
gives exact per-second traded volume. Summing that over a window is the whole
measurement. Every gate exists to stop that number from lying:

| gate | value | why |
|---|---|---|
| flow window | 10s | the design point; this is what "the last 10 seconds" means |
| burst multiple | ≥ 8× the symbol's own baseline | "a lot of volume" is relative to that symbol |
| burst z-score | ≥ 4 | or a ≥ 16× multiple, since a volatile baseline inflates σ |
| absolute floor | ≥ $5,000 in the window | stops a near-zero divisor faking a huge multiple |
| price move | ≥ 0.4% and ≥ 3σ of window volatility | volume without a move is not an onset |
| tick coverage | ≥ 70% of the window | refuses to score a burst built from 2 messages |
| acceleration | current window > prior window | volume still building = the *start*, not the tail |
| lateness guard | reject if the 60s move > 3× the 10s move | that is arriving late |
| 24h move | reject if already > 60% in the same direction | late-stage pump |
| spread / depth | < 0.4% spread, > $500 top-of-book | thin-book artefacts |
| trade count | ≥ 8 trades in the window | three prints is not a pump |

**Baseline construction** is the subtle part. The reference is the **lower
quartile** of recent 10-second flows, floored at 25% of the 24h average rate.
Using the median instead creates a feedback loop: stored bursts push the median
up toward the contamination cap, the next cap is a multiple of the new median,
and the baseline ratchets until the symbol goes blind. Samples are also capped
at 6× the current reference so a sustained pump cannot inflate the baseline it
is measured against.

**Baselines are seeded at startup** from 1000 one-second klines (~16 minutes of
history). Without this the scanner is blind for the first ~80 seconds after a
restart — exactly when a move is most likely to be missed.

**Trade-level escalation:** when a symbol trips the onset gate it is subscribed
to its own `@aggTrade` stream for 180s, which supplies taker-side buy/sell
imbalance. This distinguishes aggressive market buying from a wick on a thin
book, and is unscheduled afterwards to keep the stream count bounded.

**Alert scoring** (0–100, every component reported in the alert):
flow burst 30 · speed 25 · aggression 25 · quality 20 − lateness penalty.
Severity: `WATCH` < 55 ≤ `ALERT` < 75 ≤ `CRITICAL`.

## 4. Turtle system (the position-taking layer)

Indicators are Wilder-correct: ATR(20) is the Turtles' `N`, Donchian levels come
from **completed bars only** (comparing a bar to a channel containing that bar is
a tautology), ADX/DMI(14), RSI(14).

- **Entry:** close beyond the prior 20-bar high (System 1) or 55-bar high
  (System 2).
- **Exit:** close below the prior 10-bar low, or the 2N stop, whichever comes
  first. The stop only ever ratchets toward the trade.
- **Sizing:** `Unit = (risk% × equity) / N`. The defining property is that a 1N
  adverse move costs the same dollars in every market, which is what makes
  positions comparable across instruments of wildly different volatility.
- **Pyramiding:** add a unit every ½N in favour.

### Portfolio limits

| limit | value | rationale |
|---|---|---|
| risk per unit | **0.5%** of equity | with alt correlations 0.6–0.9, three 1% units are one 3% bet |
| max units per market | 4 | original Turtle |
| max units per correlation cluster | **2** | one cluster *is* one trade in a cascade |
| max units total | **6** | crypto alts are one asset class in different tickers |
| max notional per position | 25% of equity | N-based sizing can demand more than a spot account can fill |

Correlation clusters are computed from realised returns rather than a static
map, because crypto correlation is too unstable for hand-assigned groups.

## 5. Corrections adopted from the design review

Three of these changed the running code, and two of them were things I had
wrong:

1. **Volume confirmation is now a gate, and it fails closed.** A breakout must
   clear `1.5 × max(20-bar median volume, $20,000)` **and** trade at least
   $150,000 absolute. Flooring the *divisor* stops a near-zero median from
   making any ratio look large; the absolute floor stops a dust-volume bar from
   confirming off dust-volume history. A quiet symbol's breakout is *rejected*,
   not passed — which is precisely the wick-hunt this gate exists to kill.
2. **ADX is no longer a veto.** ADX is smoothed about three times over, so it
   lags exactly the fresh breakouts this system wants and reads high during
   blow-off tops. It now scores trend strength; the volume gate and the
   volatility band do the filtering.
3. **The BTC regime gate is latched with hysteresis, not a live threshold.**
   Risk-off begins on the first daily close below the prior 55-day low and ends
   only on a close above the prior 55-day *high*. The two levels differ so the
   gate cannot flap while price sits on the boundary — flapping is exactly when
   the worst alt entries happen. During risk-off no new alt entries and no
   pyramid adds, but existing positions are **not** force-exited; they resolve
   under their own stops, and the onset circuit breaker stays armed.
4. **Volatility band:** ATR% must sit between 1.5% and 25%. Below is dead
   capital; above makes the 2N stop a coin flip.

## 6. Where this loses money

Honest list, because a plan without one is marketing:

- **Range regimes.** Donchian systems bleed sideways. Expect streaks of 8–15
  stopped trades. This is the normal cost of the edge, and the reason most
  people switch the system off immediately before the trend that pays for it.
- **Wick hunts.** Alt breakouts routinely sweep the level by 1–2% and reverse.
  The volume gate is what moves this from the #1 loss source to #3.
- **Pump-and-dump tops.** The pattern the onset layer detects *is* what a Turtle
  breakout looks like on a daily bar. Late-stage pumps that satisfy every daily
  gate then collapse 40% in a day. The 24h-move veto and the blow-off filter are
  partial mitigations only.
- **Gap-through stops.** 2N has 0.5–2% expected slippage on alts in a fast
  market, 5%+ on memes. Size assuming 1.5× stop slippage.
- **BTC risk-off cascades.** Alt correlations go to 0.9 and every stop triggers
  in the same hour. The 3% total-risk cap is the only defence.
- **Fee drag.** ~0.1% per side. A 4h-trigger system churning weekly pays 1–2%
  per month against an edge honest crypto backtests put at 3–6%/month in good
  regimes. Prefer maker entries.
- **Single-venue risk.** A Binance outage during a cascade is unmodellable.

## 7. Not built yet (in priority order)

1. **Event-driven backtest harness.** Everything else is decoration until this
   exists, because it is what prices in slippage and fees. Signals on completed
   bars, entries at next-bar open, stops evaluated intra-bar with conservative
   fills, no lookahead. Refuse to trade if MAR < 0.5, max DD > 45%, expectancy
   < +0.15R after costs, or fewer than 60 trades.
2. **Stop/ratchet state machine + paper ledger.** Position state per symbol, the
   `max(2N, trailing 10-bar channel)` ratchet, pyramid adds, and the total-risk
   governor. Run 4 weeks live with no orders and compare signal rate to backtest.
3. **Onset → Turtle bridge.** Feed downside onset alerts into the position state
   machine as exit/veto events (the circuit breaker), and upside onset alerts as
   watchlist accelerators.
4. **Funding / open-interest context** from the public futures endpoints — spot
   can't trade them, but extreme funding with flat OI at a breakout is a
   crowded-long veto, and it's free.
5. **Blow-off filter:** skip entries where RSI(1d) > 85 *and* price is > 1.6×
   its 200-day mean.

## 8. How to run it

```bash
# backend (Go) — serves the dashboard and the API on one port
cd backend
go build -o bin/pumpdump ./cmd/server
./bin/pumpdump                      # http://127.0.0.1:8787

# dashboard (React) — only needed when changing the frontend
cd web && pnpm install && pnpm build   # writes backend/web/dist, which is embedded

# the Python tree is the reference implementation and the validation harness
python run.py --once                # live single-sweep smoke test
python run.py --mode pumps          # pump/dump screen
python run.py --mode survey         # Turtle survey
python tools/test_flow.py           # offline gate validation (28 checks)
```

Useful flags: `--min-liq`, `--window`, `--min-burst-mult`, `--min-severity`,
`--equity`, `--timeframes`, `--no-screen`, `--no-survey`.
