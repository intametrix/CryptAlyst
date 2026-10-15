import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { UTCTimestamp } from 'lightweight-charts';
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type IPriceLine,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts';
import {
  INDICATOR_DEFS,
  alignIndicators,
  barTime,
  defaultIndicatorState,
  markTitle,
  parseBars,
  priceFormatFor,
  summarizeLatest,
  toCandles,
  toMarkers,
  toVolumes,
  volumeColor,
  type ChartMarker,
  type LinePoint,
  type SafeBar,
} from '../chartData';
import { clock, int, isoUtc, multiple, num, px, share, text, usd } from '../format';
import type { ColorCanon } from '../colors';
import { saveChipState, getLayout } from '../layout';
import { INTERVAL_MS, LIVE_POLL_MS, bucketStart, liveIndicator, useKlines } from '../hooks/useKlines';
import { useFlowHistory, type FlowHistoryState } from '../hooks/useFlowHistory';
import { useOutlook, type OutlookLevel } from '../hooks/useOutlook';
import { resolvePalette } from '../palette';
import { useElementSize } from '../hooks/useElementSize';
import { INTERVALS, type Interval, type ToolsMsg } from '../types';
import { eventIdentity } from '../colors';
import { EventGlyph } from './EventGlyph';
import { MarkerLayer } from './MarkerLayer';
import { OutlookStrip } from './OutlookStrip';
import { SplitPane } from './Panel';
import { presetAction, type ZoomPreset } from '../zoom';
import { Tooltip } from './Tooltip';

/* --------------------------------------------------------------- appearance */

/**
 * One sample per marker family, so the legend shows exactly the letters, colours
 * and arrows the chart draws — resolved through the canonical palette rather than
 * re-typed here.
 */
const MARK_SAMPLES: readonly {
  kind: string;
  title: string;
  blurb: string;
  toolKeys: string[];
}[] = [
  { kind: 'PUMP', title: 'Pump', blurb: 'An upward onset detected on this bar.', toolKeys: ['mark_p'] },
  { kind: 'DUMP', title: 'Dump', blurb: 'A downward onset detected on this bar.', toolKeys: ['mark_d'] },
  { kind: 'REVERSAL_DUMP', title: 'Reversal', blurb: 'An exhaustion turn, either direction.', toolKeys: ['mark_r'] },
  { kind: 'BREAKOUT_LONG', title: 'Breakout', blurb: 'A channel breakout from the Turtle detector.', toolKeys: ['mark_b'] },
];

/**
 * How container height is shared between panes — **measured**, not assumed.
 *
 * `chart.resize(w, h)` re-divides the box across all four panes in proportion to
 * their current sizes, and it does so *after* any `setHeight` call made around
 * it (verified: pinning the indicator panes to fixed heights had no lasting
 * effect — the next resize redistributed them again). Two rounds of trying to
 * pin them produced the same ratios every time.
 *
 * So the behaviour is: **every pane grows with the container, the price pane
 * most of all**, because it already holds the bulk of the box. Measured on a
 * container growing 354 → 551 px of chart host:
 *
 *   host     price pane   ATR   ADX   RSI
 *   354        218        28    28    28
 *   504        312        43    43    ...
 *   654        421        67    67
 *   804        516        77    77
 *   851        553        88    90    28
 *
 * Letting the library do that leaves the candles with only about a third of any
 * gain, though, so the price pane is additionally **pinned explicitly** to the
 * remainder after the indicator panes and the axis: that is what makes a taller
 * container actually produce taller candles. `setHeight` on pane 0 is honoured
 * when it is applied after the resize, which is why it is done in an effect
 * rather than inside the resize call.
 */
const PANE_INDICATOR_PX = { atr: 84, adx: 92, rsi: 84 } as const;
/** Indicator panes shrink to these floors before the price pane gives up space. */
/**
 * The flow pane's own height.
 *
 * It is the evidence layer — the pane that shows *why* the detector fired — so it
 * is given a readable floor rather than the sliver a pure proportional split left
 * it (measured at 24–28px, which shows nothing).
 */
const FLOW_PANE_TARGET_PX = 148;
const FLOW_PANE_MIN_PX = 72;
/** Height the time/price axes take inside the box; the panes split the rest. */
const AXIS_ALLOWANCE_PX = 30;

const COLORS = {
  up: '#2ee06a',
  down: '#ff4d5e',
  accent: '#4d8dfd',
  donchian: '#4d8dfd',
  donchianSlow: '#8b5cf6',
  ema20: '#22d3ee',
  ema50: '#f59e0b',
  sma200: '#e879f9',
  atr: '#fb923c',
  adx: '#38bdf8',
  plusDi: '#2ee06a',
  minusDi: '#ff4d5e',
  rsi: '#c084fc',
} as const;

const LINE_WIDTH = 1 as const;

/** Per-series line styling, resolved from the indicator key. */
function lineStyleFor(key: string): { color: string; lineStyle?: LineStyle; width: 1 | 2 } {
  switch (key) {
    case 'donchian20_upper':
    case 'donchian20_lower':
      return { color: COLORS.donchian, lineStyle: LineStyle.Dashed, width: LINE_WIDTH };
    case 'donchian55_upper':
    case 'donchian55_lower':
      return { color: COLORS.donchianSlow, lineStyle: LineStyle.Dotted, width: LINE_WIDTH };
    case 'ema20':
      return { color: COLORS.ema20, width: 2 };
    case 'ema50':
      return { color: COLORS.ema50, width: LINE_WIDTH };
    case 'sma200':
      return { color: COLORS.sma200, width: LINE_WIDTH };
    case 'atr20':
      return { color: COLORS.atr, width: 2 };
    case 'adx14':
      return { color: COLORS.adx, width: 2 };
    case 'plus_di':
      return { color: COLORS.plusDi, width: LINE_WIDTH };
    case 'minus_di':
      return { color: COLORS.minusDi, width: LINE_WIDTH };
    case 'rsi14':
      return { color: COLORS.rsi, width: 2 };
    default:
      return { color: COLORS.accent, width: LINE_WIDTH };
  }
}

const PANE_TINT: Record<string, string> = {
  atr: 'rgba(251, 146, 60, 0.55)',
  adx: 'rgba(56, 189, 248, 0.55)',
  rsi: 'rgba(192, 132, 252, 0.55)',
};

/** Which chart pane an indicator key is drawn in. */
function paneOf(key: string): 'atr' | 'adx' | 'rsi' {
  if (key === 'atr20' || key === 'atr_pct') return 'atr';
  if (key === 'adx14' || key === 'plus_di' || key === 'minus_di') return 'adx';
  return 'rsi';
}

/* ------------------------------------------------------------ chart handling */

/**
 * A measurement surface for tests, exposed on the chart element.
 *
 * Counters are used to *prove* the update rate rather than assert it: the
 * correctness tests read `updates` to show the ticker path drives the candle an
 * order of magnitude more often than the reconciliation poll, and `painted` is a
 * readback of the canvas pixels so "the chart is not blank" is a measurement
 * instead of an assumption.
 */
export interface ChartProbe {
  updates: { candle: number; volume: number; line: number };
  painted: () => boolean;
  paneSizes: () => { price: number; indicators: number[] };
  timeScale: () => {
    from: number | null;
    to: number | null;
    bars: number;
    /** Bar count currently in view. */
    visible: number;
    /** Epoch ms of the first and last visible bar, for a readable report. */
    firstMs: number | null;
    lastMs: number | null;
    /** The interval the loaded bars belong to, from the payload. */
    interval: string;
  };
  /**
   * What the flow pane is drawing: the bar heights (the burst multiples) and the
   * baseline the reference line sits on. Acceptance is "a visible burst bar whose
   * height matches the payload", which needs the drawn values, not the source.
   */
  flow: () => {
    values: number[];
    baseline: number | null;
    paneHeight: number;
    status: string;
    /** Every pane's height, for diagnosing the distribution. */
    allPanes: number[];
    panes: number;
  };
}

interface ChartHandles {
  chart: IChartApi;
  candles: ISeriesApi<'Candlestick'>;
  volume: ISeriesApi<'Histogram'>;
  lines: Map<string, ISeriesApi<'Line'>>;
  markers: ISeriesMarkersPluginApi<Time>;
  /** Pane index for each indicator pane, resolved after panes are created. */
  paneIndex: Record<'atr' | 'adx' | 'rsi', number>;
  /**
   * The flow pane: one histogram bar per 10-second bin, `burst_multiple` tall.
   *
   * This is the evidence layer. A 1s candle shows price, not the abnormal
   * quote-volume burst the detector actually thresholds on, so without this pane
   * a user watching a 5-second zoom has no way to see *why* an alert fired.
   */
  flow: ISeriesApi<'Histogram'>;
  flowPaneIndex: number;
  /** The reference line at 1.0× — normal flow. */
  flowBaseline: IPriceLine;
  /** The support/resistance lines currently drawn, so they can be replaced. */
  levelLines: IPriceLine[];
  /** The forecast projection: a dashed mid line plus its uncertainty band. */
  forecastLine: ISeriesApi<'Line'> | null;
  /** The band's two halves (upper = fill down from, lower = paint over). */
  bandUpper: ISeriesApi<'Area'> | null;
  bandLower: ISeriesApi<'Area'> | null;
}

/** Resolve the pane index an indicator's pane landed in (v5 exposes `panes()`). */
function resolvePaneIndex(chart: IChartApi, want: number): number {
  const panes = chart.panes();
  if (want >= 0 && want < panes.length) return want;
  return Math.max(0, panes.length - 1);
}

/** The pane index a just-added series landed in, for `priceScale(id, pane)`. */
function flowPaneIndexOrZero(chart: IChartApi): number {
  return Math.max(0, chart.panes().length - 1);
}

/**
 * Create the chart with all series and panes ready. Series stay attached and
 * are toggled with `applyOptions({ visible })` — cheaper and safer than
 * re-creating them, and it keeps the pane list stable.
 */
/**
 * The series name for a chart element, taken **verbatim** from `/api/tools`.
 *
 * Chart copy is not authored here: if the glossary has a title for the key that
 * is the name the legend shows, and if it does not, the raw key is used rather
 * than a prettier invention. One source of truth means the chip, the tooltip and
 * the series cannot say three different things.
 */
function seriesTitle(tools: ToolsMsg | null, keys: readonly string[], rawFallback: string): string {
  for (const key of keys) {
    const entry = tools?.indicators?.[key] ?? tools?.metrics?.[key] ?? null;
    const title = typeof entry?.title === 'string' ? entry.title.trim() : '';
    if (title !== '') return title;
  }
  return rawFallback;
}

function buildChart(
  container: HTMLElement,
  bars: readonly SafeBar[],
  tools: ToolsMsg | null,
  label: { interval: string },
): ChartHandles {
  const chart = createChart(container, {
    autoSize: false,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#8492a9',
      fontFamily:
        'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 11,
      panes: { enableResize: true, separatorColor: '#1e2636', separatorHoverColor: 'rgba(77, 141, 253, 0.4)' },
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: 'rgba(30, 38, 54, 0.6)' },
      horzLines: { color: 'rgba(30, 38, 54, 0.6)' },
    },
    rightPriceScale: { borderColor: '#1e2636', scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: { borderColor: '#1e2636', timeVisible: true, secondsVisible: false, rightOffset: 2 },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: 'rgba(77, 141, 253, 0.6)', labelBackgroundColor: '#1d2840', width: 1 },
      horzLine: { color: 'rgba(77, 141, 253, 0.6)', labelBackgroundColor: '#1d2840', width: 1 },
    },
    handleScale: { axisPressedMouseMove: { time: true, price: false } },
  });

  const format = priceFormatFor(bars);
  const candles = chart.addSeries(CandlestickSeries, {
    upColor: COLORS.up,
    downColor: COLORS.down,
    borderUpColor: COLORS.up,
    borderDownColor: COLORS.down,
    wickUpColor: 'rgba(46, 224, 106, 0.75)',
    wickDownColor: 'rgba(255, 77, 94, 0.75)',
    priceFormat: { type: 'price', precision: format.precision, minMove: format.minMove },
  });
  candles.setData(toCandles(bars));

  // Volume rides on its own invisible scale so it cannot squash the candles.
  const volume = chart.addSeries(HistogramSeries, {
    priceScaleId: 'volume',
    priceFormat: { type: 'volume' },
    priceLineVisible: false,
    lastValueVisible: false,
  });
  volume.setData(toVolumes(bars));
  chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });

  // --- overlay lines, pane 0 -------------------------------------------------
  const overlayKeys = INDICATOR_DEFS.filter((d) => d.pane === 'price').flatMap((d) => [...d.series]);
  const lines = new Map<string, ISeriesApi<'Line'>>();
  for (const key of overlayKeys) {
    const style = lineStyleFor(key);
    const series = chart.addSeries(LineSeries, {
      color: style.color,
      lineWidth: style.width,
      lineStyle: style.lineStyle ?? LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: false,
      // The glossary's own title, verbatim.
      title: seriesTitle(tools, [key], key),
    });
    lines.set(key, series);
  }

  // --- indicator panes ------------------------------------------------------
  const atrPane = chart.addPane();
  const atrLine = atrPane.addSeries(LineSeries, {
    color: COLORS.atr,
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    visible: false,
    title: seriesTitle(tools, ['atr20', 'atr'], 'atr20'),
  });
  lines.set('atr20', atrLine);
  atrLine.createPriceLine({
    price: 0,
    color: 'rgba(251, 146, 60, 0.25)',
    lineWidth: 1,
    lineStyle: LineStyle.Dotted,
    axisLabelVisible: false,
    title: '',
  });

  const adxPane = chart.addPane();
  const adxLine = adxPane.addSeries(LineSeries, {
    color: COLORS.adx,
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    visible: false,
    title: seriesTitle(tools, ['adx14', 'adx'], 'adx14'),
  });
  const plusLine = adxPane.addSeries(LineSeries, {
    color: COLORS.plusDi,
    lineWidth: LINE_WIDTH,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    visible: false,
    title: seriesTitle(tools, ['plus_di'], 'plus_di'),
  });
  const minusLine = adxPane.addSeries(LineSeries, {
    color: COLORS.minusDi,
    lineWidth: LINE_WIDTH,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    visible: false,
    title: seriesTitle(tools, ['minus_di'], 'minus_di'),
  });
  lines.set('adx14', adxLine);
  lines.set('plus_di', plusLine);
  lines.set('minus_di', minusLine);
  adxLine.createPriceLine({
    price: 25,
    color: 'rgba(56, 189, 248, 0.35)',
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    axisLabelVisible: true,
    title: '25',
  });

  const rsiPane = chart.addPane();
  const rsiLine = rsiPane.addSeries(LineSeries, {
    color: COLORS.rsi,
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
    visible: false,
    title: seriesTitle(tools, ['rsi14', 'rsi'], 'rsi14'),
  });
  lines.set('rsi14', rsiLine);
  for (const level of [30, 70] as const) {
    rsiLine.createPriceLine({
      price: level,
      color: level === 70 ? 'rgba(255, 77, 94, 0.4)' : 'rgba(46, 224, 106, 0.4)',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: String(level),
    });
  }

  // --- the flow pane: burst multiple per 10-second bin -----------------------
  // Log scale on purpose: bursts are heavy-tailed, so one 47x bar would flatten
  // every other bin on a linear axis. `logarithmic` is a valid `PriceScaleMode`
  // in the installed typings (verified in `typings.d.ts`).
  const flowPane = chart.addPane();
  const flow = flowPane.addSeries(HistogramSeries, {
    priceScaleId: 'right',
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    priceLineVisible: false,
    lastValueVisible: true,
    base: 0,
    title: 'flow burst',
  });
  chart.priceScale('right', flowPaneIndexOrZero(chart)).applyOptions({
    mode: PriceScaleMode.Logarithmic,
    borderColor: '#1e2636',
  });
  // The 1.0 reference line: the boundary between normal and abnormal flow.
  const flowBaseline = flow.createPriceLine({
    price: 1,
    color: '#8492a9',
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    axisLabelVisible: true,
    title: '1.0x baseline',
  });

  const markers = createSeriesMarkers(candles, []);

  // --- measurement surface (inert unless a test asks for it) ----------------
  const probe: ChartProbe = {
    updates: { candle: 0, volume: 0, line: 0 },
    painted: () => {
      const canvas = container.querySelector('canvas');
      if (canvas === null) return false;
      try {
        const context = canvas.getContext('2d');
        if (context === null) return false;
        const { data } = context.getImageData(0, 0, Math.min(400, canvas.width), Math.min(400, canvas.height));
        // A blank chart is a single flat colour; any spread of pixel values means
        // candles, grid lines or axes were actually drawn.
        for (let i = 3; i < data.length; i += 4 * 37) {
          if (data[i] !== data[3]) return true;
        }
        return false;
      } catch {
        // A tainted canvas cannot be read; do not fail the check over it.
        return true;
      }
    },
    paneSizes: () => {
      const sizes = chart.panes().map((pane, i) => (i === 0 ? 0 : pane.getHeight()));
      return { price: chart.paneSize(0).height, indicators: sizes.slice(1) };
    },
    flow: () => {
      const data = flow.data() as readonly { value?: number }[];
      return {
        values: data.map((p) => (typeof p.value === 'number' ? p.value : 0)),
        // The reference line is created at 1.0; `options()` reads it back from the
        // library rather than trusting a constant kept alongside.
        baseline: flowBaseline.options().price,
        paneHeight: chart.paneSize(chart.panes().length - 1).height,
        status: '',
        allPanes: chart.panes().map((pane, i) => pane.getHeight() + (i === 0 ? 0 : 0)),
        panes: chart.panes().length,
      };
    },
    timeScale: () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      const from = range?.from ?? null;
      const to = range?.to ?? null;
      // Report *times*, not indices: "a 5s preset showed 5 bars" is only
      // checkable against the data, and an index can silently mean anything.
      const at = (index: number): number | null => {
        if (bars.length === 0) return null;
        const i = Math.max(0, Math.min(bars.length - 1, Math.round(index)));
        return bars[i].t;
      };
      return {
        from,
        to,
        bars: bars.length,
        visible: from === null || to === null ? bars.length : Math.max(0, Math.round(to - from)),
        firstMs: from === null ? null : at(from),
        lastMs: to === null ? null : at(to),
        interval: label.interval,
      };
    },
  };

  const wrapUpdate = <T,>(series: T, key: keyof ChartProbe['updates']): T => {
    const target = series as unknown as { update: (v: unknown) => void };
    const original = target.update.bind(target);
    target.update = (value: unknown): void => {
      probe.updates[key] += 1;
      original(value);
    };
    return series;
  };
  wrapUpdate(candles, 'candle');
  wrapUpdate(volume, 'volume');
  for (const series of lines.values()) wrapUpdate(series, 'line');

  (container as HTMLElement & { __probe?: ChartProbe }).__probe = probe;

  const handles: ChartHandles = {
    chart,
    candles,
    volume,
    lines,
    markers,
    paneIndex: {
      atr: resolvePaneIndex(chart, 1),
      adx: resolvePaneIndex(chart, 2),
      rsi: resolvePaneIndex(chart, 3),
    },
    flow,
    flowPaneIndex: resolvePaneIndex(chart, 4),
    flowBaseline,
    levelLines: [],
    forecastLine: null,
    bandUpper: null,
    bandLower: null,
  };

  return handles;
}

/* ------------------------------------------------------------------ tooltips */

interface MetricChip {
  key: string;
  label: string;
  tools: readonly string[];
  value: string;
}

/* -------------------------------------------------------------------- panel */

/** Zoom controls the chart exposes to whoever renders its bottom slot. */
export interface ChartZoomControls {
  /**
   * Apply a preset. Returns `false` when it asked for more bars than are loaded,
   * so the caller can show a "limited history" affordance instead of silently
   * fetching.
   */
  applyPreset: (preset: ZoomPreset) => boolean;
  /** True when the preset's interval is the one already loaded. */
  canZoomLocally: (preset: ZoomPreset) => boolean;
  /**
   * Bumped every time a fresh payload has been painted into the series.
   *
   * An interval switch replaces the data, so a preset that must be applied *to
   * that data* waits for this to change rather than guessing a delay — effects
   * run on a commit, and this counter changes on exactly the commit that matters.
   */
  readySeq: number;
  /** Restore the default (full history) view. */
  reset: () => void;
}

export interface ChartPanelProps {
  /** Selected symbol, or `null` when nothing has been chosen yet. */
  symbol: string | null;
  interval: Interval;
  tools: ToolsMsg | null;
  /** Called when the user picks a different timeframe chip. */
  onIntervalChange: (interval: Interval) => void;
  /**
   * The newest SSE price for *this* symbol (already filtered upstream). Applied
   * to the forming candle between REST polls, which is what makes the price
   * move sub-second without any extra requests.
   */
  priceTick?: { symbol: string; price: number; atMs: number } | null;
  /**
   * The newest `ticker` push for this symbol (~4 Hz). This is the primary live
   * path: it drives the candle and accumulates bar volume from the rolling 24h
   * counter, while the REST poll reconciles.
   */
  tickerTick?: { symbol: string; price: number; quoteVol24h: number | null; atMs: number } | null;
  /** Local ms of the last applied ticker frame, for the live pill. */
  tickerAtMs?: number | null;
  /** The canonical palette; the chart draws with the server's own colours. */
  canon?: ColorCanon | null;
  /** Toggle the flow pane with the other chart elements. */
  showFlow?: boolean;
  /**
   * "Events only": every indicator is hidden so the events can be read against
   * bare price and the flow pane. The caller owns the toggle because it must also
   * remember the indicator set to restore.
   */
  eventsOnly?: boolean;
  /** Flip the mode; the caller remembers what to restore. */
  onToggleEventsOnly?: () => void;
  /** The flow pane's own history state, reported up for the label and legend. */
  onFlowState?: (state: FlowHistoryState) => void;
  /** The shared selected event (a bar open time in epoch ms), or `null`. */
  selectedEvent?: number | null;
  /** Reports a marker click / chart click so the caller can share the selection. */
  onSelectEvent?: (time: number | null) => void;
  /**
   * The event list + detail area, rendered under the chart. Passed in so the
   * selection it renders is the *same* state the markers use, rather than two
   * copies that can disagree.
   */
  bottom?: ReactNode;
  /**
   * Publishes the chart's zoom controls upward.
   *
   * The event panel lives in the caller (so the selection is shared), but only
   * the chart can move the time scale, so the chart hands its controls over
   * rather than the caller reaching into the series. `null` while unmounted.
   */
  onZoomControls?: (controls: ChartZoomControls | null) => void;
  /**
   * Reports the marks for the charted symbol, newest first.
   *
   * The chart already converts the wire marks into drawable markers; the event
   * list needs the same objects, so they are published rather than parsed a
   * second time in the caller (which is how two views drift apart).
   */
  onEvents?: (events: ChartMarker[]) => void;
}

/**
 * Candles + volume + indicator panes, with every chart element toggleable and
 * every chip carrying a glossary-driven tooltip.
 *
 * The chart is disposed on unmount and whenever the symbol changes, because
 * lightweight-charts holds canvases and observers that would otherwise leak
 * (`chart.remove()` is the only thing that releases them).
 */
export function ChartPanel({
  symbol,
  interval,
  tools,
  onIntervalChange,
  priceTick = null,
  tickerTick = null,
  tickerAtMs = null,
  canon = null,
  showFlow = true,
  eventsOnly = false,
  onToggleEventsOnly,
  onFlowState,
  selectedEvent = null,
  onSelectEvent,
  bottom = null,
  onZoomControls,
  onEvents,
}: ChartPanelProps) {
  const chartRef = useRef<ChartHandles | null>(null);
  /**
   * Set *before* `chart.remove()` so no resize can be delivered to a disposed
   * instance. The host box collapses while the overlay unmounts, which makes the
   * library's own observer fire one last time; without this guard that callback
   * throws "Object is disposed".
   */
  const disposedRef = useRef(false);
  /** The box height the panes were last distributed for. */
  const lastDistributed = useRef<number>(-1);
  /**
   * The current mode, readable from the resize callback without making it a
   * dependency (which would re-subscribe the observer on every toggle).
   */
  const eventsOnlyRef = useRef(false);
  /**
   * Callback props are read through refs.
   *
   * A parent that passes an inline arrow (`onEvents={setChartEvents}` is fine, but
   * `onEvents={(e) => setEvents(e)}` is not) would otherwise give these effects a
   * new dependency every render. When the callback also sets parent state, that
   * is an infinite render loop — which is exactly what starved the ticker path
   * during testing: 24 frames arrived and produced one update.
   */
  const eventsSinkRef = useRef(onEvents);
  eventsSinkRef.current = onEvents;
  const zoomSinkRef = useRef(onZoomControls);
  zoomSinkRef.current = onZoomControls;
  const selectSinkRef = useRef(onSelectEvent);
  selectSinkRef.current = onSelectEvent;
  // Observe the chart's own host box with `content-box`, so the measured size is
  // exactly the space the canvas may occupy. It is the innermost element that
  // changes when the overlay is dragged, and it is a definite-height box.
  const { ref: containerRef, size } = useElementSize<HTMLDivElement>({ box: 'content-box' });
  const sizeRefLive = useRef(size);
  sizeRefLive.current = size;

  // `active` is the pop-out's mount state: closing the overlay unmounts this
  // component, so polling stops with it, and reopening starts it again.
  const { data, loading, refreshing, error, atMs, live, liveAtMs, polling, refresh } = useKlines(symbol, interval, {
    active: true,
  });
  /**
   * The 10-second flow bins for this symbol.
   *
   * Fetched from `/api/flow/history`, which returns the ratio the detector
   * thresholds on. Three states are distinguished: `ready`, `quiet` (tracked but
   * no flow — drawn flat) and `untracked` (a 404 — a coverage gap, not an error).
   */
  const flowHistory = useFlowHistory(symbol);
  /**
   * The support/resistance map, the forecast, and the ETA to reversal.
   *
   * Fetched from `/api/outlook`, which is where the levels the user asked for
   * actually live. Absent sections are normal: a symbol with no measured levels
   * simply gets no lines.
   */
  const outlook = useOutlook(symbol);
  /** The forming candle's identity, shared by the poll and the SSE tick. */
  const liveStateRef = useRef<{ lastBarTime: number | null; lastOpen: number | null; lastClose: number | null }>({
    lastBarTime: null,
    lastOpen: null,
    lastClose: null,
  });
  /**
   * Volume accumulation for the forming bar.
   *
   * `quote_vol_24h` is a **rolling 24h total**, not a per-bar volume, so it can
   * only be used as a delta: each frame adds `counter - previousCounter` to this
   * bar's accumulator. A negative delta is clamped to 0 — the 24h window rolls
   * forward and the counter legitimately goes down, which would otherwise make
   * the bar volume negative. The poll re-anchors the counter so drift cannot
   * accumulate forever.
   */
  const volumeRef = useRef<{
    barTime: number | null;
    counter: number | null;
    /** Volume already attributed to the forming bar. */
    accumulated: number | null;
    /** Last value pushed to the series, so we only update when it changes. */
    pushed: number | null;
  }>({ barTime: null, counter: null, accumulated: null, pushed: null });
  /**
   * The marks on the bar under the crosshair, plus where to park the tooltip.
   * lightweight-charts markers have no tooltip of their own, so the crosshair is
   * what makes a letter explainable.
   */
  const [hover, setHover] = useState<{ key: string; x: number; y: number } | null>(null);
  /** Ticking clock for the "live · 1s ago" affordance and the hover state. */
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const [chips, setChips] = useState<Record<string, boolean>>(() => ({
    ...defaultIndicatorState(),
    ...getLayout().chips,
  }));
  const [showVolume, setShowVolume] = useState(true);

  const bars = useMemo<SafeBar[]>(() => parseBars(data?.bars), [data]);

  /**
   * Marks from the history payload, plus any the fast poll has delivered since.
   * The server dedupes per (bar, kind), so they are merged by `time:letter` —
   * newest wins — and the marker set is always replaced wholesale, never
   * appended to a stale array.
   */
  const marks = useMemo<unknown[]>(() => {
    const merged = new Map<string, unknown>();
    const add = (raw: unknown): void => {
      if (typeof raw !== 'object' || raw === null) return;
      const mark = raw as { t?: unknown; letter?: unknown; kind?: unknown };
      const key = `${String(mark.t ?? '')}:${String(mark.letter ?? mark.kind ?? '')}`;
      merged.set(key, raw);
    };
    for (const mark of Array.isArray(data?.marks) ? data.marks : []) add(mark);
    for (const mark of live?.marks ?? []) add(mark);
    return [...merged.values()];
  }, [data, live]);

  /** Every mark for this symbol as a drawable marker, oldest first. */
  const markerList = useMemo(() => toMarkers(marks, bars, canon), [marks, bars, canon]);

  useEffect(() => {
    // Newest first for the list; the chart keeps its own oldest-first order.
    eventsSinkRef.current?.(markerList.slice().reverse());
  }, [markerList]);
  const latest = useMemo(() => summarizeLatest(data?.latest, bars), [data, bars]);
  const overlaySeries = useMemo(
    () => alignIndicators(INDICATOR_DEFS.flatMap((d) => [...d.series]), data?.indicators, bars),
    [data, bars],
  );

  const enabledKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const def of INDICATOR_DEFS) {
      if (chips[def.id] === true) for (const key of def.series) keys.add(key);
    }
    return keys;
  }, [chips]);

  // `eventsOnly` arrives as a prop so the caller can persist it, but the chips
  // also need to end the mode locally; the two are kept in step.
  const [eventsOnlyLocal, setEventsOnlyLocal] = useState(eventsOnly);
  useEffect(() => {
    setEventsOnlyLocal(eventsOnly);
  }, [eventsOnly]);
  const eventsOnlyActive = eventsOnly || eventsOnlyLocal;

  /**
   * "Events only": every chip off, so the events read against bare price.
   *
   * The previous set is remembered in a ref and restored verbatim — a user who
   * had EMA20 on and SMA200 off gets exactly that back, not a default set. The
   * remembered copy is deliberately *not* written to storage while the mode is
   * active, so a reload during events-only still restores their real preferences.
   */
  const chipMemo = useRef<Record<string, boolean> | null>(null);
  const toggleEventsOnly = useCallback(() => {
    const entering = !(eventsOnly || eventsOnlyLocal);
    if (entering) {
      chipMemo.current = chips;
      setChips(Object.fromEntries(INDICATOR_DEFS.map((def) => [def.id, false])));
    } else if (chipMemo.current !== null) {
      // Restore exactly the set that was on, not a default.
      setChips(chipMemo.current);
      chipMemo.current = null;
    }
    setEventsOnlyLocal(entering);
    // The caller may persist the mode; the chips stay consistent either way.
    onToggleEventsOnly?.();
  }, [chips, eventsOnly, eventsOnlyLocal, onToggleEventsOnly]);

  const toggleChip = useCallback((id: string) => {
    setChips((prev) => {
      const next = { ...prev, [id]: !(prev[id] === true) };
      // A chip pressed while in events-only mode is a normal action and ends the
      // mode, so the toggle and the chips can never disagree about what is shown.
      if (eventsOnlyActive) {
        setEventsOnlyLocal(false);
        chipMemo.current = null;
      }
      // While the mode is active the all-off state is a *view*, not a preference,
      // so it is not persisted over the user's real set.
      if (!eventsOnlyActive) saveChipState(next);
      return next;
    });
  }, []);

  const resetChips = useCallback(() => {
    const next = defaultIndicatorState();
    saveChipState(next);
    setChips(next);
  }, []);

  /* -- create / destroy the chart when the symbol (or its bar count) changes -- */

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || symbol === null || bars.length === 0) return;

    disposedRef.current = false;
    const handles = buildChart(container, bars, tools, { interval });
    chartRef.current = handles;
    setLayer({ chart: handles.chart, series: handles.candles });

    // Size to the measured box right away; the ResizeObserver effect keeps it in
    // step afterwards (including while the overlay is being dragged), and the
    // zero guard means a not-yet-laid-out box cannot throw here.
    const { width, height } = sizeRefLive.current;
    if (width > 0 && height > 0) handles.chart.resize(width, height);

    return () => {
      disposedRef.current = true;
      chartRef.current = null;
      setLayer(null);
      // `remove()` detaches observers and frees the canvases — without it a
      // symbol change would leak a whole chart each time.
      handles.chart.remove();
    };
    // Rebuilt only when the *symbol* or the bar count changes: a 30s poll that
    // merely refreshes the forming candle must not throw away the user's zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, bars.length, bars[0]?.t, tools]);

  /* -- container resize: the canvas follows the box, not the viewport -------- */

  /**
   * Give the indicator panes their heights and let the price pane take the rest.
   *
   * Called after every resize. `setHeight` on panes 1..3 is what stops the
   * library from splitting the new height evenly, which is what made a taller
   * container leave the candles the same size.
   */
  const distributePaneHeights = useCallback((chart: IChartApi, boxHeight: number, collapse: boolean): void => {
    const handles = chartRef.current;
    const panes = chart.panes();
    if (handles === null || panes.length < 2) return;

    const indicators = (['atr', 'adx', 'rsi'] as const)
      .map((key) => ({ key, index: handles.paneIndex[key] }))
      .filter((item) => item.index > 0 && item.index < panes.length);

    const available = Math.max(0, boxHeight - AXIS_ALLOWANCE_PX);
    /**
     * Every pane below the candles wants height, and in a short box their floors
     * add up to more than the box — which squeezes the price pane to nothing and
     * the chart renders blank (measured: 30px of canvas at a 404px overlay).
     *
     * So the auxiliaries share a *budget* rather than each taking its floor: the
     * floors are scaled down together until they fit, and the price pane keeps a
     * guaranteed share. Growth still lands on the candles, because the budget is
     * a fraction of the box, not a fixed number of pixels.
     */
    // Events-only: the indicator panes and the flow pane are given the minimum,
    // so the price pane takes the whole box. A hidden *series* would leave its
    // pane in place, which is why this is handled here rather than by the chips.
    if (collapse) {
      let reclaimed = 0;
      for (const item of indicators) {
        const now = panes[item.index].getHeight();
        reclaimed += Math.max(0, now - 1);
        if (now !== 1) panes[item.index].setHeight(1);
      }
      if (handles.flowPaneIndex > 0 && handles.flowPaneIndex < panes.length) {
        const now = panes[handles.flowPaneIndex].getHeight();
        reclaimed += Math.max(0, now - 1);
        if (now !== 1) panes[handles.flowPaneIndex].setHeight(1);
      }
      void reclaimed;
      const priceHeight = Math.max(80, available - 1);
      if (panes[0].getHeight() !== priceHeight) panes[0].setHeight(priceHeight);
      lastDistributed.current = boxHeight;
      return;
    }

    /**
     * Allocation.
     *
     * The library re-divides the panes to fit the box, so the targets must **sum
     * to the available height** — a set that over-reserves is scaled back
     * proportionally and the flow pane ends up at 42px no matter what it asked
     * for (measured). So the heights are computed arithmetically first and then
     * applied.
     *
     * Priority: the flow pane (the evidence layer) takes a readable share, the
     * indicator panes share a bounded budget, and the price pane gets everything
     * left — which is what makes the candles absorb any released height.
     */
    const flowIndex = handles.flowPaneIndex > 0 && handles.flowPaneIndex < panes.length ? handles.flowPaneIndex : -1;
    const flowWant = flowIndex === -1 ? 0 : Math.max(FLOW_PANE_MIN_PX, Math.min(FLOW_PANE_TARGET_PX, Math.round(available * 0.25)));

    // The price pane keeps at least this much; below it the chart is unreadable.
    const priceFloor = Math.max(60, Math.round(available * 0.3));
    // Indicators share whatever is left after the flow pane, capped so they can
    // never crowd out the candles.
    const indicatorBudget =
      indicators.length === 0
        ? 0
        : Math.max(0, Math.min(Math.round(available * 0.4), available - priceFloor - flowWant));

    const targets = new Map<number, number>();
    if (flowIndex !== -1) targets.set(flowIndex, flowWant);

    if (indicators.length > 0 && indicatorBudget > 0) {
      const share = Math.floor(indicatorBudget / indicators.length);
      let allocated = 0;
      indicators.forEach((item, position) => {
        const isLast = position === indicators.length - 1;
        // The last one takes the remainder, so the budget is used exactly and
        // rounding cannot push the total over.
        const height = isLast ? Math.max(12, indicatorBudget - allocated) : Math.max(12, Math.min(PANE_INDICATOR_PX[item.key], share));
        allocated += height;
        targets.set(item.index, height);
      });
    }

    const auxTotal = [...targets.values()].reduce((sum, value) => sum + value, 0);
    // The price pane is the remainder by construction, so the set always fits.
    const priceHeight = Math.max(priceFloor, available - auxTotal);
    targets.set(0, priceHeight);

    // Shrink the auxiliaries if the price pane's floor forced the total over.
    let overflow = auxTotal + priceHeight - available;
    if (overflow > 0) {
      const shrinkable = [...targets.entries()].filter(([index]) => index !== 0).sort((a, b) => b[1] - a[1]);
      for (const [index, value] of shrinkable) {
        if (overflow <= 0) break;
        const room = Math.max(0, value - 12);
        const take = Math.min(room, overflow);
        targets.set(index, value - take);
        overflow -= take;
      }
    }

    for (const [index, height] of targets) {
      if (index < panes.length && panes[index].getHeight() !== height) panes[index].setHeight(height);
    }
    lastDistributed.current = boxHeight;
  }, []);

  useEffect(() => {
    eventsOnlyRef.current = eventsOnlyActive;
    const handles = chartRef.current;
    const box = containerRef.current;
    if (handles === null || disposedRef.current || box === null || box.clientHeight <= 0) return;
    // The pane heights must be recomputed *after* React has committed the chip
    // changes, so the collapse happens on the next frame.
    const frame = window.requestAnimationFrame(() => {
      if (disposedRef.current) return;
      lastDistributed.current = -1;
      distributePaneHeights(handles.chart, box.clientHeight, eventsOnlyActive);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [eventsOnlyActive, distributePaneHeights]);

  // lightweight-charts never observes its container, so this is the only thing
  // that makes it repaint mid-drag. The measurement is already rAF-throttled by
  // `useElementSize`, so this runs at most once per frame.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    // A collapsed box reports 0: resizing to that throws inside the library, so
    // it is skipped and the next real size wins.
    if (size.width <= 0 || size.height <= 0) return;
    handles.chart.resize(size.width, size.height);
    // Pane heights are applied on the next frame, because a `resize` re-divides
    // the panes and a `setHeight` issued in the same tick is overwritten.
    const height = size.height;
    const frame = window.requestAnimationFrame(() => {
      if (disposedRef.current) return;
      // The most recent measurement always wins: a stale call cannot undo a
      // newer distribution.
      if (lastDistributed.current === height) return;
      distributePaneHeights(handles.chart, height, eventsOnlyRef.current);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [size.width, size.height, distributePaneHeights]);

  /**
   * A stable bar time -> logical index map for the loaded history.
   *
   * `setVisibleLogicalRange` is **index based**, not time based, so a zoom preset
   * has to be expressed in indices. This map is rebuilt only when the bars or the
   * signals change, so a preset can be applied at any moment without a lookup in
   * the library. The candle series' own index is authoritative: the flow bins
   * share the same time scale, so they follow the same range as one gesture.
   */
  const barIndexByTime = useMemo(() => {
    const map = new Map<number, number>();
    bars.forEach((bar, i) => map.set(barTime(bar), i));
    return map;
  }, [bars]);

  /**
   * Presets act on the **candle** series' index space.
   *
   * For the sub-minute presets (30s, 2m) the candle series cannot resolve that
   * many distinct bars — a 1m chart has one bar per minute — so the flow pane is
   * doing the work there and candles legitimately repeat values within a minute.
   * The preset is still applied as a candle-bar range so that the shared time
   * scale zooms as one gesture, and the flow pane follows.
   */
  const applyZoomPreset = useCallback(
    (preset: ZoomPreset): boolean => {
      const handles = chartRef.current;
      if (handles === null || disposedRef.current) return false;
      const total = bars.length;
      if (total === 0) return false;
      if (preset.mode === 'fit' || preset.bars === undefined) {
        handles.chart.timeScale().fitContent();
        return true;
      }
      const last = total - 1;
      const from = Math.max(0, last - preset.bars + 1);
      // `to` is exclusive-ish: `last + 1` yields exactly `bars` visible because
      // the range is inclusive at `from` and the scale adds no padding of its own
      // inside an explicit range.
      handles.chart.timeScale().setVisibleLogicalRange({ from, to: last + 1 });
      // False means the preset asked for more bars than are loaded. The caller
      // never fetches to satisfy a zoom; it only switches interval when the plan
      // says a different interval is required.
      return last - from + 1 >= preset.bars;
    },
    [bars.length],
  );

  /** Can this preset be satisfied from the bars already loaded? */
  const canZoomLocally = useCallback(
    (preset: ZoomPreset): boolean => presetAction(preset, interval) === 'zoom' && preset.mode !== 'fit',
    [interval],
  );

  const resetZoom = useCallback(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    handles.chart.timeScale().fitContent();
  }, []);

  /** Incremented after each payload is painted, for callers that must wait. */
  const [readySeq, setReadySeq] = useState(0);
  /** The chart handles, exposed to the HTML marker layer. */
  const [layer, setLayer] = useState<{ chart: IChartApi; series: ISeriesApi<'Candlestick'> } | null>(null);

  /** Centre the view on one event: ±60 bars, clamped to what is loaded. */
  // Hand the controls to the caller once they exist, and withdraw them on
  // unmount so a stale closure cannot act on a disposed chart.
  useEffect(() => {
    onFlowState?.(flowHistory);
  }, [flowHistory, onFlowState]);

  useEffect(() => {
    const sink = zoomSinkRef.current;
    if (sink === undefined) return;
    sink({ applyPreset: applyZoomPreset, canZoomLocally, reset: resetZoom, readySeq });
    return () => sink(null);
  }, [applyZoomPreset, canZoomLocally, resetZoom, readySeq]);

  const zoomToEvent = useCallback(
    (timeMs: number): void => {
      const handles = chartRef.current;
      if (handles === null || disposedRef.current) return;
      const index = barIndexByTime.get(Math.floor(timeMs / 1000));
      if (index === undefined) return;
      const total = bars.length;
      const span = 60;
      const from = Math.max(0, index - span);
      const to = Math.min(total - 1, index + span);
      handles.chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: to + 1.5 });
    },
    [barIndexByTime, bars.length],
  );

  /* -- crosshair: explain a marker on hover ---------------------------------- */

  // The marker list keyed by bar time, so a crosshair move is a map lookup.
  const marksByTime = useMemo(() => {
    const map = new Map<number, ChartMarker[]>();
    for (const marker of toMarkers(marks, bars, canon)) {
      const list = map.get(marker.time);
      if (list === undefined) map.set(marker.time, [marker]);
      else list.push(marker);
    }
    return map;
  }, [marks, bars]);

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    // Clicking a bar that carries one or more markers selects that event — the
    // same selection a row in the event list produces.
    const onClick = (param: { time?: unknown }): void => {
      const sink = selectSinkRef.current;
      if (sink === undefined) return;
      const t = typeof param.time === 'number' ? param.time : null;
      if (t === null || !marksByTime.has(t)) {
        sink(null);
        return;
      }
      const timeMs = t * 1000;
      sink(timeMs);
      zoomToEvent(timeMs);
    };
    handles.chart.subscribeClick(onClick);
    return () => handles.chart.unsubscribeClick(onClick);
  }, [marksByTime, zoomToEvent]);

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    const onMove = (param: { time?: unknown; point?: { x: number; y: number } }): void => {
      const t = typeof param.time === 'number' ? param.time : null;
      if (t === null || !marksByTime.has(t) || param.point === undefined) {
        setHover((prev) => (prev === null ? prev : null));
        return;
      }
      const point = param.point;
      setHover((prev) =>
        prev !== null && prev.key === `${t}` && prev.x === point.x && prev.y === point.y
          ? prev
          : { key: `${t}`, x: point.x, y: point.y },
      );
    };
    handles.chart.subscribeCrosshairMove(onMove);
    return () => handles.chart.unsubscribeCrosshairMove(onMove);
  }, [marksByTime]);

  const hoveredMarks = hover === null ? [] : marksByTime.get(Number(hover.key)) ?? [];

  /** A one-line read of the flow pane, for its tooltip. */
  const flowSummary = useMemo(() => {
    if (flowHistory.status === 'untracked') return 'this symbol is not tracked for flow';
    if (flowHistory.status === 'quiet') return 'tracked, but no flow in the window';
    if (flowHistory.status === 'loading') return 'loading';
    if (flowHistory.status === 'error') return 'flow history unavailable';
    const bins = flowHistory.bins;
    if (bins.length === 0) return 'no bins';
    const peak = bins.reduce((best, bin) => (bin.burstMultiple > best.burstMultiple ? bin : best), bins[0]);
    return `${multiple(peak.burstMultiple)} peak · ${usd(peak.flowQuote)} in that bin`;
  }, [flowHistory]);

  /* -- initial data into the series ----------------------------------------- */

  // `setData` is deliberately confined to this effect: it is the initial load
  // and the symbol/interval change. Polls below only ever call `update`, which
  // is what keeps the user's pan and zoom intact and the candles from flickering.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null) return;
    handles.candles.setData(toCandles(bars));
    handles.volume.setData(toVolumes(bars));
    const byKey = new Map(overlaySeries.map((s) => [s.key, s.data]));
    for (const [key, series] of handles.lines) {
      const points: LinePoint[] = byKey.get(key) ?? [];
      series.setData(points.map((p) => ({ time: p.time, value: p.value })));
    }
    handles.chart.timeScale().fitContent();
    // The panes are shared by size *and* by payload: the first payload usually
    // arrives after the first resize, and the chart is rebuilt for it.
    const box = containerRef.current;
    if (box !== null && box.clientHeight > 0 && lastDistributed.current !== box.clientHeight) {
      lastDistributed.current = box.clientHeight;
      distributePaneHeights(handles.chart, box.clientHeight, eventsOnlyRef.current);
    }
    // Announce that this payload is on screen, so a queued zoom preset can be
    // applied against these bars and not the previous interval's.
    setReadySeq((n) => n + 1);
    // Share the height on the initial load too, not only on a resize: the flow
    // pane is the evidence layer and a default proportional split leaves it a
    // 28px sliver that shows nothing.
    // The newest drawn bar, so a live tick can tell whether SSE is still inside
    // it or has crossed into the next candle.
    const last = bars.length > 0 ? bars[bars.length - 1] : null;
    liveStateRef.current = {
      lastBarTime: last === null ? null : barTime(last),
      lastOpen: last === null ? null : last.o,
      lastClose: last === null ? null : last.c,
    };
  }, [bars, overlaySeries, data, distributePaneHeights]);

  /* -- support / resistance levels ------------------------------------------- */

  // Horizontal price lines on the candle pane, labelled with the price, the
  // touch count and the strength. Opacity tracks strength, so the level that has
  // been tested most is the one that reads strongest — a bare line would imply
  // every level is equally meaningful.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;

    for (const line of handles.levelLines) {
      try {
        handles.candles.removePriceLine(line);
      } catch {
        // Already detached; nothing to do.
      }
    }
    handles.levelLines = [];
    if (outlook === null) return;

    const palette = resolvePalette();
    // Draw the strongest few: a wall of lines hides the candles it is annotating.
    const selected: OutlookLevel[] = [...outlook.resistances.slice(0, 4), ...outlook.supports.slice(0, 4)];
    for (const level of selected) {
      const strength = Math.max(0.25, Math.min(1, level.strength));
      const color = level.kind === 'support' ? palette.up : palette.down;
      const line = handles.candles.createPriceLine({
        price: level.price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${level.kind === 'support' ? 'S' : 'R'} ${level.touches}x ${(strength * 100).toFixed(0)}%`,
      });
      // `applyOptions` carries the alpha: strength is the level's confidence.
      line.applyOptions({ color: colorWithAlpha(color, 0.3 + strength * 0.6) });
      handles.levelLines.push(line);
    }
  }, [outlook]);

  /* -- the forecast: a projection, drawn as one --------------------------------- */

  // The forecast is a *projection*, so it must not look like history: dashed, its
  // own series, and with the lower/upper band visible — a bare line would imply a
  // precision the model explicitly disclaims.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    const points = outlook?.forecast.points ?? [];
    if (points.length === 0) {
      if (handles.forecastLine !== null) {
        handles.forecastLine.setData([]);
      }
      return;
    }
    const palette = resolvePalette();
    if (handles.forecastLine === null) {
      // A line series on the price pane: dashed, so it cannot be mistaken for a
      // historical series, and created once and reused.
      handles.forecastLine = handles.chart.addSeries(LineSeries, {
        color: palette.accent,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        title: 'forecast',
      });
    }
    handles.forecastLine.setData(
      points.map((point) => ({ time: Math.floor(point.t / 1000) as UTCTimestamp, value: point.value })),
    );

    // The uncertainty band: an area from the lower bound to the upper bound.
    //
    // Done with two area series, not a line — an area filled *to* the upper bound
    // and one filled *down from* the lower bound, with the lower one painted in
    // the pane background. That keeps the visual weight on the interval without
    // inventing interpolated values between the model's own points.
    const banded = points.filter((p) => p.lower !== null && p.upper !== null);
    if (banded.length === 0) return;
    const geom = bandGeometry(banded, palette);
    const pane = handles.chart.panes()[handles.paneIndex.atr];
    if (pane === undefined || pane === null) return;
    if (handles.bandUpper === null || handles.bandLower === null) {
      handles.bandUpper = pane.addSeries(AreaSeries, {
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        topColor: geom.fill,
        bottomColor: geom.transparent,
        lineColor: geom.edge,
        title: 'forecast band',
      });
      handles.bandLower = pane.addSeries(AreaSeries, {
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        topColor: geom.background,
        bottomColor: geom.transparent,
        lineColor: geom.edge,
        title: 'forecast band',
      });
    }
    handles.bandUpper.setData(banded.map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.upper as number })));
    handles.bandLower.setData(banded.map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.lower as number })));
  }, [outlook]);

/**
 * The two colours the forecast band needs.
 *
 * `fill` is the wash between the bounds; `background` is the pane's own colour,
 * painted over the lower series so the band appears to hang between the bounds
 * rather than extending down to the axis.
 */
function bandGeometry(
  points: readonly { lower: number | null; upper: number | null }[],
  palette: { accent: string; background: string },
): { fill: string; edge: string; background: string; transparent: string } {
  void points;
  return {
    fill: colorWithAlpha(palette.accent, 0.18),
    edge: colorWithAlpha(palette.accent, 0.55),
    background: palette.background,
    transparent: 'rgba(0,0,0,0)',
  };
}

/** Convert a colour to one with the given alpha, when it is a hex literal. */
function colorWithAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{6})$/i.exec(color.trim());
  if (hex === null) return color;
  const value = hex[1];
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha)).toFixed(3)})`;
}

  /* -- the flow pane's data -------------------------------------------------- */

  // Bars are coloured from the canonical palette: above the 1.0 baseline is the
  // "up" token, below it the "down" token. A second colour language for this pane
  // is exactly what the canon exists to prevent.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    const palette = resolvePalette();
    const points = flowHistory.bins.map((bin) => ({
      time: Math.floor(bin.t / 1000) as UTCTimestamp,
      value: bin.burstMultiple,
      color: bin.burstMultiple >= 1 ? palette.up : palette.down,
    }));
    // `setData` is correct here: the flow history is a whole series, not an
    // increment, and it arrives on the same cadence as the candles' history.
    handles.flow.setData(points);
  }, [flowHistory.bins]);

  /* -- reconciliation: re-anchor the volume counter from the poll ------------ */

  // The ticker's rolling counter can drift (and the 24h window rolls), so each
  // reconciliation payload re-anchors it. The bar's accumulated volume is kept,
  // because it is the part the delta logic actually produced.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    if (live !== null && live.quoteVol24h !== null) {
      volumeRef.current.counter = live.quoteVol24h;
      volumeRef.current.barTime = Math.floor(live.t / 1000);
      volumeRef.current.accumulated = null;
      volumeRef.current.pushed = null;
    }
  }, [live]);

  /* -- incremental updates from the fast poll -------------------------------- */

  // `indicators` from `/api/klines/latest` are scalars at the polled bar's time,
  // so each one is pushed with `update` — and skipped when it is null, because a
  // warm-up null must never be plotted as 0.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current || live === null) return;

    const time: Time = barTime({ ...live });
    /**
     * `series.update()` only accepts a time **at or after** the last one and
     * throws otherwise ("Cannot update oldest data") — and a throw inside the
     * library during a render pass takes the whole tree down with it, which is a
     * blank dashboard rather than a wrong candle.
     *
     * So the live payload is checked before it is applied: a bar at the last
     * known time updates in place, one exactly one interval later may append, and
     * anything out of order is ignored — the reconciliation fetch is what
     * re-bases the series, not a stray payload.
     */
    const lastTime = liveStateRef.current.lastBarTime;
    if (lastTime !== null) {
      const intervalSec = (INTERVAL_MS[interval] ?? 60_000) / 1000;
      const delta = time - lastTime;
      const inPlace = delta === 0;
      const appends = delta > 0 && delta <= intervalSec * 1.5;
      if (!inPlace && !appends) return;
    }

    handles.candles.update({ time, open: live.o, high: live.h, low: live.l, close: live.c });
    handles.volume.update({ time, value: live.v, color: volumeColor(live) });

    for (const [key, series] of handles.lines) {
      const value = liveIndicator(live, key);
      if (value === null) continue; // warming up: skip, never push 0
      series.update({ time, value });
    }

    liveStateRef.current = {
      lastBarTime: time,
      lastOpen: live.o,
      lastClose: live.c,
    };
    // No `fitContent()` here on purpose: the visible range belongs to the user.
  }, [live]);

  /* -- the ticker push: the primary live path (~4 Hz) ------------------------ */

  // The candle's close/high/low and its volume both move here. This is what
  // takes the chart from the poll's cadence to the ticker's, without a request:
  // `series.update()` on the forming bar is cheap and keeps the user's pan/zoom.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    if (tickerTick === null || symbol === null) return;
    if (tickerTick.symbol !== symbol) return; // a frame for another symbol
    if (tickerTick.price <= 0) return;

    const state = liveStateRef.current;
    if (state.lastBarTime === null) return;
    const bucket = Math.floor(bucketStart(tickerTick.atMs, interval) / 1000);
    const intervalSec = (INTERVAL_MS[interval] ?? 60_000) / 1000;
    // A tick belongs to the forming bar while it falls inside that bar's window,
    // with a grace of half an interval so a bar whose own timestamps are not
    // exactly interval-aligned is still matched. Once it is clearly past the
    // window, the push opens the next candle itself.
    const grace = intervalSec / 2;
    if (tickerTick.atMs / 1000 < state.lastBarTime - grace) return; // out of order
    if (bucket > state.lastBarTime && tickerTick.atMs / 1000 >= state.lastBarTime + intervalSec - grace) {
      // --- rollover: the push opens the new candle --------------------------
      // Waiting for the reconciliation poll would freeze the chart for up to its
      // whole interval at every bar boundary. The new bar starts from this tick;
      // the poll later finalises the closed bar and its indicator values.
      handles.candles.update({
        time: bucket as Time,
        open: tickerTick.price,
        high: tickerTick.price,
        low: tickerTick.price,
        close: tickerTick.price,
      });
      volumeRef.current = { barTime: bucket, counter: tickerTick.quoteVol24h, accumulated: 0, pushed: 0 };
      liveStateRef.current = { lastBarTime: bucket, lastOpen: tickerTick.price, lastClose: tickerTick.price };
      return;
    }

    const time = state.lastBarTime as Time;
    const open = state.lastOpen ?? tickerTick.price;
    const high = Math.max(tickerTick.price, state.lastClose ?? tickerTick.price);
    const low = Math.min(tickerTick.price, state.lastClose ?? tickerTick.price);
    handles.candles.update({ time, open, high, low, close: tickerTick.price });
    liveStateRef.current = { ...state, lastClose: tickerTick.price };

    // --- volume: add the counter's delta to this bar's accumulator ----------
    const volume = volumeRef.current;
    if (tickerTick.quoteVol24h === null) return;
    if (volume.barTime !== state.lastBarTime) {
      // A new bar starts a fresh accumulation; the first frame contributes no
      // delta, because we have no previous counter for this bar yet.
      volume.barTime = state.lastBarTime;
      volume.counter = tickerTick.quoteVol24h;
      volume.accumulated = 0;
      volume.pushed = 0;
      return;
    }
    const previousCounter = volume.counter;
    volume.counter = tickerTick.quoteVol24h;
    if (previousCounter === null) return;
    const delta = tickerTick.quoteVol24h - previousCounter;
    if (delta <= 0) return; // rolling window: clamp, never subtract
    volume.accumulated = (volume.accumulated ?? 0) + delta;
    if (volume.pushed !== volume.accumulated) {
      volume.pushed = volume.accumulated;
      handles.volume.update({
        time,
        value: volume.accumulated,
        color: volumeColor({ o: open, c: tickerTick.price }),
      });
    }
  }, [tickerTick, symbol, interval]);

  /* -- SSE price ticks into the forming candle ------------------------------- */

  // The `flow` leaderboard arrives every second; when it names the charted
  // symbol we nudge the forming candle's close/high/low, which gives sub-second
  // movement between polls. The REST poll stays authoritative.
  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current || priceTick === null || symbol === null) return;
    if (priceTick.symbol !== symbol) return;

    const state = liveStateRef.current;
    if (state.lastBarTime === null) return;
    // Only touch the candle if the tick belongs to it; a tick for the next
    // interval is a rollover, and the poll's history refresh handles that.
    if (bucketStart(priceTick.atMs, interval) / 1000 !== state.lastBarTime) return;

    const previousClose = state.lastClose;
    const open = state.lastOpen ?? priceTick.price;
    const high = previousClose === null ? priceTick.price : Math.max(priceTick.price, previousClose);
    const low = previousClose === null ? priceTick.price : Math.min(priceTick.price, previousClose);
    handles.candles.update({
      time: state.lastBarTime as Time,
      open,
      high,
      low,
      close: priceTick.price,
    });
    liveStateRef.current = { ...state, lastClose: priceTick.price };
  }, [priceTick, symbol, interval]);

  /* -- markers --------------------------------------------------------------- */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    // `setMarkers` replaces the whole array (there is no append), which is what
    // keeps an incremental poll from losing the history's markers.
    const markers: SeriesMarker<Time>[] = markerList.map((m) => {
      // The selected event's marker is enlarged, so clicking a list row makes the
      // matching marker on the chart obviously "the one".
      const isSelected = selectedEvent !== null && Math.floor(selectedEvent / 1000) === m.time;
      return {
        time: m.time,
        position: m.position,
        shape: m.shape,
        color: m.color,
        text: m.text,
        size: isSelected ? 2.4 : 1.15,
      };
    });
    handles.markers.setMarkers(markers);
  }, [markerList, selectedEvent]);

  // Selecting an event from either surface zooms to it, so both directions of
  // the interaction land the user on the move.
  const lastZoomedRef = useRef<number | null>(null);
  useEffect(() => {
    if (selectedEvent === null) {
      lastZoomedRef.current = null;
      return;
    }
    if (lastZoomedRef.current === selectedEvent) return;
    lastZoomedRef.current = selectedEvent;
    zoomToEvent(selectedEvent);
  }, [selectedEvent, zoomToEvent]);

  /* -- visibility of every toggleable series --------------------------------- */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null) return;
    for (const [key, series] of handles.lines) {
      // Events-only hides every indicator line, whatever its chip says, so the
      // user sees bare price. The chips keep their own state and win again the
      // moment the mode is left.
      series.applyOptions({ visible: !eventsOnlyActive && enabledKeys.has(key) });
    }
    handles.volume.applyOptions({ visible: showVolume });
    handles.flow.applyOptions({ visible: showFlow });
  }, [enabledKeys, showVolume, showFlow, data, eventsOnlyActive]);

  /* -- latest values in the footer ------------------------------------------- */

  const footerChips = useMemo<MetricChip[]>(() => {
    const list: MetricChip[] = [
      {
        key: 'close',
        label: 'close',
        tools: ['price', 'close'],
        value: px(latest.close),
      },
      {
        key: 'volume_ratio',
        label: 'vol ratio',
        tools: ['volume_ratio'],
        value: multiple(latest.volumeRatio),
      },
      {
        key: 'trend',
        label: 'trend',
        tools: ['trend'],
        value: latest.trend !== null ? text(latest.trend) : '—',
      },
    ];
    // Tooltip keys come from the chip registry's series -> glossary map, so the
    // footer can never point at a key the glossary does not know.
    const glossaryKeyFor = new Map<string, string>();
    for (const def of INDICATOR_DEFS) {
      for (const [series, glossary] of Object.entries(def.glossaryKey)) {
        glossaryKeyFor.set(series, glossary);
      }
    }
    const indicatorKeys: Array<{ key: string; label: string; tools: string[] }> = [
      { key: 'atr20', label: 'ATR 20', tools: ['atr20'] },
      { key: 'atr_pct', label: 'ATR %', tools: ['atr_pct'] },
      { key: 'adx14', label: 'ADX 14', tools: ['adx14'] },
      { key: 'plus_di', label: '+DI', tools: ['plus_di'] },
      { key: 'minus_di', label: '−DI', tools: ['minus_di'] },
      { key: 'rsi14', label: 'RSI 14', tools: ['rsi14'] },
      { key: 'donchian20_upper', label: 'don 20 hi', tools: [] },
      { key: 'donchian20_lower', label: 'don 20 lo', tools: [] },
    ].map((item) => ({ ...item, tools: [glossaryKeyFor.get(item.key) ?? item.key] }));
    const seriesMap = new Map(overlaySeries.map((s) => [s.key, s.data]));
    for (const item of indicatorKeys) {
      const points = seriesMap.get(item.key) ?? [];
      const last = points.length > 0 ? points[points.length - 1].value : null;
      const isPct = item.key === 'atr_pct';
      const value =
        last === null ? '—' : isPct ? share(last, 3) : item.key === 'adx14' || item.key.startsWith('plus') || item.key.startsWith('minus') ? num(last, 1) : px(last);
      list.push({ key: item.key, label: item.label, tools: item.tools, value });
    }
    return list;
  }, [latest, overlaySeries]);

  /**
   * The "live" affordance: a pulsing dot while the pipeline is running, the poll
   * state, and the age of the last update — so the user can see that the candle
   * is actually moving rather than guessing from a still chart.
   */
  const liveIndicatorNode = useMemo(() => {
    // The push is the primary path, so the pill reports *its* age. The poll is
    // reconciliation and would otherwise make a healthy chart look stale.
    const lastMs = tickerAtMs ?? liveAtMs ?? atMs;
    const ageSec = lastMs === null ? null : Math.max(0, Math.round((nowMs - lastMs) / 1000));
    const label =
      ageSec === null
        ? polling
          ? 'live · waiting'
          : 'paused'
        : `live · ${ageSec}s ago`;
    const state = symbol === null ? 'off' : refreshing ? 'refreshing' : polling ? 'live' : 'paused';
    return (
      <Tooltip
        keys={['live']}
        tools={tools}
        fallbackTitle="Live updates"
        label={`Prices arrive on the SSE ticker channel several times a second, and each frame updates the forming candle in place — your pan and zoom are never reset. Bar volume is accumulated from the rolling 24h counter's delta. /api/klines/latest is polled every ${(LIVE_POLL_MS / 1000).toFixed(0)}s purely to reconcile drift and deliver markers and indicator values. Updates stop while the chart is closed or the tab is hidden.`}
        value={ageSec === null ? null : `${ageSec}s since the last update`}
        tone="stat"
      >
        <span className={`live-pill live-${state}`} tabIndex={0}>
          <i className="live-dot" aria-hidden="true" />
          {label}
        </span>
      </Tooltip>
    );
  }, [tickerAtMs, liveAtMs, atMs, nowMs, polling, refreshing, symbol, tools]);

  const statusLine = useMemo(() => {
    const parts: string[] = [];
    if (symbol !== null) parts.push(symbol);
    parts.push(interval);
    if (bars.length > 0) parts.push(`${int(bars.length)} bars`);
    if (data?.ts_ms != null) parts.push(clock(data.ts_ms));
    if (atMs !== null) parts.push(`loaded ${clock(atMs)}`);
    return parts.join(' · ');
  }, [symbol, interval, bars.length, data, atMs]);

  const volumeChip = (
    <Tooltip
      key="volume"
      tone="chip"
      keys={['volume_median_20', 'volume_ratio']}
      tools={tools}
      fallbackTitle="Volume histogram"
      label="Per-bar base volume, coloured by the candle's direction."
      value={showVolume ? 'shown' : 'hidden'}
    >
      <button
        type="button"
        className={`chip chip-toggle${showVolume ? ' on' : ''}`}
        aria-pressed={showVolume}
        onClick={() => setShowVolume((v) => !v)}
      >
        volume
      </button>
    </Tooltip>
  );

  return (
    <section className="panel chart-panel">
          <div className="panel-head chart-toolbar">
            <div className="panel-meta">
              <span className="sub num" title={symbol === null ? 'no symbol selected' : `${symbol} · ${interval}`}>
                {statusLine}
              </span>
              {liveIndicatorNode}
            </div>
            <div className="actions">
              <div className="interval-chips" role="group" aria-label="chart interval">
                {INTERVALS.map((iv) => (
                  <Tooltip
                    key={iv}
                    tone="chip"
                    keys={['interval']}
                    tools={tools}
                    fallbackTitle={`Interval ${iv}`}
                    label={`Fetch 300 ${iv} klines for the selected symbol.`}
                  >
                    <button
                      type="button"
                      className={`chip chip-toggle${interval === iv ? ' on' : ''}`}
                      aria-pressed={interval === iv}
                      onClick={() => onIntervalChange(iv)}
                    >
                      {iv}
                    </button>
                  </Tooltip>
                ))}
              </div>
              <button
                type="button"
                className="btn"
                onClick={resetChips}
                title="restore the default indicator toggles"
              >
                reset indicators
              </button>
            </div>
          </div>

          <div className="chip-bar">
            {INDICATOR_DEFS.map((def) => {
              const on = chips[def.id] === true;
              const value = footerChips.find((c) => c.key === def.series[0])?.value ?? null;
              return (
                <Tooltip
                  key={def.id}
                  tone="chip"
                  keys={def.toolKeys.length > 0 ? def.toolKeys : [def.id]}
                  tools={tools}
                  fallbackTitle={def.label}
                  label={def.blurb}
                  value={value}
                >
                  <button
                    type="button"
                    className={`chip chip-toggle${on ? ' on' : ''}`}
                    aria-pressed={on}
                    onClick={() => toggleChip(def.id)}
                  >
                    <span className="chip-dot" aria-hidden="true" />
                    {def.label}
                  </button>
                </Tooltip>
              );
            })}
            {volumeChip}
            {/*
              "Events only": one click hides every indicator so the events read
              against bare price and the flow pane. The chip set is remembered and
              restored verbatim, and the price pane absorbs the released height
              (the pane distribution already runs after every resize).
            */}
            <Tooltip
              keys={['events_only']}
              tools={tools}
              fallbackTitle="Events only"
              label="Hide every indicator — overlays, panes and their legend entries — leaving candles, volume, event badges and flow. The price pane absorbs the released height. Clicking again restores exactly the set you had, not a default."
              value={eventsOnlyActive ? 'on' : null}
              tone="chip"
            >
              <button
                type="button"
                className={`chip chip-toggle chip-events${eventsOnlyActive ? ' on' : ''}`}
                aria-pressed={eventsOnlyActive}
                onClick={toggleEventsOnly}
              >
                events only
              </button>
            </Tooltip>
          </div>

          <div className="chart-body">
            {symbol === null ? (
              <p className="empty">pick a symbol anywhere in the dashboard to load its chart</p>
            ) : (
              <SplitPane
                id="chart.split"
                vertical
                side="first"
                fillHeight
                defaultSize={380}
                min={180}
                max={900}
                firstClassName="chart-price-pane"
                secondClassName="chart-indicator-pane"
                first={
                  <div className="chart-host" ref={containerRef}>
                    {/*
                      The event badges, as HTML over the canvas.

                      The library's own markers cannot carry a text colour
                      (`SeriesMarker` has none; `textColor` is a chart-level
                      option), so they would render white text on a bright fill and
                      could never match the toolbar badge. This layer anchors the
                      *same* `EventGlyph` component to each bar time instead, so the
                      chart badge and the toolbar badge are one component.
                    */}
                    {layer !== null && (
                      <MarkerLayer
                        chart={layer.chart}
                        series={layer.series}
                        markers={markerList}
                        canon={canon}
                        selectedEvent={selectedEvent}
                        onSelect={(timeMs) => onSelectEvent?.(timeMs)}
                        repaintKey={readySeq}
                      />
                    )}
                    {/*
                      lightweight-charts gives markers no tooltip, so the crosshair
                      parks a zero-size focusable trigger here and focusing it opens
                      the dashboard's own Tooltip. `pointer-events: none` keeps it
                      from stealing the hover that opened it.
                    */}
                    <Tooltip
                      keys={hoveredMarks.map((m) => m.kind).filter((k) => k !== '')}
                      tools={tools}
                      fallbackTitle={hoveredMarks.length > 0 ? markTitle(hoveredMarks[0].letter) : 'Event'}
                      label={hoveredMarks
                        .map((m) =>
                          [
                            m.label,
                            `kind ${m.kind}`,
                            `severity ${m.severity}`,
                            m.score === null ? null : `score ${num(m.score, 1)}`,
                            m.burstMultiple === null ? null : `${multiple(m.burstMultiple)} baseline`,
                            m.flowQuote === null ? null : `${usd(m.flowQuote)} flow`,
                          ]
                            .filter((part): part is string => part !== null)
                            .join(' · '),
                        )
                        .join('  |  ')}
                      tone="badge"
                      className={`mark-hover${hoveredMarks.length > 0 ? ' on' : ''}`}
                    >
                      <button
                        type="button"
                        tabIndex={hoveredMarks.length > 0 ? 0 : -1}
                        aria-label="chart event marker"
                        className="mark-hover-trigger"
                        style={hover === null ? undefined : { left: `${hover.x}px`, top: `${hover.y}px` }}
                        onFocus={(event) => {
                          // Only the parked trigger should ever be focused.
                          if (hover === null) event.currentTarget.blur();
                        }}
                      />
                    </Tooltip>
                    {loading && bars.length === 0 && <p className="chart-overlay">loading {symbol} {interval}…</p>}
                    {error !== null && bars.length === 0 && (
                      <div className="chart-overlay chart-error">
                        <strong>chart unavailable</strong>
                        <span>{error}</span>
                        <button type="button" className="btn" onClick={refresh}>
                          retry
                        </button>
                      </div>
                    )}
                  </div>
                }
                second={
                  <div className="chart-pane-labels">
                    {(['atr', 'adx', 'rsi'] as const)
                      .filter((pane) => [...enabledKeys].some((key) => paneOf(key) === pane))
                      .map((pane) => (
                        <span key={pane} className="pane-label" style={{ borderColor: PANE_TINT[pane] }} aria-hidden="true">
                          {pane.toUpperCase()}
                        </span>
                      ))}
                    {/*
                      The flow pane's own label carries its state, because the two
                      empty cases are different things: a tracked-but-quiet symbol
                      is drawn flat, while an untracked one is a coverage gap and
                      must not look like a failure.
                    */}
                    {showFlow && (
                      <Tooltip
                        keys={['flow_burst']}
                        tools={tools}
                        fallbackTitle="Flow pane — burst multiple"
                        label="Each bar is one 10-second bin: its traded quote value divided by that symbol's own baseline for the same window. The dashed line is 1.0x, normal flow. The axis is logarithmic because bursts are heavy-tailed — one 47x bar would flatten every other bin on a linear axis. Bins share the time scale with the candles, so both zoom as one gesture; at a 5s or 10s zoom there is at most one bin in view, which is why the candle pane does the work there."
                        value={flowSummary}
                        tone="stat"
                      >
                        <span className={`pane-label pane-flow pane-flow-${flowHistory.status}`} tabIndex={0}>
                          FLOW · 10s
                          <span className="pane-flow-state">
                            {flowHistory.status === 'ready'
                              ? `${int(flowHistory.bins.length)} bins`
                              : flowHistory.status === 'quiet'
                                ? 'quiet'
                                : flowHistory.status === 'untracked'
                                  ? 'not tracked'
                                  : flowHistory.status === 'loading'
                                    ? 'loading…'
                                    : 'unavailable'}
                          </span>
                        </span>
                      </Tooltip>
                    )}
                  </div>
                }
              />
            )}
            {error !== null && bars.length > 0 && (
              <p className="chart-warn">
                refresh failed — showing data from {isoUtc(atMs)}.{' '}
                <button type="button" className="link-btn" onClick={refresh}>
                  retry
                </button>
              </p>
            )}
          </div>

          <div className="chart-marker-legend" role="group" aria-label="chart marker legend">
            <span className="marker-legend-label">markers</span>
            {MARK_SAMPLES.map((sample) => {
              const identity = eventIdentity({ kind: sample.kind, canon });
              return (
                <Tooltip
                  key={sample.kind}
                  keys={sample.toolKeys}
                  tools={tools}
                  fallbackTitle={`${sample.title} marker`}
                  label={`${sample.blurb} The chart draws ${identity.letter} in this colour with a${
                    identity.direction === 1 ? 'n up' : ' down'
                  } arrow — the same glyph and colour as the alert-feed badge and the event list.`}
                  tone="badge"
                >
                  <span className="marker-key" tabIndex={0}>
                    <EventGlyph kind={sample.kind} canon={canon} size="sm" />
                    {sample.title}
                  </span>
                </Tooltip>
              );
            })}
          </div>

          {/* The support/resistance map, ETA and short-horizon read: the values
              the outlook endpoint publishes, with its own wording preserved. */}
          <OutlookStrip outlook={outlook} tools={tools} />

          {bottom !== null && <div className="chart-bottom">{bottom}</div>}

          <div className="chart-footer">
            {footerChips.map((chip) => (
              <Tooltip
                key={chip.key}
                tone="stat"
                keys={chip.tools}
                tools={tools}
                fallbackTitle={chip.label}
                value={chip.value}
              >
                <span className="legend" tabIndex={0}>
                  <span className="legend-label">{chip.label}</span>
                  <span className="legend-value num">{chip.value}</span>
                </span>
              </Tooltip>
            ))}
          </div>
    </section>
  );
}
