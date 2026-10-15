import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchJson } from '../api';
import { toNum } from '../format';
import type { ChartIndicatorMap, Interval, KlinesMsg, SafeBarLike, SymbolDetailMsg } from '../types';

/* --------------------------------------------------------------- constants */

/**
 * How often `/api/klines/latest` is polled.
 *
 * **The ticker push is the primary live path** (SSE `ticker`, ~4 Hz). This poll
 * is demoted to *reconciliation*: its job is to correct any drift, finalise bar
 * boundaries, and deliver `marks` and `indicators`, which the ticker does not
 * carry. Because it no longer drives the visible price, it can be relaxed.
 */
export const LIVE_POLL_MS = 10_000;

/** The one-shot seed for the ticker channel. */
export const TICKER_SEED_URL = '/api/ticker';

/** API.md: `limit` is clamped by the backend to 2..1000; the spec asks for 300. */
export const KLINES_LIMIT = 300;

/**
 * The exchange caps a 1-second kline response at 1000 bars (16.6 minutes), so a
 * finer preset asks for that many and the coarse ones step up to `1m` — exactly
 * what `/api/zoom-presets` publishes.
 */
export const KLINES_LIMIT_BY_INTERVAL: Readonly<Record<string, number>> = {
  '1s': 1000,
};

/** Milliseconds per bar, by interval — used to bucket incoming live prices. */
export const INTERVAL_MS: Readonly<Record<string, number>> = {
  '1s': 1_000,
  '1m': 60_000,
  '5m': 300_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '1d': 86_400_000,
};

/** Open time of the bar `tMs` falls inside, for the given interval. */
export function bucketStart(tMs: number, interval: string): number {
  const size = INTERVAL_MS[interval] ?? 60_000;
  return Math.floor(tMs / size) * size;
}

/** One incrementally-applied update from the light endpoint. */
export interface LiveBar {
  /** Monotonic id, so an identical value still produces one effect run. */
  seq: number;
  /** Bar open time in epoch ms. */
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  q: number;
  n: number;
  tb: number;
  /** Scalar indicator values for this bar; a key may be `null` (warming up). */
  indicators: Record<string, number | null>;
  /** The `latest` bag, for the footer. */
  latest: Record<string, unknown>;
  /**
   * Cumulative 24h quote volume at this bar, used to re-seed the volume
   * accumulator. The ticker's rolling counter can drift (or roll over), so the
   * poll re-anchors it.
   */
  quoteVol24h: number | null;
  /**
   * Marks from this poll's payload. Present on the light endpoint, so a new
   * event marker appears without waiting for a rollover refetch.
   */
  marks: unknown[] | null;
  /** When the poll landed (local ms). */
  atMs: number;
}

/** Everything the chart needs. */
export interface KlinesState {
  /** The last full payload; only replaced by a full fetch. */
  data: KlinesMsg | null;
  /**
   * Increments each time a fresh payload replaces the series wholesale.
   *
   * The chart calls `setData()` and re-fits the viewport exactly then — never on
   * the incremental poll, which only moves the last candle.
   */
  loadSeq: number;
  /** A full history load is in flight (and nothing is drawn yet). */
  loading: boolean;
  /** A full history refresh is in flight on top of already-drawn data. */
  refreshing: boolean;
  error: string | null;
  /** Local ms of the last successful full load. */
  atMs: number | null;
  /** The newest incrementally-applied bar, or `null` before the first poll. */
  live: LiveBar | null;
  /** Local ms of the last successful poll, for the "live" indicator. */
  liveAtMs: number | null;
  /** True while the incremental pipeline is running. */
  polling: boolean;
  /** Force a full refetch (history rollover, retry, tab re-shown). */
  refresh: () => void;
}

function num(v: unknown): number | null {
  return toNum(v);
}

function toBar(raw: unknown): Omit<LiveBar, 'seq' | 'indicators' | 'latest' | 'marks' | 'quoteVol24h' | 'atMs'> | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const b = raw as Record<string, unknown>;
  const t = num(b.t);
  const o = num(b.o);
  const h = num(b.h);
  const l = num(b.l);
  const c = num(b.c);
  if (t === null || o === null || h === null || l === null || c === null) return null;
  return {
    t: Math.trunc(t),
    o,
    h: Math.max(h, o, c, l),
    l: Math.min(l, o, c, h),
    c,
    v: num(b.v) ?? 0,
    q: num(b.q) ?? 0,
    n: num(b.n) ?? 0,
    tb: num(b.tb) ?? 0.5,
  };
}

/** Read the scalar indicator map, dropping anything non-finite. */
function toIndicators(raw: unknown): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  if (typeof raw !== 'object' || raw === null) return out;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    out[key] = num(value);
  }
  return out;
}

/**
 * Poll `/api/klines` for history, then `/api/klines/latest` for increments.
 *
 * - **full fetch** on symbol/interval change (and on an explicit `refresh()`),
 *   with an `AbortController` so a late response for the previous symbol can
 *   never paint over the new one
 * - **1s incremental polls** afterwards, which the chart applies with
 *   `series.update(...)` — never `setData`, which would rebuild the series and
 *   throw away the user's pan/zoom
 * - **rollover**: a poll whose bar time is newer than the drawn bars triggers one
 *   history refresh, so the just-finished candle is finalised with correct
 *   indicator history and `marks` are refreshed, then polling resumes
 * - **visibility**: polls stop while the tab is hidden and refetch immediately on
 *   becoming visible again, so a returned-to tab never shows a stale candle
 * - **active**: polls stop entirely when the chart is not mounted
 */
export function useKlines(
  symbol: string | null,
  interval: Interval,
  options: { active?: boolean; pollMs?: number } = {},
): KlinesState {
  const active = options.active ?? true;
  const pollMs = options.pollMs ?? LIVE_POLL_MS;

  const [data, setData] = useState<KlinesMsg | null>(null);
  /**
   * Increments each time a **fresh payload** replaces the series wholesale.
   *
   * The chart must call `setData()` and re-fit the viewport exactly then — and
   * *never* on the incremental poll, which only moves the last candle. Without
   * this counter the chart cannot tell the two apart, and re-fitting on a poll
   * yanks the user back to the present every few seconds.
   */
  const [loadSeq, setLoadSeq] = useState(0);
  const [loading, setLoading] = useState(symbol !== null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [atMs, setAtMs] = useState<number | null>(null);
  const [live, setLive] = useState<LiveBar | null>(null);
  const [liveAtMs, setLiveAtMs] = useState<number | null>(null);
  const [polling, setPolling] = useState(false);
  const [nonce, setNonce] = useState(0);

  /** Newest bar open time currently drawn, so rollover can be detected. */
  const newestBarT = useRef<number | null>(null);
  /** Set when a rollover is detected; the loop refetches history for it. */
  const needsHistory = useRef(false);
  const seq = useRef(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  /* -- full history --------------------------------------------------------- */

  useEffect(() => {
    if (symbol === null) {
      setData(null);
      setLoadSeq((n) => n + 1);
      setLoading(false);
      setRefreshing(false);
      setError(null);
      setAtMs(null);
      setLive(null);
      setLiveAtMs(null);
      newestBarT.current = null;
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    const limit = KLINES_LIMIT_BY_INTERVAL[interval] ?? KLINES_LIMIT;
    const url = `/api/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}&_r=${nonce}`;

    void (async () => {
      const body = await fetchJson<KlinesMsg>(url, controller.signal);
      if (cancelled) return;
      if (body === null || !Array.isArray(body.bars)) {
        // Keep whatever is already drawn; a failed refresh must not blank a chart.
        setLoading(false);
        setRefreshing(false);
        setError(`could not load klines for ${symbol} ${interval}`);
        return;
      }
      setData(body);
      setLoadSeq((n) => n + 1);
      setError(null);
      setLoading(false);
      setRefreshing(false);
      setAtMs(Date.now());
      const bars = body.bars;
      newestBarT.current =
        bars.length > 0 && typeof bars[bars.length - 1]?.t === 'number' ? bars[bars.length - 1].t : null;
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [symbol, interval, nonce]);

  /* -- incremental polling -------------------------------------------------- */

  useEffect(() => {
    if (symbol === null || !active) {
      setPolling(false);
      return;
    }
    let cancelled = false;
    let timer: number | null = null;
    let controller = new AbortController();
    setPolling(true);

    const stop = (): void => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      setPolling(false);
    };

    const schedule = (): void => {
      if (cancelled) return;
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
        stop();
        return;
      }
      timer = window.setTimeout(() => {
        void tick();
      }, pollMs);
    };

    const tick = async (): Promise<void> => {
      if (cancelled) return;

      // A rollover was spotted: take one full history pass, so the finished
      // candle's indicators and the refreshed marks are authoritative. The
      // nonce change re-runs the history effect; polling continues meanwhile.
      if (needsHistory.current) {
        needsHistory.current = false;
        setRefreshing(true);
        refresh();
      }

      const url = `/api/klines/latest?symbol=${encodeURIComponent(symbol)}&interval=${interval}`;
      const body = await fetchJson<{ bar?: unknown; indicators?: unknown; latest?: unknown }>(
        url,
        controller.signal,
      );
      if (cancelled) return;

      if (body !== null) {
        const bar = toBar(body.bar);
        const polledMarks = (body as { marks?: unknown }).marks;
        if (bar !== null) {
          const previous = newestBarT.current;
          if (previous !== null && bar.t > previous) {
            // A new candle started (or several were skipped on a thin symbol).
            needsHistory.current = true;
          }
          newestBarT.current = bar.t;
          seq.current += 1;
          setLive({
            ...bar,
            seq: seq.current,
            indicators: toIndicators(body.indicators),
            latest: typeof body.latest === 'object' && body.latest !== null ? (body.latest as Record<string, unknown>) : {},
            marks: Array.isArray(polledMarks) ? polledMarks : null,
            quoteVol24h: num((body as { quote_vol_24h?: unknown }).quote_vol_24h),
            atMs: Date.now(),
          });
          setLiveAtMs(Date.now());
          setError(null);
        }
      }
      schedule();
    };

    const onVisibility = (): void => {
      if (cancelled) return;
      if (document.visibilityState === 'visible') {
        // Refetch immediately so a returned-to tab is never stale.
        if (timer !== null) {
          window.clearTimeout(timer);
          timer = null;
        }
        void tick();
      } else {
        stop();
      }
    };

    // Start immediately: the first poll lands right after the history.
    void tick();
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      cancelled = true;
      controller.abort();
      controller = new AbortController();
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
    // Deliberately not keyed on `nonce`: a rollover refresh must not tear down
    // and restart the poll loop. The controller/`cancelled` guard per run is what
    // stops a stale response for the previous symbol from landing here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, interval, active, pollMs]);

  return { data, loadSeq, loading, refreshing, error, atMs, live, liveAtMs, polling, refresh };
}

/**
 * Poll `/api/symbol` for the drill-down of one symbol.
 *
 * Same cadence and lifecycle as the chart's poll: fetched once on open, then
 * refreshed, with polling suspended while the tab is hidden. The payload is
 * additive — any section may be missing — so a partial response still renders.
 */
export function useSymbolDetail(symbol: string | null, pollMs: number = LIVE_POLL_MS * 5): {
  detail: SymbolDetailMsg | null;
  loading: boolean;
  error: string | null;
  atMs: number | null;
  refresh: () => void;
} {
  const [detail, setDetail] = useState<SymbolDetailMsg | null>(null);
  const [loading, setLoading] = useState(symbol !== null);
  const [error, setError] = useState<string | null>(null);
  const [atMs, setAtMs] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (symbol === null) {
      setDetail(null);
      setLoading(false);
      setError(null);
      setAtMs(null);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);

    const load = async (): Promise<void> => {
      const body = await fetchJson<SymbolDetailMsg>(
        `/api/symbol?symbol=${encodeURIComponent(symbol)}&_r=${nonce}`,
        controller.signal,
      );
      if (cancelled) return;
      if (body === null) {
        setLoading(false);
        setError(`could not load the drill-down for ${symbol}`);
        return;
      }
      setDetail(body);
      setError(null);
      setLoading(false);
      setAtMs(Date.now());
    };

    void load();
    const timer = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      void load();
    }, pollMs);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [symbol, pollMs, nonce]);

  return { detail, loading, error, atMs, refresh };
}

/** Guarded read of a live indicator value. */
export function liveIndicator(bar: LiveBar | null, key: string): number | null {
  if (bar === null) return null;
  const value = bar.indicators[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** True when the value is one we may push into a series (never `null`). */
export function plottable(value: number | null): value is number {
  return value !== null && Number.isFinite(value);
}

/** Alias kept for callers that only need the type. */
export type { ChartIndicatorMap, SafeBarLike };
