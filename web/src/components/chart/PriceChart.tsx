import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  type UTCTimestamp,
} from 'lightweight-charts';
import {
  barTime,
  priceFormatFor,
  toCandles,
  toVolumes,
  volumeColor,
  type AlignedSeries,
  type ChartMarker,
  type LinePoint,
  type SafeBar,
} from '../../chartData';
import type { ColorCanon } from '../../colors';
import { useElementSize } from '../../hooks/useElementSize';
import {
  INTERVAL_MS,
  bucketStart,
  liveIndicator,
  type KlinesState,
  type LiveBar,
} from '../../hooks/useKlines';
import type { Interval } from '../../types';
import { MarkerLayer } from '../MarkerLayer';

/* --------------------------------------------------------------- appearance */

const COLORS = {
  up: '#2ee06a',
  down: '#ff4d5e',
  donchian: '#4d8dfd',
  donchianSlow: '#8b5cf6',
  ema20: '#22d3ee',
  ema50: '#f59e0b',
} as const;

const LINE_WIDTH = 1 as const;

/** Every overlay line the new popup knows about, keyed by its wire series. */
export const OVERLAY_KEYS = [
  'donchian20_upper',
  'donchian20_lower',
  'donchian55_upper',
  'donchian55_lower',
  'ema20',
  'ema50',
] as const;

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
    default:
      return { color: COLORS.ema50, width: LINE_WIDTH };
  }
}

/* ------------------------------------------------------------ chart handles */

interface ChartHandles {
  chart: IChartApi;
  candles: ISeriesApi<'Candlestick'>;
  volume: ISeriesApi<'Histogram'>;
  lines: Map<string, ISeriesApi<'Line'>>;
  markers: ISeriesMarkersPluginApi<Time>;
}

/**
 * One chart, ONE pane: candles + volume histogram overlay + the overlay lines
 * (Donchian 20/55, EMA 20/50) + markers. No pane allocator — the candles take
 * the whole region minus the time axis.
 */
function buildChart(
  container: HTMLElement,
  bars: readonly SafeBar[],
): ChartHandles {
  const chart = createChart(container, {
    autoSize: false,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#8492a9',
      fontFamily:
        'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 11,
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

  const lines = new Map<string, ISeriesApi<'Line'>>();
  for (const key of OVERLAY_KEYS) {
    const style = lineStyleFor(key);
    lines.set(
      key,
      chart.addSeries(LineSeries, {
        color: style.color,
        lineWidth: style.width,
        lineStyle: style.lineStyle ?? LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: false,
      }),
    );
  }

  const markers = createSeriesMarkers(candles, []);

  return { chart, candles, volume, lines, markers };
}

/* ------------------------------------------------------------------ exports */

/** Zoom controls the chart exposes to the popup header. */
export interface ChartZoomControls {
  applyPreset: (preset: ZoomPresetLike) => boolean;
  canZoomLocally: (preset: ZoomPresetLike) => boolean;
  /** Bumped every time a fresh payload has been painted into the series. */
  readySeq: number;
  reset: () => void;
}

/** The slice of the zoom preset the chart needs (avoids a zoom.ts type cycle). */
export interface ZoomPresetLike {
  mode: 'range' | 'fit';
  bars?: number;
}

export interface PriceChartProps {
  symbol: string;
  interval: Interval;
  canon?: ColorCanon | null;
  priceTick?: { symbol: string; price: number; atMs: number } | null;
  tickerTick?: { symbol: string; price: number; quoteVol24h: number | null; atMs: number } | null;
  tickerAtMs?: number | null;
  /** The klines state, lifted to the popup so the data column reads it too. */
  klines: KlinesState;
  /** Bars parsed from the payload. */
  bars: SafeBar[];
  /** Every mark as a drawable marker, oldest first. */
  markerList: ChartMarker[];
  /** The aligned overlay series (donchian + EMA lines). */
  overlaySeries: AlignedSeries[];
  /** Chip visibility: volume overlay / donchian / ema. */
  showVol: boolean;
  showDon: boolean;
  showEma: boolean;
  /** The shared selected event (bar open time, epoch ms), or null. */
  selectedEvent?: number | null;
  onSelectEvent?: (time: number | null) => void;
  onZoomControls?: (controls: ChartZoomControls | null) => void;
}

/**
 * The candle chart: the subject of the popup. It owns the
 * lightweight-charts instance and the live forming-candle update path
 * (ticker push primary, SSE price tick fallback, REST poll reconciliation),
 * ported from ChartPanel without the pane machinery.
 */
export function PriceChart({
  symbol,
  interval,
  canon = null,
  priceTick = null,
  tickerTick = null,
  klines,
  bars,
  markerList,
  overlaySeries,
  showVol,
  showDon,
  showEma,
  selectedEvent = null,
  onSelectEvent,
  onZoomControls,
}: PriceChartProps) {
  const chartRef = useRef<ChartHandles | null>(null);
  const disposedRef = useRef(false);
  const selectSinkRef = useRef(onSelectEvent);
  selectSinkRef.current = onSelectEvent;
  const zoomSinkRef = useRef(onZoomControls);
  zoomSinkRef.current = onZoomControls;

  const { ref: containerRef, size } = useElementSize<HTMLDivElement>({ box: 'content-box' });
  const sizeRefLive = useRef(size);
  sizeRefLive.current = size;

  const { data, loadSeq, loading, error, atMs, live, refreshing, refresh } = klines;

  /** The forming candle's identity, shared by the poll and the SSE tick. */
  const liveStateRef = useRef<{ lastBarTime: number | null; lastOpen: number | null; lastClose: number | null }>({
    lastBarTime: null,
    lastOpen: null,
    lastClose: null,
  });
  /**
   * Volume accumulation for the forming bar: `quote_vol_24h` is a rolling 24h
   * total, so only its delta can be attributed to the bar. A negative delta is
   * clamped to 0 (the window legitimately rolls). The poll re-anchors.
   */
  const volumeRef = useRef<{
    barTime: number | null;
    counter: number | null;
    accumulated: number | null;
    pushed: number | null;
  }>({ barTime: null, counter: null, accumulated: null, pushed: null });
  /** The most recent incremental payload, for the ticker's indicator lookups. */
  const lastLiveRef = useRef<LiveBar | null>(null);
  const overlayTailRef = useRef(new Map<string, number>());
  const overlayPointsRef = useRef(new Map<string, LinePoint[]>());
  /** The payload signature each overlay series was last given. */
  const overlayAppliedRef = useRef(new Map<string, string>());
  /** Mirrors the chart's follow flag for the ticker path outside the closure. */
  const followMirror = useRef(true);
  /** Incremented after each payload is painted, for queued zoom presets. */
  const [readySeq, setReadySeq] = useState(0);
  /** The chart handles, exposed to the HTML marker layer. */
  const [layer, setLayer] = useState<{ chart: IChartApi; series: ISeriesApi<'Candlestick'> } | null>(null);

  /* -- create / destroy ------------------------------------------------------ */

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || bars.length === 0) return;

    disposedRef.current = false;
    const built = buildChart(container, bars);
    const handles: ChartHandles = built;
    // `buildChart` seeds a local follow flag; re-derive it authoritatively here.
    followMirror.current = true;
    {
      const scale = handles.chart.timeScale();
      const atEdge = (): boolean => {
        const range = scale.getVisibleLogicalRange();
        if (range === null) return true;
        const total = handles.candles.data().length;
        return total === 0 || range.to >= total - 1;
      };
      followMirror.current = atEdge();
      scale.subscribeVisibleLogicalRangeChange(() => {
        followMirror.current = atEdge();
      });
    }
    chartRef.current = handles;
    setLayer({ chart: handles.chart, series: handles.candles });

    const { width, height } = sizeRefLive.current;
    if (width > 0 && height > 0) handles.chart.resize(width, height);

    return () => {
      disposedRef.current = true;
      chartRef.current = null;
      setLayer(null);
      // `remove()` detaches observers and frees the canvases.
      handles.chart.remove();
    };
    // Rebuilt only on a real reload (symbol change / interval change), never on
    // a poll refresh: `loadSeq` is exactly that signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, loadSeq]);

  /* -- container resize ------------------------------------------------------ */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    if (size.width <= 0 || size.height <= 0) return;
    handles.chart.resize(size.width, size.height);
  }, [size.width, size.height]);

  /* -- initial data into the series ------------------------------------------ */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null) return;
    handles.candles.setData(toCandles(bars));
    handles.volume.setData(toVolumes(bars));
    const byKey = new Map(overlaySeries.map((s) => [s.key, s.data]));
    for (const [key, series] of handles.lines) {
      const points = byKey.get(key) ?? [];
      const signature = `${symbol}|${interval}|${points.length}|${points[0]?.time ?? 0}|${points[points.length - 1]?.time ?? 0}`;
      if (overlayAppliedRef.current.get(key) === signature) continue;
      series.setData(points.map((p) => ({ time: p.time, value: p.value })));
      overlayAppliedRef.current.set(key, signature);
      if (points.length > 0) {
        overlayTailRef.current.set(key, points[points.length - 1].value);
        overlayPointsRef.current.set(key, points.slice());
      }
    }
    handles.chart.timeScale().fitContent();
    setReadySeq((n) => n + 1);
    const last = bars.length > 0 ? bars[bars.length - 1] : null;
    liveStateRef.current = {
      lastBarTime: last === null ? null : barTime(last),
      lastOpen: last === null ? null : last.o,
      lastClose: last === null ? null : last.c,
    };
    // Keyed on the load, not on `bars` (which changes identity every poll).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadSeq]);

  /* -- overlay visibility ---------------------------------------------------- */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null) return;
    handles.volume.applyOptions({ visible: showVol });
    for (const [key, series] of handles.lines) {
      const on = key.startsWith('donchian') ? showDon : key.startsWith('ema') ? showEma : false;
      series.applyOptions({ visible: on });
    }
  }, [showVol, showDon, showEma, loadSeq]);

  /* -- zoom controls ---------------------------------------------------------- */

  const applyZoomPreset = useCallback((preset: ZoomPresetLike): boolean => {
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
    handles.chart.timeScale().setVisibleLogicalRange({ from, to: last + 1 });
    return last - from + 1 >= preset.bars;
  }, [bars.length]);

  const canZoomLocally = useCallback(
    (preset: ZoomPresetLike): boolean => preset.mode !== 'fit' && preset.bars !== undefined,
    [],
  );

  const resetZoom = useCallback(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    handles.chart.timeScale().fitContent();
  }, []);

  useEffect(() => {
    const sink = zoomSinkRef.current;
    if (sink === undefined) return;
    sink({ applyPreset: applyZoomPreset, canZoomLocally, reset: resetZoom, readySeq });
    return () => sink(null);
  }, [applyZoomPreset, canZoomLocally, resetZoom, readySeq]);

  /* -- zoom to an event -------------------------------------------------------- */

  /** A stable bar time -> logical index map for the loaded history. */
  const barIndexByTime = useMemo(() => {
    const map = new Map<number, number>();
    bars.forEach((bar, i) => map.set(barTime(bar), i));
    return map;
  }, [bars]);

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

  // Selecting an event from either surface zooms to it.
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

  /* -- click / crosshair selection -------------------------------------------- */

  const marksByTime = useMemo(() => {
    const map = new Map<number, ChartMarker[]>();
    for (const marker of markerList) {
      const list = map.get(marker.time);
      if (list === undefined) map.set(marker.time, [marker]);
      else list.push(marker);
    }
    return map;
  }, [markerList]);

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
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

  /* -- markers ----------------------------------------------------------------- */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    // `setMarkers` replaces the whole array (there is no append), which is what
    // keeps an incremental poll from losing the history's markers.
    const markers: SeriesMarker<Time>[] = markerList.map((m) => {
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

  /* -- overlay tail carry-forward ----------------------------------------------- */

  /**
   * Carry every overlay forward to a bar the payload has not covered yet.
   *
   * A new candle opens before the server has computed its indicator values, so
   * the line is extended at its last reading — that keeps the tail glued to the
   * candle. (The installed library silently discards whitespace points, so a
   * line always ends at its last real point.)
   */
  const extendOverlaysTo = useCallback((handles: ChartHandles, time: UTCTimestamp, liveValue: (key: string) => number | null): void => {
    for (const [key, series] of handles.lines) {
      const points = overlayPointsRef.current.get(key);
      if (points === undefined || points.length === 0) continue;
      const tail = points[points.length - 1];
      if (tail.time === time) continue; // already on this bar
      const value = liveValue(key) ?? overlayTailRef.current.get(key);
      if (value === undefined || !Number.isFinite(value)) continue;
      if (time < tail.time) continue; // only forward
      const next = [...points, { time, value }];
      overlayPointsRef.current.set(key, next);
      overlayTailRef.current.set(key, value);
      series.update({ time, value });
    }
  }, []);

  /* -- reconciliation: re-anchor the volume counter from the poll --------------- */

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

  /* -- incremental updates from the fast poll ----------------------------------- */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current || live === null) return;

    const time: Time = barTime({ ...live });
    // `series.update()` only accepts a time at or after the last one, so an
    // out-of-order payload is ignored here; the reconciliation fetch re-bases.
    const lastTime = liveStateRef.current.lastBarTime;
    if (lastTime !== null) {
      const intervalSec = (INTERVAL_MS[interval] ?? 60_000) / 1000;
      const delta = time - lastTime;
      const inPlace = delta === 0;
      const appends = delta > 0 && delta <= intervalSec * 1.5;
      if (!inPlace && !appends) return;
    }

    lastLiveRef.current = live;
    handles.candles.update({ time, open: live.o, high: live.h, low: live.l, close: live.c });
    handles.volume.update({ time, value: live.v, color: volumeColor(live) });

    for (const [key, series] of handles.lines) {
      const value = liveIndicator(live, key);
      if (value === null) continue; // warming up: skip, never push 0
      series.update({ time, value });
      overlayTailRef.current.set(key, value);
    }
    extendOverlaysTo(handles, time, (key) => liveIndicator(live, key));

    liveStateRef.current = { lastBarTime: time, lastOpen: live.o, lastClose: live.c };
    // No `fitContent()` here on purpose: the visible range belongs to the user.
  }, [live, extendOverlaysTo, interval]);

  /* -- the ticker push: the primary live path (~4 Hz) ---------------------------- */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current) return;
    if (tickerTick === null || tickerTick.price <= 0) return;
    if (tickerTick.symbol !== symbol) return; // a frame for another symbol

    const state = liveStateRef.current;
    if (state.lastBarTime === null) return;
    const bucket = Math.floor(bucketStart(tickerTick.atMs, interval) / 1000);
    const intervalSec = (INTERVAL_MS[interval] ?? 60_000) / 1000;
    // A tick belongs to the forming bar while it falls inside that bar's
    // window, with a grace of half an interval. Past that, the push opens the
    // next candle itself rather than waiting for the reconciliation poll.
    const grace = intervalSec / 2;
    if (tickerTick.atMs / 1000 < state.lastBarTime - grace) return; // out of order
    if (bucket > state.lastBarTime && tickerTick.atMs / 1000 >= state.lastBarTime + intervalSec - grace) {
      // --- rollover: the push opens the new candle ---------------------------
      handles.candles.update({
        time: bucket as Time,
        open: tickerTick.price,
        high: tickerTick.price,
        low: tickerTick.price,
        close: tickerTick.price,
      });
      volumeRef.current = { barTime: bucket, counter: tickerTick.quoteVol24h, accumulated: 0, pushed: 0 };
      // The ticker carries no indicator values: each line is carried forward at
      // its last reading and the next payload replaces it.
      extendOverlaysTo(handles, bucket as UTCTimestamp, (key) => liveIndicator(lastLiveRef.current, key));
      liveStateRef.current = { lastBarTime: bucket, lastOpen: tickerTick.price, lastClose: tickerTick.price };
      // A new bar may move the viewport — only for a reader at the live edge,
      // and only by the same sliver the library would have used.
      if (followMirror.current) {
        const scale = handles.chart.timeScale();
        const range = scale.getVisibleLogicalRange();
        if (range !== null && range.to - range.from > 0) {
          const width = range.to - range.from;
          const total = handles.candles.data().length;
          if (total > 0) scale.setVisibleLogicalRange({ from: total - width, to: total });
        }
      }
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
      // delta, because there is no previous counter for this bar yet.
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
  }, [tickerTick, symbol, interval, extendOverlaysTo]);

  /* -- SSE price ticks into the forming candle ------------------------------------ */

  useEffect(() => {
    const handles = chartRef.current;
    if (handles === null || disposedRef.current || priceTick === null) return;
    if (priceTick.symbol !== symbol) return;

    const state = liveStateRef.current;
    if (state.lastBarTime === null) return;
    if (bucketStart(priceTick.atMs, interval) / 1000 !== state.lastBarTime) return;

    const previousClose = state.lastClose;
    const open = state.lastOpen ?? priceTick.price;
    const high = previousClose === null ? priceTick.price : Math.max(priceTick.price, previousClose);
    const low = previousClose === null ? priceTick.price : Math.min(priceTick.price, previousClose);
    handles.candles.update({ time: state.lastBarTime as Time, open, high, low, close: priceTick.price });
    liveStateRef.current = { ...state, lastClose: priceTick.price };
  }, [priceTick, symbol, interval]);

  /* ------------------------------------------------------------------------------ */

  return (
    <div className="chart-host cp-chart-host" ref={containerRef}>
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
      {loading && bars.length === 0 && <p className="chart-loading">loading {symbol} {interval}…</p>}
      {error !== null && bars.length === 0 && (
        <div className="chart-overlay chart-error">
          <strong>chart unavailable</strong>
          <span>{error}</span>
          <button type="button" className="btn" onClick={refresh}>
            retry
          </button>
        </div>
      )}
      {error === null && bars.length === 0 && !loading && data === null && (
        <p className="empty">pick a symbol anywhere in the dashboard to load its chart</p>
      )}
      {error !== null && bars.length > 0 && (
        <p className="chart-warn">
          refresh failed — showing data from {atMs === null ? '—' : new Date(atMs).toISOString()}.{' '}
          <button type="button" className="link-btn" onClick={refresh}>
            retry
          </button>
        </p>
      )}
      {refreshing && bars.length > 0 && <span className="cp-refreshing" aria-hidden="true" />}
    </div>
  );
}
