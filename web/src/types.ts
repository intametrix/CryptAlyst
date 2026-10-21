/**
 * Wire types for the pumpdump backend.
 *
 * Source of truth: `API.md` (repository root). Everything mirrored here is
 * documented there — no invented fields. Fields the contract allows to be
 * absent or `null` are deliberately typed `?: T | null`: the UI must never
 * assume any of them are present, and every read site has to guard.
 */

/** Client-side connection state for the SSE stream (`onopen` / `onerror`). */
export type ConnectionState = 'connecting' | 'live' | 'offline';

/** `severity` ∈ WATCH | ALERT | CRITICAL (API.md). Unknown values tolerated. */
export type Severity = 'WATCH' | 'ALERT' | 'CRITICAL';

/**
 * `kind` — the known set, plus room for values the backend grows later.
 *
 * API.md documents PUMP | DUMP | REVERSAL_DUMP | REVERSAL_PUMP; the Turtle
 * detector added BREAKOUT, now split into BREAKOUT_LONG / BREAKOUT_SHORT. The
 * UI treats the list as open: anything it has not seen is prettified rather than
 * labelled "UNKNOWN".
 */
export type AlertKind =
  | 'PUMP'
  | 'DUMP'
  | 'REVERSAL_DUMP'
  | 'REVERSAL_PUMP'
  | 'BREAKOUT'
  | 'BREAKOUT_LONG'
  | 'BREAKOUT_SHORT';

/** Which model produced an alert. The feed mixes detectors. */
export type Detector = 'flow-onset' | 'pump-screen' | 'turtle-breakout' | (string & {});

/** Effective server config, from `hello.config` (also `GET /api/config`). */
export interface Config {
  market?: string | null;
  window_sec?: number | null;
  min_burst_multiple?: number | null;
  min_burst_z?: number | null;
  min_move_pct_10s?: number | null;
  min_flow_usd?: number | null;
  min_quote_volume_24h?: number | null;
  max_symbols?: number | null;
  cooldown_sec?: number | null;
  severity_min?: Severity | string | null;
}

/** `hello` — once on connect. */
export interface HelloMsg {
  type: 'hello';
  server_time_ms?: number | null;
  config?: Config | null;
}

/** `status` — every 2s. */
export interface StatusMsg {
  type: 'status';
  ts_ms?: number | null;
  uptime_sec?: number | null;
  symbols_tracked?: number | null;
  baselines_ready?: number | null;
  messages?: number | null;
  scans?: number | null;
  candidates?: number | null;
  alerts?: number | null;
  universe_volume_24h?: number | null;
  screen_generated_ms?: number | null;
  survey_generated_ms?: number | null;
  stream_connected?: boolean | null;
  stream_up?: boolean | null;
  stream_messages?: number | null;
  stream_streams?: number | null;
  stream_reconnects?: number | null;
  truncated?: number | null;
  clients?: number | null;
}

/** One row of the flow split (pumps / dumps). */
export interface FlowSplitRow extends FlowRow {
  /** The 24h change, so a bounce inside a downtrend is not read as a pump. */
  change_24h_pct?: number | null;
}

/**
 * The `split`-shaped flow payload: `pumps` and `dumps` are the two panels, and
 * the `dropped_*` counters are what explain an empty one.
 */
export interface FlowSplitMsg {
  type?: string | null;
  ts_ms?: number | null;
  split?: boolean | null;
  window_sec?: number | null;
  symbols?: number | null;
  pumps?: FlowSplitRow[] | null;
  dumps?: FlowSplitRow[] | null;
  pump_count?: number | null;
  dump_count?: number | null;
  dropped_thin?: number | null;
  dropped_stale?: number | null;
  dropped_quiet?: number | null;
  min_move_pct?: number | null;
  min_flow_usd?: number | null;
  max_stale_ms?: number | null;
}

/** One row of the flow leaderboard. */
export interface FlowRow {
  symbol?: string | null;
  last_price?: number | null;
  flow_quote?: number | null;
  baseline_quote?: number | null;
  burst_multiple?: number | null;
  burst_z?: number | null;
  change_10s?: number | null;
  change_30s?: number | null;
  change_60s?: number | null;
  coverage?: number | null;
  trades_10s?: number | null;
  taker_buy_ratio?: number | null;
  spread_pct?: number | null;
  book_depth_usd?: number | null;
  quote_volume_24h?: number | null;
  updated_ms?: number | null;
}

/** `flow` — every 1s, `rows` sorted by `flow_quote` descending. */
export interface FlowMsg {
  type: 'flow';
  ts_ms?: number | null;
  window_sec?: number | null;
  rows?: FlowRow[] | null;
}

/**
 * `alert.metrics` — "Any `metrics` key may be absent or `null`; the UI must
 * tolerate that." Hence every field is optional and nullable.
 */
export interface AlertMetrics {
  window_sec?: number | null;
  flow_quote?: number | null;
  burst_multiple?: number | null;
  burst_z?: number | null;
  change_10s?: number | null;
  change_30s?: number | null;
  change_60s?: number | null;
  sigma_multiple?: number | null;
  coverage?: number | null;
  trades_10s?: number | null;
  trades_multiple?: number | null;
  acceleration?: number | null;
  range_position?: number | null;
  spread_pct?: number | null;
  book_depth_usd?: number | null;
  quote_volume_24h?: number | null;
  latency_ms?: number | null;
  penalty?: number | null;
  agg_confirmed?: boolean | null;
  agg_buy_quote?: number | null;
  agg_sell_quote?: number | null;
  agg_buy_ratio?: number | null;
  agg_trades?: number | null;
  agg_largest_trade_usd?: number | null;
}

/** `alert.components` — loose bag; keys vary by detector. */
export interface AlertComponents {
  flow?: number | null;
  speed?: number | null;
  aggression?: number | null;
  quality?: number | null;
  /** Breakout detectors send their own component keys. */
  [key: string]: number | null | undefined;
}

/**
 * `alert` — on occurrence.
 *
 * The feed mixes three detectors, so `components` and `metrics` keys vary per
 * message: the flow detector sends burst/flow keys, the screen sends return and
 * volume keys, the Turtle detector sends ATR/ADX/extension keys. Nothing here
 * assumes a fixed set — the card renders whatever keys are present.
 */
export interface AlertMsg {
  type: 'alert';
  event_id?: string | null;
  ts_ms?: number | null;
  market?: string | null;
  symbol?: string | null;
  kind?: AlertKind | string | null;
  severity?: Severity | string | null;
  score?: number | null;
  price?: number | null;
  reasons?: string[] | null;
  components?: AlertComponents | null;
  metrics?: AlertMetrics | null;
  /** Which detector produced this alert: flow-onset | pump-screen | turtle-breakout. */
  detector?: Detector | string | null;
  /** True for batch detectors, whose signal may be older than the alert. */
  batch?: boolean | null;
  /** For batch alerts: when the underlying snapshot was taken (epoch ms). */
  snapshot_ms?: number | null;
  /** One-line human summary of the signal. */
  summary?: string | null;
  /**
   * The server's resolved colour for this event. Authoritative: the UI must use
   * it rather than mapping kind to a constant of its own.
   */
  color?: string | null;
  /** The server's letter for this kind (P/D/R/B), when it sends one. */
  letter?: string | null;
  /** The server's arrow direction: +1 up, −1 down. */
  direction?: number | null;
}

/** One row of the market-wide screen. */
export interface ScreenRow {
  symbol?: string | null;
  kind?: AlertKind | string | null;
  score?: number | null;
  price?: number | null;
  ret_5m?: number | null;
  ret_15m?: number | null;
  ret_60m?: number | null;
  ret_24h?: number | null;
  vol_ratio?: number | null;
  vol_z?: number | null;
  recent_quote_volume?: number | null;
  baseline_quote_volume?: number | null;
  quote_volume_24h?: number | null;
  trades_ratio?: number | null;
  taker_buy_ratio?: number | null;
  close_position?: number | null;
  range_pct?: number | null;
  atr_pct?: number | null;
  from_24h_high?: number | null;
  from_24h_low?: number | null;
  bars_since_extreme?: number | null;
  reasons?: string[] | null;
}

/** `screen` — every 60s, `rows` sorted by `score` descending. */
export interface ScreenMsg {
  type: 'screen';
  ts_ms?: number | null;
  symbols_scanned?: number | null;
  rows?: ScreenRow[] | null;
}

/** `survey.budget`. */
export interface Budget {
  total_units?: number | null;
  long_units?: number | null;
  short_units?: number | null;
  total_notional?: number | null;
  total_risk?: number | null;
  positions?: number | null;
  rejected?: number | null;
}

/** `survey.setups[].plan` — may be `null` for rejected setups. */
export interface SetupPlan {
  entry?: number | null;
  stop?: number | null;
  unit_size?: number | null;
  unit_notional?: number | null;
  units?: number | null;
  total_notional?: number | null;
  total_risk?: number | null;
  pyramid_levels?: number[] | null;
  exit_level?: number | null;
  notional_capped?: boolean | null;
}

/** `survey.setups[]`. */
export interface Setup {
  symbol?: string | null;
  /** 1 = long, -1 = short. */
  direction?: number | null;
  score?: number | null;
  close?: number | null;
  atr?: number | null;
  atr_pct?: number | null;
  adx?: number | null;
  rsi?: number | null;
  extension_up_atr?: number | null;
  group?: string | null;
  accepted?: boolean | null;
  reject_reason?: string | null;
  plan?: SetupPlan | null;
}

/** `survey.exit_signals[]`. */
export interface ExitSignal {
  symbol?: string | null;
  close?: number | null;
  exit_long?: boolean | null;
  exit_short?: boolean | null;
  exit_low_10?: number | null;
  adx?: number | null;
}

/** `survey` — every 5 min. */
export interface SurveyMsg {
  type: 'survey';
  ts_ms?: number | null;
  equity?: number | null;
  timeframes?: string[] | null;
  symbols_scanned?: number | null;
  symbols_with_data?: number | null;
  budget?: Budget | null;
  setups?: Setup[] | null;
  exit_signals?: ExitSignal[] | null;
}

/** One row of the `heat` message (`type: "heat"`), also `GET /api/heat`. */
export interface HeatRow {
  symbol?: string | null;
  /** 0-100 composite. Below 25 the UI draws nothing. */
  heat?: number | null;
  /** Each component is 0-1: flow, move, trend, level, range, … */
  components?: Record<string, number> | null;
  /** Short human strings, e.g. "16.1x flow burst". */
  reasons?: string[] | null;
}

/** One row of a `ticker` frame — a per-tick price push (~4 Hz). */
export interface TickerRow {
  symbol?: string | null;
  ts_ms?: number | null;
  price?: number | null;
  bid?: number | null;
  ask?: number | null;
  /** Rolling 24h quote volume: a cumulative counter, so bars take its delta. */
  quote_vol_24h?: number | null;
  price_change_pct_24h?: number | null;
}

/**
 * `ticker` — pushed at ~4 Hz (coalesced at 250 ms) for ~25 symbols per frame.
 * Also the body of `GET /api/ticker?symbol=X`.
 */
export interface TickerMsg {
  type: 'ticker';
  ts_ms?: number | null;
  rows?: TickerRow[] | null;
}

/** `heat` — pushed every ~2s. Also the body of `GET /api/heat`. */
export interface HeatMsg {
  type: 'heat';
  ts_ms?: number | null;
  rows?: HeatRow[] | null;
}

/** Every message on `/api/events`, discriminated by `type`. */
export type StreamMsg =
  | HelloMsg
  | StatusMsg
  | FlowMsg
  | AlertMsg
  | ScreenMsg
  | SurveyMsg
  | HeatMsg
  | TickerMsg;

/* ------------------------------------------------------------------ charting */

/** Chart intervals documented in API.md ("Charting & indicators (added)"). */
/**
 * Chart intervals.
 *
 * `1s` is first-class: the backend serves one-second candles (the exchange caps
 * such a response at 1000 bars, which is why the finer zoom presets use `1s` and
 * the coarser ones step up to `1m`).
 */
export type Interval = '1s' | '1m' | '5m' | '15m' | '1h' | '4h' | '1d';

/** Selectable intervals, in ascending timeframe order. */
export const INTERVALS: readonly Interval[] = ['1s', '1m', '5m', '15m', '1h', '4h', '1d'];

/** True when `v` is one of the documented intervals. */
export function isInterval(v: unknown): v is Interval {
  return typeof v === 'string' && (INTERVALS as readonly string[]).includes(v);
}

/** One OHLCV bar from `/api/klines`. `t` is the bar OPEN time (epoch ms). */
export interface ChartBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  q: number;
  n: number;
  tb: number;
}

/**
 * `indicators.*` — every array is index-aligned with `bars` and holds `null`
 * during warm-up (API.md "Alignment rule"). Keys the backend may add later are
 * reachable through the index signature.
 */
export interface ChartIndicatorMap {
  donchian20_upper?: Array<number | null> | null;
  donchian20_lower?: Array<number | null> | null;
  donchian55_upper?: Array<number | null> | null;
  donchian55_lower?: Array<number | null> | null;
  ema20?: Array<number | null> | null;
  ema50?: Array<number | null> | null;
  sma200?: Array<number | null> | null;
  atr20?: Array<number | null> | null;
  atr_pct?: Array<number | null> | null;
  volume_median_20?: Array<number | null> | null;
  adx14?: Array<number | null> | null;
  plus_di?: Array<number | null> | null;
  minus_di?: Array<number | null> | null;
  rsi14?: Array<number | null> | null;
  [key: string]: Array<number | null> | null | undefined;
}

/**
 * `klines.marks[]` — a detected event, drawn as a letter on its bar.
 *
 * `t` is already snapped server-side to a bar open time that exists in `bars`,
 * so it can be handed to the marker API directly; `letter` is the glyph (P pump,
 * D dump, R reversal, B breakout) and `direction` is +1 up / −1 down.
 */
export interface ChartMark {
  t?: number | null;
  letter?: string | null;
  label?: string | null;
  kind?: AlertKind | string | null;
  direction?: number | null;
  /** The server's own colour for this mark; preferred when present. */
  color?: string | null;
  /** Which detector produced the event, when the server reports it. */
  detector?: Detector | string | null;
  /** One-line description, when the server sends one. */
  summary?: string | null;
  severity?: Severity | string | null;
  score?: number | null;
  flow_quote?: number | null;
  burst_multiple?: number | null;
}

/** Structural shape of a bar, used where the full `ChartBar` is overkill. */
export interface SafeBarLike {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  q: number;
  n: number;
  tb: number;
}

/** `klines.latest` — a loose bag of "current" values; every key optional. */
export interface ChartLatest {
  close?: number | null;
  atr20?: number | null;
  atr_pct?: number | null;
  adx14?: number | null;
  rsi14?: number | null;
  plus_di?: number | null;
  minus_di?: number | null;
  donchian20_upper?: number | null;
  donchian20_lower?: number | null;
  donchian55_upper?: number | null;
  donchian55_lower?: number | null;
  volume_ratio?: number | null;
  trend?: string | null;
  [key: string]: unknown;
}

/** `GET /api/klines` response. */
export interface KlinesMsg {
  type: 'klines';
  symbol?: string | null;
  interval?: string | null;
  ts_ms?: number | null;
  bars?: ChartBar[] | null;
  indicators?: ChartIndicatorMap | null;
  marks?: ChartMark[] | null;
  latest?: ChartLatest | null;
  /**
   * This symbol's persisted alerts, newest first.
   *
   * The backend returns them inside the klines payload so one request on
   * selection yields both bars and events. They are consumed here rather than
   * fetched separately, which is what lets the feed show history immediately
   * instead of starting empty and waiting for the next live alert.
   */
  alerts?: AlertMsg[] | null;
}

/* ---------------------------------------------------------------- drilldown */

/**
 * `GET /api/symbol` — the consolidated drill-down for one symbol.
 *
 * Every section is individually absent or `null` — that is normal, not an error.
 * `why_no_trade` is the important field: when there is no `trade`, that string
 * names the exact gate that refused the setup, and it IS the answer.
 */
export interface SymbolDetailMsg {
  type?: string | null;
  symbol?: string | null;
  ts_ms?: number | null;
  context?: {
    last_price?: number | null;
    quote_volume_24h?: number | null;
    change_24h_pct?: number | null;
    high_price?: number | null;
    low_price?: number | null;
    [key: string]: unknown;
  } | null;
  flow?: FlowRow | null;
  heat?: { heat?: number | null; components?: Record<string, number> | null; reasons?: string[] | null } | null;
  screen?: ScreenRow | null;
  setup?: Setup | null;
  /** The destination: the trade plan, or `null` when there is no trade. */
  trade?: SetupPlan | null;
  /** The gate that refused the setup. Present exactly when `trade` is null. */
  why_no_trade?: string | null;
  /** Flow metrics flattened for display. Keys vary. */
  metrics?: Record<string, unknown> | null;
  /** This symbol's recent alerts, newest first. */
  alerts?: AlertMsg[] | null;
  links?: { chart?: string | null; live?: string | null; alerts?: string | null } | null;
}

/* -------------------------------------------------------------------- tools */

/** One `/api/tools` glossary entry. */
export interface ToolEntry {
  title?: string | null;
  formula?: string | null;
  meaning?: string | null;
  reading?: string | null;
  units?: string | null;
}

/** `GET /api/tools` — indicator + metric glossary. */
export interface ToolsMsg {
  type?: string | null;
  indicators?: Record<string, ToolEntry | null> | null;
  metrics?: Record<string, ToolEntry | null> | null;
}

/** Stable identity for an alert: `event_id` when present, else a derived key. */
export function alertKey(a: AlertMsg): string {
  if (typeof a.event_id === 'string' && a.event_id !== '') return a.event_id;
  return `${a.ts_ms ?? 'x'}-${a.symbol ?? '?'}-${a.kind ?? '?'}`;
}

/**
 * Turn any raw enum-ish value into a readable label.
 *
 * The dashboard must never show "UNKNOWN", "undefined", "null" or an empty
 * badge: an unrecognised value degrades to a pretty version of itself, so a new
 * backend enum (`BREAKOUT_LONG`, `flow-onset`, something nobody has thought of
 * yet) reads as "Breakout Long" / "Flow Onset" without a frontend release.
 *
 * `_`, `-` and `.` become spaces, words are title-cased, and anything
 * unrenderable becomes `fallback` (never an empty string).
 */
export function prettifyEnum(v: unknown, fallback = 'Unclassified'): string {
  if (typeof v !== 'string' && typeof v !== 'number') return fallback;
  const raw = String(v).trim();
  if (raw === '') return fallback;
  const cleaned = raw.replace(/[_.\-]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (cleaned === '') return fallback;
  const titled = cleaned
    .split(' ')
    .map((word) => (word.length === 0 ? word : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()))
    .join(' ');
  return titled === '' ? fallback : titled;
}

/** The raw, normalised enum word (`BREAKOUT_LONG`) or `''` when unusable. */
export function enumWord(v: unknown): string {
  if (typeof v !== 'string' && typeof v !== 'number') return '';
  return String(v).trim().toUpperCase().replace(/[^A-Z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
}

/** Coarse style bucket for a `kind`; unknown values get the neutral bucket. */
export type KindStyle =
  | 'pump'
  | 'dump'
  | 'reversal-pump'
  | 'reversal-dump'
  | 'breakout-long'
  | 'breakout-short'
  | 'breakout'
  | 'other';

/** Coarse style bucket for a `severity`. */
export type SeverityStyle = 'watch' | 'alert' | 'critical' | 'other';

/**
 * Map `kind` onto a style bucket.
 *
 * Matching is done on the normalised word, so `breakout_long`, `BREAKOUT-LONG`
 * and `BREAKOUT_LONG` all land in the same bucket, and a bare `BREAKOUT` (the
 * older Turtle value) gets its own neutral colour.
 */
export function kindStyle(v: unknown): KindStyle {
  const word = enumWord(v);
  switch (word) {
    case 'PUMP':
      return 'pump';
    case 'DUMP':
      return 'dump';
    case 'REVERSAL_PUMP':
      return 'reversal-pump';
    case 'REVERSAL_DUMP':
      return 'reversal-dump';
    case 'BREAKOUT':
      return 'breakout';
    case 'BREAKOUT_LONG':
      return 'breakout-long';
    case 'BREAKOUT_SHORT':
      return 'breakout-short';
    default:
      // Covers new spellings the backend may grow (BREAKOUT_UP, BREAKOUT_BEAR, …).
      const bullish = ['LONG', 'UP', 'BULL', 'HIGH'].some((w) => word.includes(w));
      const bearish = ['SHORT', 'DOWN', 'BEAR', 'LOW'].some((w) => word.includes(w));
      if (word.startsWith('BREAKOUT')) {
        if (bearish && !bullish) return 'breakout-short';
        if (bullish && !bearish) return 'breakout-long';
        return 'breakout';
      }
      if (word.includes('REVERSAL') && word.includes('PUMP')) return 'reversal-pump';
      if (word.includes('REVERSAL') && word.includes('DUMP')) return 'reversal-dump';
      if (word.includes('PUMP')) return 'pump';
      if (word.includes('DUMP')) return 'dump';
      return 'other';
  }
}

/** Map `severity` onto a style bucket; unknown values are neutral. */
export function severityStyle(v: unknown): SeverityStyle {
  switch (enumWord(v)) {
    case 'WATCH':
      return 'watch';
    case 'ALERT':
      return 'alert';
    case 'CRITICAL':
      return 'critical';
    default:
      return 'other';
  }
}

/** Readable label for a detector, e.g. `turtle-breakout` → "Turtle Breakout". */
export function detectorLabel(v: unknown): string {
  return prettifyEnum(v, 'Unknown detector');
}
