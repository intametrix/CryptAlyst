# API contract — pumpdump backend ↔ React dashboard

Base URL: `http://127.0.0.1:8787`. All responses are JSON. All times are
**milliseconds since epoch (UTC)** unless the field name says otherwise.
Fractions (returns, ratios) are **fractions, not percents** — `0.0461` means
+4.61%. The UI is responsible for `×100` display formatting.

The backend serves the built frontend from `/` (static files), so the dashboard
and API share an origin and no CORS proxy is needed.

---

## Live stream — `GET /api/events` (Server-Sent Events)

`Content-Type: text/event-stream`. The client uses `EventSource`. Every message
is a JSON object with a `type` discriminator.

| `type` | cadence | meaning |
|---|---|---|
| `hello` | once on connect | server time + effective config |
| `status` | every 2s | counters, health, uptime |
| `flow` | every 1s | current flow leaderboard (top ~25) |
| `alert` | on occurrence | a detected pump/dump onset or reversal |
| `screen` | every 60s | market-wide pump/dump screen results |
| `survey` | every 5 min | Turtle indicator survey + allocation |

### `hello`
```json
{ "type": "hello", "server_time_ms": 1792091700000,
  "config": { "market": "spot", "window_sec": 10, "min_burst_multiple": 8,
              "min_burst_z": 4, "min_move_pct_10s": 0.4, "min_flow_usd": 5000,
              "min_quote_volume_24h": 750000, "max_symbols": 400,
              "cooldown_sec": 900, "severity_min": "WATCH" } }
```

### `status`
```json
{ "type": "status", "ts_ms": 1792091700000, "uptime_sec": 1234,
  "symbols_tracked": 400, "baselines_ready": 388, "messages": 512345,
  "scans": 1234, "candidates": 41, "alerts": 9,
  "universe_volume_24h": 4820000000,
  "screen_generated_ms": 1792091690000,
  "survey_generated_ms": 1792091400000,
  "stream_connected": true }
```

### `flow`
```json
{ "type": "flow", "ts_ms": 1792091700000, "window_sec": 10,
  "rows": [ {
    "symbol": "NEARUSDT", "last_price": 106.1,
    "flow_quote": 70200, "baseline_quote": 7700,
    "burst_multiple": 9.1, "burst_z": 6.2,
    "change_10s": -0.0009, "change_30s": 0.001, "change_60s": 0.002,
    "coverage": 1.0, "trades_10s": 120, "taker_buy_ratio": 0.62,
    "spread_pct": 0.0002, "book_depth_usd": 5000,
    "quote_volume_24h": 304000000, "updated_ms": 1792091700000
  } ] }
```
`rows` is sorted by `flow_quote` descending. `taker_buy_ratio` may be `0.5`
when no trade-level data has been collected for that symbol.

### `alert`
```json
{ "type": "alert", "event_id": "a1b2c3d4e5f6", "ts_ms": 1792091700000,
  "market": "spot", "symbol": "OSMOUSDT", "kind": "PUMP",
  "severity": "CRITICAL", "score": 77, "price": 0.0477,
  "reasons": ["10s flow $76,400 = 16.1x baseline (15.9sigma)",
              "10s move +4.61% (9.2x 0.50% vol)"],
  "components": { "flow": 22.5, "speed": 25, "aggression": 14, "quality": 15.5 },
  "metrics": { "window_sec": 10, "flow_quote": 76400, "burst_multiple": 16.1,
               "burst_z": 15.9, "change_10s": 0.0461, "change_30s": 0.0647,
               "change_60s": 0.0553, "sigma_multiple": 9.2, "coverage": 1.0,
               "trades_10s": 214, "trades_multiple": 3.1, "acceleration": 2.4,
               "range_position": 0.82, "spread_pct": 0.0002,
               "book_depth_usd": 5120, "quote_volume_24h": 5450000,
               "latency_ms": 0, "penalty": 0, "agg_confirmed": true,
               "agg_buy_quote": 41200, "agg_sell_quote": 18900,
               "agg_buy_ratio": 0.685, "agg_trades": 214,
               "agg_largest_trade_usd": 9100 } }
```
`kind` ∈ `PUMP | DUMP | REVERSAL_DUMP | REVERSAL_PUMP`.
`severity` ∈ `WATCH | ALERT | CRITICAL`.
Any `metrics` key may be absent or `null`; the UI must tolerate that.

### `screen`
```json
{ "type": "screen", "ts_ms": 1792091700000, "symbols_scanned": 200,
  "rows": [ {
    "symbol": "OSMOUSDT", "kind": "PUMP", "score": 77, "price": 0.0477,
    "ret_5m": 0.0461, "ret_15m": 0.0647, "ret_60m": 0.0553, "ret_24h": 0.12,
    "vol_ratio": 16.1, "vol_z": 15.9,
    "recent_quote_volume": 76400, "baseline_quote_volume": 4700,
    "quote_volume_24h": 5450000, "trades_ratio": 2.1,
    "taker_buy_ratio": 0.54, "close_position": 0.82, "range_pct": 0.052,
    "atr_pct": 0.0097, "from_24h_high": -0.0104, "from_24h_low": 0.0915,
    "bars_since_extreme": 1, "reasons": ["5m +4.61% / 15m +6.47% / 1h +5.53%"]
  } ] }
```
`rows` sorted by `score` descending. `kind` ∈ `PUMP | DUMP | REVERSAL_DUMP | REVERSAL_PUMP`.

### `survey`
```json
{ "type": "survey", "ts_ms": 1792091700000, "equity": 10000,
  "timeframes": ["1d", "4h", "1h"], "symbols_scanned": 150,
  "symbols_with_data": 148,
  "budget": { "total_units": 6, "long_units": 6, "short_units": 0,
              "total_notional": 4200, "total_risk": 300, "positions": 2,
              "rejected": 11 },
  "setups": [ {
    "symbol": "SOLUSDT", "direction": 1, "score": 71, "close": 106.1,
    "atr": 3.2, "atr_pct": 0.030, "adx": 28.4, "rsi": 61.2,
    "extension_up_atr": 0.42, "group": "g1", "accepted": true,
    "reject_reason": null,
    "plan": { "entry": 106.1, "stop": 99.7, "unit_size": 31.2,
              "unit_notional": 3310, "units": 1, "total_notional": 3310,
              "total_risk": 100, "pyramid_levels": [107.7, 109.3, 110.9],
              "exit_level": 101.4, "notional_capped": false }
  } ],
  "exit_signals": [ { "symbol": "ADAUSDT", "close": 0.61,
                      "exit_long": true, "exit_short": false,
                      "exit_low_10": 0.615, "adx": 18.2 } ] }
```
`plan` may be `null` for rejected setups.

---

## REST endpoints

| method | path | returns |
|---|---|---|
| GET | `/api/health` | `{"status":"ok","uptime_sec":N}` — for liveness checks |
| GET | `/api/config` | the `config` object from `hello` |
| GET | `/api/universe` | `{"symbols":[{"symbol","last_price","quote_volume_24h","change_24h_pct"}]}` |
| GET | `/api/flow` | the `flow` payload (one-shot) |
| GET | `/api/alerts?limit=200` | `{"alerts":[<alert payloads, newest first>]}` |
| DELETE | `/api/alerts` | clears the buffer, returns `{"cleared":N}` |
| GET | `/api/screen` | the `screen` payload (last cached result) |
| POST | `/api/screen/refresh` | triggers a new screen pass; returns `202` |
| GET | `/api/survey` | the `survey` payload (last cached result) |
| POST | `/api/survey/refresh` | triggers a new survey; returns `202` |

## Frontend requirements

- **Vite + React + TypeScript.** Use `pnpm`.
- Dev server on port **5173**, proxying `/api` → `http://127.0.0.1:8787`
  (configure in `vite.config.ts` so `EventSource('/api/events')` works in dev).
- Build output to `../backend/web/dist` (set `build.outDir`), because the Go
  binary embeds and serves it.
- **No heavy dependencies** — no chart library, no UI kit, no state library.
  Plain React hooks + hand-written CSS. `react`, `react-dom`, `typescript`,
  `vite`, `@vitejs/plugin-react` only.
- Dark theme, monospace numerics (`font-variant-numeric: tabular-nums`).
- Layout:
  1. **Header** — title, live/offline pill, uptime, symbols tracked, alerts count.
  2. **Stat strip** — pumps, dumps, reversals, CRITICAL count, universe 24h volume, scan count.
  3. **Live alerts feed** (prominent, left/main column) — newest first, max 100
     kept in memory. Each card: time, symbol, kind badge, severity badge, score,
     price, the 10s move, flow size and multiple, and the `reasons` list.
     New cards should flash/highlight briefly. Keep a "pause" toggle that stops
     auto-inserting new alerts (so you can read one).
  4. **Flow leaderboard** (right column) — top rows with an inline horizontal
     bar sized by `burst_multiple`, coloured green/red by sign of `change_10s`.
  5. **Pump/dump screen** (full width below) — sortable table; clicking a row
     filters/highlights. Columns: symbol, kind, score, price, 5m, 15m, 1h,
     volume ratio, taker %, close position, 5m volume, 24h volume.
  6. **Turtle survey** (collapsible) — setups with plan details and the budget summary.
- Formatting helpers: `pct(f)` → `+4.61%`, `usd(n)` → `$76.4K` / `$1.8M`, `px(n)`
  → adaptive decimals for small-cap prices (e.g. `0.047700`, `82,490.00`).
- Must handle an offline backend gracefully: show a red "disconnected" pill and
  keep retrying `EventSource` (it auto-reconnects, but the pill must reflect state).
- Responsive down to ~1100px wide; below that, stack the columns.

---

# Charting & indicators (added)

## `GET /api/klines?symbol=BTCUSDT&interval=1h&limit=200`

Returns OHLCV bars plus every technical indicator the backend already computes,
so the UI never re-implements indicator math. `interval` ∈ `1m 5m 15m 1h 4h 1d`.
`limit` is clamped to 2..1000.

```json
{
  "type": "klines",
  "symbol": "BTCUSDT",
  "interval": "1h",
  "ts_ms": 1792095000000,
  "bars": [
    { "t": 1792095000000, "o": 82400.1, "h": 82500.0, "l": 82300.0,
      "c": 82450.0, "v": 12.3, "q": 1013000.0, "n": 420, "tb": 0.55 }
  ],
  "indicators": {
    "donchian20_upper": [null, null, 82500.0],
    "donchian20_lower": [null, null, 81800.0],
    "donchian55_upper": [null],
    "donchian55_lower": [null],
    "ema20":  [null, 82410.2],
    "ema50":  [null, 82390.7],
    "sma200": [null],
    "atr20":     [null, 412.5],
    "atr_pct":   [null, 0.005],
    "volume_median_20": [null, 890000.0],
    "adx14":    [null, 24.1],
    "plus_di":  [null, 28.0],
    "minus_di": [null, 19.5],
    "rsi14":    [null, 61.2]
  },
  "marks": [
    { "t": 1792091400000, "kind": "PUMP", "severity": "CRITICAL",
      "score": 77, "flow_quote": 76400, "burst_multiple": 16.1 }
  ],
  "latest": {
    "close": 82450.0, "atr20": 412.5, "atr_pct": 0.005, "adx14": 24.1,
    "rsi14": 61.2, "donchian20_upper": 82500.0, "donchian20_lower": 81800.0,
    "volume_ratio": 1.14, "trend": "up"
  }
}
```

**Alignment rule (important):** every array in `indicators` is index-aligned
with `bars` — same length, with `null` wherever the indicator is not yet
defined (warm-up periods). Never emit a shorter array; the chart maps values to
bars by index.

`bars[].t` is the bar **open time** in epoch ms. `bars[].tb` is the taker-buy
share (0-1) derived from the kline's taker-buy quote volume. The **final bar is
the still-forming candle** and is included (a chart should show it); indicators
on it are provisional. The backend's *trading* logic explicitly excludes it.

`marks` are recent onset alerts for this symbol, so they can be drawn as
markers on the chart. May be empty.

## `GET /api/tools` — indicator + metric glossary

Everything the UI needs to render **detailed tooltips** without hard-coding
copy. Returns a map of key → title, formula, plain-English meaning, and how to
read it.

```json
{
  "type": "tools",
  "indicators": {
    "atr20": {
      "title": "ATR(20) — Average True Range, the Turtles' N",
      "formula": "Wilder-smoothed mean of True Range over 20 bars",
      "meaning": "Average distance price travels per bar, in price units.",
      "reading": "Sizes positions and stops. A 2N stop is twice this.",
      "units": "price"
    },
    "adx14": { "title": "...", "formula": "...", "meaning": "...", "reading": "...", "units": "0-100" },
    "rsi14": { "...": "..." },
    "donchian20_upper": { "...": "..." },
    "burst_multiple": { "...": "..." }
  },
  "metrics": {
    "burst_multiple": {
      "title": "Flow burst multiple",
      "meaning": "Volume in the last window divided by this symbol's own baseline for that window.",
      "reading": "8x is the alert gate. 20x+ is exceptional.",
      "units": "x"
    },
    "coverage": { "...": "..." },
    "taker_buy_ratio": { "...": "..." },
    "score": { "...": "..." },
    "severity": { "...": "..." },
    "extension_up_atr": { "...": "..." },
    "volume_ratio": { "...": "..." }
  }
}
```

The UI must fall back gracefully if a key is missing (`?? null`) and render the
raw key name rather than crashing.

## UI requirements (charting & interactivity)

- **Charting**: use `lightweight-charts` (TradingView). Do NOT hand-roll charts.
  Candlestick pane + volume histogram, plus indicator panes.
- **Clickable everything.** Specifically:
  - every symbol name anywhere (alerts, flow leaderboard, screen table, survey)
    selects that symbol and loads its chart
  - every timeframe chip (1m/5m/15m/1h/4h/1d) is clickable
  - every indicator chip toggles that overlay/pane on the chart
  - column headers sort (already), sortable columns show an affordance
  - alert cards expand to show all `metrics` keys
  - a selected thing must look selected (persistent highlight, not just hover)
- **Detailed tooltips** on: every indicator chip, every table column header,
  every metric label in an alert card, and every stat in the stat strip. Tooltips
  must be **rich** (title + meaning + reading + units) and driven by
  `/api/tools` where possible, falling back to local copy. Hover *and* keyboard
  focus must both show them. Tooltips must not overflow the viewport.
- Poll `/api/klines?symbol=<selected>&interval=<selected>` whenever the symbol or
  interval changes; refetch every 30s while the tab is visible.
- Keep it type-safe: `tsc -b` must pass and `pnpm build` must exit 0.
