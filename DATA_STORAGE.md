# Data storage — schemas, locations, indexes, current state

A census of where market data lives in this system, taken 2026-10-21.
**Headline: there is no database.** Everything durable is append-only JSONL
plus one JSON model file; every index is a Go map in memory. Retention is
deliberate and total: the human's instruction is that no data is forgotten,
so nothing is rotated, truncated, or compacted anywhere.

---

## 1. The Go service (`backend/`, module `pumpdump`) — the new stack

Four durable stores, all configured by flag with **defaults relative to the
launch directory** — where the data lands depends on where the binary is
started from. That is a known wart, recorded here so it gets fixed
deliberately, not by accident.

### alerts.jsonl — durable alert history
- Flag: `-alert-history` (default `alerts.jsonl`). One JSON object per line,
  appended per emitted alert, opened `O_APPEND|O_CREATE`, never truncated.
- Schema: the `api.Alert` struct — `type, detector, batch, snapshot_ms,
  event_id, ts_ms, market, symbol, kind, summary, color,
  probability/probability_basis (omitted unless a model is fitted),
  severity, score, price, reasons[], components{}, metrics{}`.
- Readers: `HistoryForSymbol` (memory first, disk fallback — newest first),
  `ReadHistory` (full-history query, newest first, O(file) line scan),
  `HistoryStats` (O(file) scan; counts malformed lines instead of skipping
  silently).
- In-memory mirrors: global ring **2000** (`NewAlertBuffer(2000)`),
  per-symbol ring **4000** (a memory bound, not retention), startup load
  trims each symbol to its last **400**.

### ledger.jsonl — prediction ledger for the offline learner
- Flag: `-ledger` (default `ledger.jsonl`). Append-only; retraction is an
  append, never an edit.
- Record types: `prediction` (flat feature snapshot: id, detector, kind,
  symbol, direction, severity, score, entry_price, ts_ms, features map),
  `path` (price sample every 3 s with MFE/MAE), `resolution` (label, MFE/MAE,
  time-to-MFE, horizon), `retraction` (marks an id contaminated; readers
  exclude it).
- Default horizon 15 min (`-ledger-horizon`); labelling only from fixed
  horizons, never mid-path verdicts. Consumed by `cmd/learner`, which writes
  the model below and never touches live gates.

### turtle_sessions.jsonl — Turtle session history
- Flag: `-turtle-log` (default `turtle_sessions.jsonl`). Append-only, never
  rotated, flushed per record, malformed lines counted not skipped.
- Record kinds: `session_start` (full config), `session_stop` (final equity),
  `fill`, `reject` (with reason — the rows that teach), `add_unit`,
  `close`, `watchlist`, `watchlist_rotation`, `allocation`, `mark`.

### calibration.json — the fitted probability model
- Flag: `-calibration`. Written by `cmd/learner`; absent means uncalibrated
  and the API omits `probability` entirely rather than render a bare number.

### In-memory aggregates (rebuilt on restart, seeded from 1s klines)
| structure | capacity | notes |
|---|---|---|
| per-symbol tick ring | 600 s (`TrackCapacitySeconds`) | 1 s buckets, flow + trade counts |
| flow baseline | 180 samples, capped at 6× reference | lower-quartile, floored |
| trades baseline | 180 samples, capped at 6× | cannot seed from klines |
| order-size / taker-print baselines | per symbol | separates whales from retail |
| klines cache | 300 ms TTL, keyed (symbol, interval, limit) | liveness cache, not on disk |
| survey snapshot | last pass only | 150 symbols × 3 timeframes × ≤250 bars = up to 112,500 bars per pass |
| universe / tracked maps | full listing | insertion + liquidity orderings |

### Indexes — what exists and what does not
There are no secondary indexes in the database sense. Lookups that exist:
`bySymbol` alert map, klines cache map, universe maps, engine state maps.
Everything else — `ReadHistory`, `HistoryStats`, ledger reads, learner
ingest — is a full O(file) line scan, safe because files are append-only and
lines are bounded (1 MB scanner limit). If any JSONL ever grows past
scan-at-startup comfort, the fix is a sidecar offset file, never rotation.

### Current state of this checkout's stores
| store | state |
|---|---|
| `backend/turtle_sessions.jsonl` | **4,310 records**, 2.7 MB — 123 symbols, span 2026-10-16 07:17 → 21:14 UTC: 2,393 rejects, 1,480 watchlist rotations, 164 fills, 141 closes, 46 watchlists, 46 session starts, 21 allocations, 19 session stops |
| `backend/ledger.jsonl` | exists, **0 records** (service not left running long enough for a labelled prediction) |
| `backend/alerts.jsonl` | **not created yet** — first alert will create it |
| `calibration.json` | **absent — uncalibrated** |
| live proof | a boot against Binance returned 25 live flow rows; `/api/config` matched the plan exactly |

---

## 2. scalp5 — the running fleet (live as of the census)

Fourteen Python strategy processes are running right now (`master.py` on
:8799, `grid.py`, `hft.py`, `swing_v2.py`, `skim_ai.py`, `multi_models.py`,
`crypto_strategies.py`, `fast_ignition.py`, `models10.py`, `alpha_signals.py`,
`alpha_rank.py`, `turtle_multi.py`, `stats_models.py`, `slow_edges.py`,
`swing_turtle.py`), appending to their own ledgers continuously:

| file | records | size |
|---|---|---|
| `scalp5/ledger/backend.jsonl` | **809,627** | 256 MB |
| `scalp5/alerts.jsonl` | 52,598 | 62 MB |
| `scalp5/state/fills.jsonl` | 12,460 | 5.0 MB |
| `scalp5/ledger/skim.jsonl` | 3,511 | 708 KB (actively written) |
| `scalp5/ledger/scalp_fast.jsonl` | 3,801 | 900 KB (actively written) |
| `scalp5/ledger/swing_turtle.jsonl` | 323 | 132 KB (actively written) |
| `scalp5/ledger/swing3.jsonl` | 39 | 32 KB |
| `scalp5/ledger/multi_models.jsonl` | 175 | 136 KB |
| `scalp5/ledger/stats_models.jsonl` | 168 | 132 KB |
| `scalp5/bridge/messages.jsonl` | 97 | 56 KB (append-only IPC log) |

These are the old experiment's records; they are data, they stay.

## 3. binance-turtle (Python reference) and the channel

- `binance-turtle/cache/` — **101 files, 39 MB** of per-symbol-interval JSON
  klines (`SYMBOL_INTERVAL.json`, ~1,500 bars each: open_time, open, high,
  low, close, volume, close_time, quote_volume, trades, taker_buy_base,
  taker_buy_quote), plus `signals.jsonl` and backtest result JSONs.
- `pumpdump/` + `tools/` (the Python reference implementation and flow
  harness) keep **no durable store** — everything is in-memory or computed.
- `.comms/*.jsonl` — the agent channel mailbox, 3 messages at census time.
