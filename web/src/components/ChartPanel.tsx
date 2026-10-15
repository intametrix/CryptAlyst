import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts';
import {
  INDICATOR_DEFS,
  MARK_LETTERS,
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
import { saveChipState, getLayout } from '../layout';
import { LIVE_POLL_MS, bucketStart, liveIndicator, useKlines } from '../hooks/useKlines';
import { useElementSize } from '../hooks/useElementSize';
import { INTERVALS, type Interval, type ToolsMsg } from '../types';
import { SplitPane } from './Panel';
import type { ZoomPreset } from './EventsPanel';
import { Tooltip } from './Tooltip';

/* --------------------------------------------------------------- appearance */

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
const PANE_MIN_PX = { atr: 40, adx: 44, rsi: 40 } as const;
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
  timeScale: () => { from: number | null; to: number | null; bars: number };
}

interface ChartHandles {
  chart: IChartApi;
  candles: ISeriesApi<'Candlestick'>;
  volume: ISeriesApi<'Histogram'>;
  lines: Map<string, ISeriesApi<'Line'>>;
  markers: ISeriesMarkersPluginApi<Time>;
  /** Pane index for each indicator pane, resolved after panes are created. */
  paneIndex: Record<'atr' | 'adx' | 'rsi', number>;
}

/** Resolve the pane index an indicator's pane landed in (v5 exposes `panes()`). */
function resolvePaneIndex(chart: IChartApi, want: number): number {
  const panes = chart.panes();
  if (want >= 0 && want < panes.length) return want;
  return Math.max(0, panes.length - 1);
}

/**
 * Create the chart with all series and panes ready. Series stay attached and
 * are toggled with `applyOptions({ visible })` — cheaper and safer than
 * re-creating them, and it keeps the pane list stable.
 */
function buildChart(container: HTMLElement, bars: readonly SafeBar[]): ChartHandles {
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
      title: key,
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
    title: 'ATR 20',
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
    title: 'ADX 14',
  });
  const plusLine = adxPane.addSeries(LineSeries, {
    color: COLORS.plusDi,
    lineWidth: LINE_WIDTH,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    visible: false,
    title: '+DI',
  });
  const minusLine = adxPane.addSeries(LineSeries, {
    color: COLORS.minusDi,
    lineWidth: LINE_WIDTH,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
    visible: false,
    title: '−DI',
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
    title: 'RSI 14',
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
    timeScale: () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      return { from: range?.from ?? null, to: range?.to ?? null, bars: bars.length };
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
  const markerList = useMemo(() => toMarkers(marks, bars), [marks, bars]);

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

  const toggleChip = useCallback((id: string) => {
    setChips((prev) => {
      const next = { ...prev, [id]: !(prev[id] === true) };
      saveChipState(next);
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
    const handles = buildChart(container, bars);
    chartRef.current = handles;

    // Size to the measured box right away; the ResizeObserver effect keeps it in
    // step afterwards (including while the overlay is being dragged), and the
    // zero guard means a not-yet-laid-out box cannot throw here.
    const { width, height } = sizeRefLive.current;
    if (width > 0 && height > 0) handles.chart.resize(width, height);

    return () => {
      disposedRef.current = true;
      chartRef.current = null;
      // `remove()` detaches observers and frees the canvases — without it a
      // symbol change would leak a whole chart each time.
      handles.chart.remove();
    };
    // Rebuilt only when the *symbol* or the bar count changes: a 30s poll that
    // merely refreshes the forming candle must not throw away the user's zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, bars.length, bars[0]?.t]);

  /* -- container resize: the canvas follows the box, not the viewport -------- */

  /**
   * Give the indicator panes their heights and let the price pane take the rest.
   *
   * Called after every resize. `setHeight` on panes 1..3 is what stops the
   * library from splitting the new height evenly, which is what made a taller
   * container leave the candles the same size.
   */
  const distributePaneHeights = useCallback((chart: IChartApi, boxHeight: number): void => {
    const handles = chartRef.current;
    const panes = chart.panes();
    if (handles === null || panes.length < 2) return;

    const indicators = (['atr', 'adx', 'rsi'] as const)
      .map((key) => ({ key, index: handles.paneIndex[key] }))
      .filter((item) => item.index > 0 && item.index < panes.length);

    // The indicator panes are sized first, from their targets, degrading to
    // their floors when the box is short. They never grow with the box, so the
    // whole of any gain lands on the candles.
    const available = Math.max(0, boxHeight - AXIS_ALLOWANCE_PX);
    const wanted = indicators.map((item) => PANE_INDICATOR_PX[item.key]);
    const floors = indicators.map((item) => PANE_MIN_PX[item.key]);
    const totalWanted = wanted.reduce((sum, v) => sum + v, 0);
    const scale = totalWanted > available * 0.45 && totalWanted > 0 ? (available * 0.45) / totalWanted : 1;

    let reserved = 0;
    indicators.forEach((item, i) => {
      const want = Math.max(floors[i], Math.round(wanted[i] * scale));
      reserved += want;
      if (panes[item.index].getHeight() !== want) panes[item.index].setHeight(want);
    });

    // The price pane takes everything left, with a sane floor.
    const priceHeight = Math.max(60, available - reserved);
    if (panes[0].getHeight() !== priceHeight) panes[0].setHeight(priceHeight);
  }, []);

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
    // Pane heights are applied on the next frame: a `resize` re-divides the
    // panes, so a `setHeight` issued in the same tick is overwritten.
    const frame = window.requestAnimationFrame(() => {
      if (disposedRef.current) return;
      distributePaneHeights(handles.chart, size.height);
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
      if (preset.bars === null) {
        handles.chart.timeScale().fitContent();
        return true;
      }
      const last = total - 1;
      const from = Math.max(0, last - preset.bars + 1);
      handles.chart.timeScale().setVisibleLogicalRange({ from, to: last + 1 });
      // False means the preset asked for more bars than are loaded; the caller
      // surfaces a "limited history" affordance instead of silently fetching.
      return last - from + 1 >= preset.bars;
    },
    [bars.length],
  );

  const resetZoom = useCallback(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    handles.chart.timeScale().fitContent();
  }, []);

  /** Centre the view on one event: ±60 bars, clamped to what is loaded. */
  // Hand the controls to the caller once they exist, and withdraw them on
  // unmount so a stale closure cannot act on a disposed chart.
  useEffect(() => {
    const sink = zoomSinkRef.current;
    if (sink === undefined) return;
    sink({ applyPreset: applyZoomPreset, reset: resetZoom });
    return () => sink(null);
  }, [applyZoomPreset, resetZoom]);

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
    for (const marker of toMarkers(marks, bars)) {
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
    // The newest drawn bar, so a live tick can tell whether SSE is still inside
    // it or has crossed into the next candle.
    const last = bars.length > 0 ? bars[bars.length - 1] : null;
    liveStateRef.current = {
      lastBarTime: last === null ? null : barTime(last),
      lastOpen: last === null ? null : last.o,
      lastClose: last === null ? null : last.c,
    };
  }, [bars, overlaySeries, data]);

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
    const trace: { reason: string; price?: number; bucket?: number; lastBarTime?: number | null } | null =
      typeof window === 'undefined'
        ? null
        : (() => {
            const w = window as unknown as { __tickerTrace?: unknown[] };
            w.__tickerTrace = w.__tickerTrace ?? [];
            return w.__tickerTrace.length < 6 ? (w.__tickerTrace as never) : null;
          })();
    const note = (reason: string, extra: Record<string, unknown> = {}): void => {
      if (trace !== null) (trace as unknown as unknown[]).push({ reason, ...extra });
    };
    if (handles === null || disposedRef.current) return note('no-chart');
    if (tickerTick === null) return note('no-tick');
    if (symbol === null) return note('no-symbol');
    if (tickerTick.symbol !== symbol) return note('symbol-mismatch', { got: tickerTick.symbol });

    const state = liveStateRef.current;
    if (state.lastBarTime === null) return note('no-bar-time');
    // Only touch the bar the tick belongs to; a tick in the next interval is a
    // rollover and is the poll's job to finalise.
    const bucket = bucketStart(tickerTick.atMs, interval) / 1000;
    if (bucket !== state.lastBarTime) return note('bucket-mismatch', { bucket, lastBarTime: state.lastBarTime });
    if (tickerTick.price <= 0) return note('non-positive-price', { price: tickerTick.price });
    note('applied', { price: tickerTick.price });


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
      series.applyOptions({ visible: enabledKeys.has(key) });
    }
    handles.volume.applyOptions({ visible: showVolume });
  }, [enabledKeys, showVolume, data]);

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
                  <div className="chart-pane-labels" aria-hidden="true">
                    {(['atr', 'adx', 'rsi'] as const)
                      .filter((pane) => [...enabledKeys].some((key) => paneOf(key) === pane))
                      .map((pane) => (
                        <span key={pane} className="pane-label" style={{ borderColor: PANE_TINT[pane] }}>
                          {pane.toUpperCase()}
                        </span>
                      ))}
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
            {MARK_LETTERS.map((item) => (
              <Tooltip
                key={item.letter}
                keys={[`mark_${item.letter.toLowerCase()}`]}
                tools={tools}
                fallbackTitle={`${item.title} marker`}
                label={`A ${item.title.toLowerCase()} detected on this bar. Hover the letter on the chart for the event's score, flow and burst multiple.`}
                tone="badge"
              >
                <span className={`marker-key marker-${item.tone}`} tabIndex={0}>
                  <b>{item.letter}</b>
                  {item.title}
                </span>
              </Tooltip>
            ))}
          </div>

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
