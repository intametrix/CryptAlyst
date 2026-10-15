/**
 * Pure mapping layer between the `/api/klines` wire payload and the shapes
 * `lightweight-charts` wants.
 *
 * Everything here is deliberately free of React and of the chart library so it
 * can be reasoned about (and exercised outside the browser) on its own. The one
 * rule that matters most is API.md's **alignment rule**:
 *
 *   every array in `indicators` is index-aligned with `bars` — same length,
 *   with `null` wherever the indicator is not yet defined
 *
 * A defensive client therefore maps *by index* and drops nulls rather than
 * plotting them as `0`, and tolerates a shorter array than `bars` (an older or
 * buggier backend) without ever emitting a bogus zero.
 */

import type { UTCTimestamp } from 'lightweight-charts';
import { toNum } from './format';
import { eventIdentity, type ColorCanon } from './colors';
import { resolvePalette } from './palette';
import type { ChartBar, ChartIndicatorMap, ChartMark, ToolEntry } from './types';

/* --------------------------------------------------------------- bar mapping */

/** Guarded OHLCV bar. */
export interface SafeBar {
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

/** Lightweight-charts candlestick datum (seconds, per the library's `Time`). */
export interface CandlePoint {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** Lightweight-charts histogram datum. */
export interface VolumePoint {
  time: UTCTimestamp;
  value: number;
  color: string;
}

/** Lightweight-charts line datum — never carries a null/NaN value. */
export interface LinePoint {
  time: UTCTimestamp;
  value: number;
}

/** An indicator line that may be shorter than `bars`; never padded with zeros. */
export interface AlignedSeries {
  key: string;
  data: LinePoint[];
}

/**
 * Parse the `bars` array defensively: non-finite numbers become `0` only for
 * volume (an absent trade count is honestly `0`), while a bar missing a finite
 * open/high/low/close is dropped entirely — a candle with a `0` price would
 * wreck the chart's price scale.
 *
 * Bars are returned in ascending `t` order and de-duplicated, because
 * lightweight-charts requires strictly increasing times.
 */
export function parseBars(raw: unknown): SafeBar[] {
  if (!Array.isArray(raw)) return [];
  const out: SafeBar[] = [];
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue;
    const b = item as Partial<ChartBar> & Record<string, unknown>;
    const t = toNum(b.t);
    const o = toNum(b.o);
    const h = toNum(b.h);
    const l = toNum(b.l);
    const c = toNum(b.c);
    if (t === null || o === null || h === null || l === null || c === null) continue;
    const high = Math.max(h, o, c, l);
    const low = Math.min(l, o, c, h);
    out.push({
      t: Math.trunc(t),
      o,
      h: high,
      l: low,
      c,
      v: toNum(b.v) ?? 0,
      q: toNum(b.q) ?? 0,
      n: toNum(b.n) ?? 0,
      tb: toNum(b.tb) ?? 0.5,
    });
  }
  out.sort((a, b) => a.t - b.t);
  const deduped: SafeBar[] = [];
  for (const bar of out) {
    const prev = deduped[deduped.length - 1];
    if (prev !== undefined && prev.t === bar.t) {
      deduped[deduped.length - 1] = bar;
      continue;
    }
    deduped.push(bar);
  }
  return deduped;
}

/** Seconds since epoch, which is what lightweight-charts' `Time` expects. */
export function barTime(bar: SafeBar): UTCTimestamp {
  return Math.floor(bar.t / 1000) as UTCTimestamp;
}

export function toCandles(bars: readonly SafeBar[]): CandlePoint[] {
  return bars.map((b) => ({ time: barTime(b), open: b.o, high: b.h, low: b.l, close: b.c }));
}

export function volumeColor(bar: { o: number; c: number }): string {
  const palette = resolvePalette();
  return bar.c > bar.o ? palette.volumeUp : bar.c < bar.o ? palette.volumeDown : palette.volumeFlat;
}

export function toVolumes(bars: readonly SafeBar[]): VolumePoint[] {
  return bars.map((b) => ({
    time: barTime(b),
    value: b.v,
    color: volumeColor(b),
  }));
}

/* --------------------------------------------------------- indicator mapping */

/**
 * Map one `indicators[key]` array onto the bars **by index**.
 *
 * - a value is emitted only when it is a finite number
 * - `null`, `undefined`, `NaN`, and `0`-looking warm-up holes are skipped, so
 *   nothing is ever plotted as a fake zero
 * - an array shorter than `bars` simply stops early (no padding, no crash)
 * - an array longer than `bars` is truncated to the bar count
 */
export function alignIndicator(key: string, values: unknown, bars: readonly SafeBar[]): AlignedSeries {
  const data: LinePoint[] = [];
  if (!Array.isArray(values)) return { key, data };
  const n = Math.min(values.length, bars.length);
  for (let i = 0; i < n; i += 1) {
    const v = toNum(values[i]);
    if (v === null) continue;
    data.push({ time: barTime(bars[i]), value: v });
  }
  return { key, data };
}

/** Map every requested indicator key at once, in the caller's order. */
export function alignIndicators(
  keys: readonly string[],
  indicators: ChartIndicatorMap | null | undefined,
  bars: readonly SafeBar[],
): AlignedSeries[] {
  const map = indicators ?? {};
  return keys.map((key) => alignIndicator(key, map[key], bars));
}

/* ------------------------------------------------------------- marker mapping */

/** A marker, in the shape `lightweight-charts` wants plus its own details. */
export interface ChartMarker {
  time: UTCTimestamp;
  position: 'aboveBar' | 'belowBar' | 'inBar';
  shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown';
  color: string;
  /** The glyph drawn on the chart: P, D, R or B. */
  text: string;
  letter: string;
  /** Human title, e.g. "Dump detected" — shown in the hover tooltip. */
  label: string;
  kind: string;
  severity: string;
  direction: 1 | -1;
  /** Which detector produced the event, or `null` when the server omits it. */
  detector: string | null;
  score: number | null;
  flowQuote: number | null;
  burstMultiple: number | null;
  /**
   * The bar's low and high.
   *
   * The HTML badge layer converts these to pixels to anchor a badge to the
   * wick — the library's own markers do that internally, but they cannot carry a
   * text colour, so the badge is rendered outside the canvas.
   */
  low: number;
  high: number;
}

/* ---------------------------------------------------------------- markers */

/** Human title for a letter, used in the legend and the hover tooltip. */
export function markTitle(letter: string): string {
  switch (letter.toUpperCase()) {
    case 'P':
      return 'Pump';
    case 'D':
      return 'Dump';
    case 'R':
      return 'Reversal';
    case 'B':
      return 'Breakout';
    default:
      return 'Event';
  }
}

/** Every letter the legend should explain, in a fixed order. */
export const MARK_LETTERS: readonly { letter: string; title: string; tone: string }[] = [
  { letter: 'P', title: 'Pump', tone: 'up' },
  { letter: 'D', title: 'Dump', tone: 'down' },
  { letter: 'R', title: 'Reversal', tone: 'rev' },
  { letter: 'B', title: 'Breakout', tone: 'info' },
];

/**
 * Marker colours come from the shared palette (`src/palette.ts`, defined as CSS
 * custom properties) and never from a value re-authored here — with one
 * exception: when the backend sends an explicit `color` for a mark, that wins,
 * because the server knows which detector produced the event and stays
 * authoritative about how it should look.
 */

/**
 * Snap each mark to the bar it belongs to and build the marker the library wants.
 *
 * `marks[].t` is a bar open time the backend has already snapped, so the direct
 * lookup below is the common path; the binary search is the fallback for a
 * payload that is not perfectly aligned. Marks outside the loaded window are
 * dropped, and marks sharing a bar are kept (the server dedupes per bar *and*
 * kind, so a bar can legitimately carry both a P and a D) — the CRITICAL one
 * wins only when two marks collide on the same bar *and* the same letter.
 */
export function toMarkers(marks: unknown, bars: readonly SafeBar[], canon: ColorCanon | null = null): ChartMarker[] {
  if (!Array.isArray(marks) || bars.length === 0) return [];
  const bySlot = new Map<string, ChartMarker>();
  const severityRank = (m: ChartMarker): number =>
    m.color.includes('ff1f') || m.color.includes('0fff') || m.color.includes('ff95') || m.color.includes('2f6d') ? 2 : 1;

  for (const raw of marks) {
    if (typeof raw !== 'object' || raw === null) continue;
    const mark = raw as ChartMark;
    const t = toNum(mark.t);
    if (t === null) continue;
    const time = snapToBar(t, bars);
    // The bar the mark landed on, for the badge anchor.
    const anchorBar = time === null ? null : bars.find((b) => Math.floor(b.t / 1000) === time) ?? null;
    if (time === null) continue;

    const kind = typeof mark.kind === 'string' ? mark.kind : '';
    const severity = typeof mark.severity === 'string' ? mark.severity.toUpperCase() : '';
    // One resolution path for every surface: the payload's own colour and letter
    // when present, the canonical palette otherwise. Nothing is re-derived here,
    // which is what keeps the marker and the feed badge identical.
    const identity = eventIdentity({
      kind,
      severity,
      color: mark.color,
      letter: mark.letter,
      direction: mark.direction,
      canon,
    });
    const letter = identity.letter;
    const direction = identity.direction;
    // The glyph is the server's arrow for this kind, so the marker and the badge
    // point the same way.
    const shape: ChartMarker['shape'] =
      identity.glyph === 'arrowDown' ? 'arrowDown' : identity.glyph === 'arrowUp' ? 'arrowUp' : 'circle';
    const color = identity.color;

    const candidate: ChartMarker = {
      time,
      low: anchorBar?.l ?? 0,
      high: anchorBar?.h ?? 0,
      position: direction === 1 ? 'belowBar' : 'aboveBar',
      shape,
      color,
      // The letter is the whole point: one glyph, no wrapping, no overlap.
      text: letter,
      letter,
      label:
        typeof mark.label === 'string' && mark.label.trim() !== ''
          ? mark.label
          : typeof mark.summary === 'string' && mark.summary.trim() !== ''
            ? mark.summary
            : `${markTitle(letter)} detected`,
      kind,
      severity,
      direction,
      detector:
        typeof mark.detector === 'string' && mark.detector.trim() !== ''
          ? mark.detector
          : typeof mark.kind === 'string' && mark.kind !== ''
            ? null
            : null,
      score: toNum(mark.score),
      flowQuote: toNum(mark.flow_quote),
      burstMultiple: toNum(mark.burst_multiple),
    };

    const slot = `${time}:${letter}`;
    const existing = bySlot.get(slot);
    if (existing === undefined || severityRank(candidate) > severityRank(existing)) {
      bySlot.set(slot, candidate);
    }
  }

  return [...bySlot.values()].sort((a, b) => a.time - b.time || a.letter.localeCompare(b.letter));
}

/**
 * Snap each mark to the bar it belongs to.
 *
 * `marks[].t` is an alert timestamp, not necessarily a bar open time, so the
 * mark is attached to the **latest bar whose open time is ≤ t** — exactly where
 * the onset would have appeared. Marks outside the loaded window are dropped;
 * marks sharing a bar are collapsed (the library wants one marker per time), and
 * a CRITICAL mark wins the slot.
 */
/**
 * Latest bar open time at or before `tMs`, or `null` if `tMs` precedes every bar.
 *
 * The backend already snaps `marks[].t` to a bar, so this is a fallback for a
 * payload that is not perfectly aligned. Unaligned marker times are the classic
 * reason markers silently vanish from the chart.
 */
export function snapToBar(tMs: number, bars: readonly SafeBar[]): UTCTimestamp | null {
  if (bars.length === 0) return null;
  if (tMs < bars[0].t) return null;
  let lo = 0;
  let hi = bars.length - 1;
  let best = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].t <= tMs) {
      best = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return barTime(bars[best]);
}

/* -------------------------------------------------------------- price format */

/** `{ precision, minMove }` for the price scale, derived from the actual prices. */
export function priceFormatFor(bars: readonly SafeBar[]): { precision: number; minMove: number } {
  let minAbs = Number.POSITIVE_INFINITY;
  let maxAbs = 0;
  for (const b of bars) {
    const a = Math.abs(b.c);
    if (a > 0 && a < minAbs) minAbs = a;
    if (a > maxAbs) maxAbs = a;
  }
  if (!Number.isFinite(minAbs)) return { precision: 2, minMove: 0.01 };

  // A tick for a $82,490 instrument is not a tick for a $0.00001234 one.
  let digits: number;
  if (maxAbs >= 1000) digits = 2;
  else if (maxAbs >= 100) digits = 3;
  else if (maxAbs >= 1) digits = 4;
  else if (minAbs >= 0.01) digits = 5;
  else if (minAbs >= 0.0001) digits = 6;
  else digits = 8;

  // Never show more decimals than the instrument's own tick size needs. The
  // value's decimal count is what matters: 0.0477 needs 5, not "2 digits for a
  // 0.04 instrument". Exponential notation extracts it exactly (1e-7 -> 7).
  let tickDigits = digits;
  const exponential = minAbs.toExponential(8);
  const exponent = Number(exponential.slice(exponential.indexOf('e') + 1));
  if (Number.isFinite(exponent) && exponent < 0) tickDigits = Math.min(8, -exponent);

  const precision = Math.max(digits, tickDigits);
  return { precision, minMove: Number(`1e-${precision}`) };
}

/* ---------------------------------------------------------------- statistics */

/** The `latest` bag plus a couple of values recomputed locally as a fallback. */
export interface LatestSummary {
  close: number | null;
  trend: string | null;
  volumeRatio: number | null;
  donchian20Upper: number | null;
  donchian20Lower: number | null;
}

export function summarizeLatest(latest: unknown, bars: readonly SafeBar[]): LatestSummary {
  const l = (typeof latest === 'object' && latest !== null ? latest : {}) as Record<string, unknown>;
  const last = bars.length > 0 ? bars[bars.length - 1] : null;
  const prev = bars.length > 1 ? bars[bars.length - 2] : null;
  let trend = typeof l.trend === 'string' ? l.trend : null;
  if (trend === null && last !== null && prev !== null) {
    trend = last.c > prev.c ? 'up' : last.c < prev.c ? 'down' : 'flat';
  }
  let volumeRatio = toNum(l.volume_ratio);
  if (volumeRatio === null && last !== null) {
    const prior = bars.slice(Math.max(0, bars.length - 21), bars.length - 1).map((b) => b.q);
    if (prior.length > 0) {
      const sorted = prior.slice().sort((a, b) => a - b);
      const mid = Math.floor(sorted.length / 2);
      const median = sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
      if (median > 0) volumeRatio = last.q / median;
    }
  }
  return {
    close: toNum(l.close) ?? (last !== null ? last.c : null),
    trend,
    volumeRatio,
    donchian20Upper: toNum(l.donchian20_upper),
    donchian20Lower: toNum(l.donchian20_lower),
  };
}

/* ------------------------------------------------------------ chip registry */

export type PaneId = 'price' | 'atr' | 'adx' | 'rsi' | 'volume';

/** One toggleable chart element. */
export interface IndicatorDef {
  /** Stable id used as the toggle key and the `localStorage` key suffix. */
  id: string;
  /** Chip label. */
  label: string;
  /** Where it is drawn. */
  pane: PaneId;
  /** `indicators.*` keys this chip plots, in draw order. */
  series: readonly string[];
  /** `/api/tools` glossary lookups, tried in order. */
  toolKeys: readonly string[];
  /** Wire series key -> canonical glossary key (see `GROUPS`). */
  glossaryKey: Readonly<Record<string, string>>;
  /** One-line chip subtitle, shown inside the tooltip. */
  blurb: string;
  /** Whether the chip starts enabled. */
  defaultOn: boolean;
  /** Local fallback glossary copy, used when /api/tools has no entry. */
  fallback: ToolEntry;
}

/**
 * The chips, each bound to the glossary keys that actually exist for it.
 *
 * `glossaryKey` maps a *series* key onto its canonical glossary entry, because
 * the wire series and the glossary are not one-to-one: the two Donchian lines
 * share one entry (`donchian_channel`), and `ema20` / `sma200` are documented as
 * `ema` / `sma`. Without this map a tooltip for `donchian20_upper` would fall
 * through to the raw key.
 */
const GROUPS: readonly {
  id: string;
  label: string;
  blurb: string;
  toolKeys: readonly string[];
  glossaryKey: Readonly<Record<string, string>>;
}[] = [
  {
    id: 'donchian20',
    label: 'Donchian 20',
    blurb: '20-bar high/low channel — the Turtles’ short breakout channel.',
    toolKeys: ['donchian_channel', 'donchian20_upper'],
    glossaryKey: { donchian20_upper: 'donchian_channel', donchian20_lower: 'donchian_channel' },
  },
  {
    id: 'donchian55',
    label: 'Donchian 55',
    blurb: '55-bar high/low channel — the Turtles’ long breakout channel.',
    toolKeys: ['donchian_channel', 'donchian55_upper'],
    glossaryKey: { donchian55_upper: 'donchian_channel', donchian55_lower: 'donchian_channel' },
  },
  {
    id: 'ema20',
    label: 'EMA 20',
    blurb: '20-bar exponential moving average — fast trend.',
    toolKeys: ['ema', 'ema20'],
    glossaryKey: { ema20: 'ema' },
  },
  {
    id: 'ema50',
    label: 'EMA 50',
    blurb: '50-bar exponential moving average — medium trend.',
    toolKeys: ['ema', 'ema50'],
    glossaryKey: { ema50: 'ema' },
  },
  {
    id: 'sma200',
    label: 'SMA 200',
    blurb: '200-bar simple moving average — the long-term regime line.',
    toolKeys: ['sma', 'sma200'],
    glossaryKey: { sma200: 'sma' },
  },
  {
    id: 'atr20',
    label: 'ATR 20',
    blurb: 'Average True Range over 20 bars — the Turtles’ N.',
    toolKeys: ['atr20', 'atr'],
    glossaryKey: { atr20: 'atr20' },
  },
  {
    id: 'adx14',
    label: 'ADX 14 + DI',
    blurb: 'Trend strength with +DI / −DI directional lines.',
    toolKeys: ['adx14', 'adx'],
    glossaryKey: { adx14: 'adx14', plus_di: 'plus_di', minus_di: 'minus_di' },
  },
  {
    id: 'rsi14',
    label: 'RSI 14',
    blurb: 'Relative Strength Index with 30/70 guide lines.',
    toolKeys: ['rsi14', 'rsi'],
    glossaryKey: { rsi14: 'rsi14' },
  },
];

/** Fallback copy for every chart element, so tooltips never depend on the API. */
const FALLBACKS: Record<string, ToolEntry> = {
  donchian20: {
    title: 'Donchian channel (20)',
    formula: 'highest high(20) and lowest low(20), plotted per bar',
    meaning: 'The price range of the last 20 bars. A close above the upper line is the Turtles’ 20-bar breakout.',
    reading: 'Breakouts of the upper line start longs; breaks of the lower line start shorts. Narrow channels mean compression.',
    units: 'price',
  },
  donchian55: {
    title: 'Donchian channel (55)',
    formula: 'highest high(55) and lowest low(55), plotted per bar',
    meaning: 'The price range of the last 55 bars — the slower, system-2 channel.',
    reading: 'A 55-bar breakout is a stronger, rarer signal than a 20-bar one. Mid-channel is the no-trade zone.',
    units: 'price',
  },
  ema20: {
    title: 'EMA(20)',
    formula: 'EMA_t = close_t·k + EMA_(t−1)·(1−k), k = 2/(20+1)',
    meaning: 'A moving average that weights recent closes more heavily — the fast trend line.',
    reading: 'Price above a rising EMA20 is short-term bullish; crossing below it warns of momentum loss.',
    units: 'price',
  },
  ema50: {
    title: 'EMA(50)',
    formula: 'EMA_t = close_t·k + EMA_(t−1)·(1−k), k = 2/(50+1)',
    meaning: 'Medium-term exponential average, slower than the EMA20.',
    reading: 'EMA20 above EMA50 is the classic bullish stack; the cross is a regime-change hint.',
    units: 'price',
  },
  sma200: {
    title: 'SMA(200)',
    formula: 'mean of the last 200 closes',
    meaning: 'The long-term baseline. Needs 200 bars, so it is blank on short histories.',
    reading: 'Above the SMA200 is a bull regime, below it a bear regime. Rarely useful on intraday timeframes.',
    units: 'price',
  },
  atr20: {
    title: 'ATR(20) — Average True Range, the Turtles’ N',
    formula: 'Wilder-smoothed mean of True Range over 20 bars',
    meaning: 'Average distance price travels per bar, in price units.',
    reading: 'Sizes positions and stops. A 2N stop is twice this. Rising ATR means volatility expansion.',
    units: 'price',
  },
  adx14: {
    title: 'ADX(14) — Average Directional Index',
    formula: 'Wilder-smoothed DX(14) where DX = 100·|+DI − −DI| / (+DI + −DI)',
    meaning: 'Trend strength regardless of direction, on a 0-100 scale.',
    reading: 'Below 20 is a choppy, range-bound market; above 25 is a trending one. 40+ is a strong trend. The survey gates on ADX.',
    units: '0-100',
  },
  rsi14: {
    title: 'RSI(14) — Relative Strength Index',
    formula: 'RSI = 100 − 100/(1 + average gain/average loss) over 14 bars',
    meaning: 'Momentum as the ratio of recent gains to recent losses, 0-100.',
    reading: 'Above 70 is stretched (overbought), below 30 washed out (oversold). Strong trends can stay pinned for a long time.',
    units: '0-100',
  },
};

/** Every toggleable chip, in display order. Each state is persisted by id. */
export const INDICATOR_DEFS: readonly IndicatorDef[] = GROUPS.map((g) => ({
  id: g.id,
  label: g.label,
  pane: (g.id === 'donchian20' || g.id === 'donchian55' || g.id === 'ema20' || g.id === 'ema50' || g.id === 'sma200'
    ? 'price'
    : g.id === 'atr20'
      ? 'atr'
      : g.id === 'adx14'
        ? 'adx'
        : 'rsi') as PaneId,
  series:
    g.id === 'donchian20'
      ? ['donchian20_upper', 'donchian20_lower']
      : g.id === 'donchian55'
        ? ['donchian55_upper', 'donchian55_lower']
        : g.id === 'adx14'
          ? ['adx14', 'plus_di', 'minus_di']
          : [g.id],
  toolKeys: g.toolKeys,
  glossaryKey: g.glossaryKey,
  blurb: g.blurb,
  defaultOn: g.id === 'donchian20' || g.id === 'ema20' || g.id === 'sma200' || g.id === 'atr20' ||
    g.id === 'adx14' || g.id === 'rsi14',
  fallback: FALLBACKS[g.id] ?? {},
}));

/** Default on/off map for the indicator chips. */
export function defaultIndicatorState(): Record<string, boolean> {
  const state: Record<string, boolean> = {};
  for (const def of INDICATOR_DEFS) state[def.id] = def.defaultOn;
  return state;
}
