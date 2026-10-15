import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toNum } from '../format';
import type { TickerMsg, TickerRow } from '../types';

/**
 * The charted symbol's newest tick.
 *
 * `price` and the cumulative `quote_vol_24h` counter both come straight off the
 * wire; the volume *delta* is the consumer's job (see `ChartPanel`), because
 * only the chart knows which bar is forming.
 */
export interface TickerTick {
  symbol: string;
  price: number;
  /** Rolling 24h quote volume — a counter, not a delta. */
  quoteVol24h: number | null;
  bid: number | null;
  ask: number | null;
  /** Exchange timestamp of this tick (epoch ms). */
  atMs: number;
  /** Local receive time, used for the "live" pill. */
  receivedMs: number;
}

/** Frames slower than this are ignored as out-of-order. */
const STALE_MS = 2_000;

/**
 * A tiny store for the newest tick, fed by the SSE `ticker` channel.
 *
 * Frames arrive ~4x a second for ~25 symbols, so this must not re-render the
 * dashboard per frame. It keeps the charted symbol's row in a ref (readable at
 * any time) and mirrors it into state at a *bounded* rate — just often enough for
 * the "live" pill to tick — while the chart itself reads the ref directly.
 *
 * Out-of-order frames (an older `ts_ms` than the last applied) are dropped, so a
 * late frame can never walk the price backwards.
 */
export function useTicker(symbol: string | null): {
  /** The newest tick for `symbol`, or `null`. */
  tick: TickerTick | null;
  /** Feed one raw `ticker` frame (called from the SSE handler). */
  ingest: (payload: unknown) => void;
  /** Local ms of the last accepted tick, for the live pill. */
  lastAtMs: number | null;
  /** How many ticks have been applied — used to prove the rate in tests. */
  count: number;
} {
  const latest = useRef<TickerTick | null>(null);
  /** Monotonic frame counter, so the pill can re-render at a bounded rate. */
  const [snapshot, setSnapshot] = useState(0);
  const [count, setCount] = useState(0);
  const lastFlush = useRef(0);
  /** Bumped whenever the subject changes, to force a fresh read. */
  const subject = useRef<string | null>(symbol);
  subject.current = symbol;

  const ingest = useCallback((payload: unknown) => {
    if (typeof payload !== 'object' || payload === null) return;
    const rows = (payload as TickerMsg).rows;
    if (!Array.isArray(rows)) return;
    const wanted = subject.current;
    if (wanted === null) return;

    for (const raw of rows) {
      if (typeof raw !== 'object' || raw === null) continue;
      const row = raw as TickerRow;
      if (typeof row.symbol !== 'string' || row.symbol !== wanted) continue;
      const price = toNum(row.price);
      if (price === null || price <= 0) continue;
      const atMs = toNum(row.ts_ms) ?? Date.now();
      const previous = latest.current;
      // Drop late frames: a stale tick must not move the candle backwards.
      if (previous !== null && previous.symbol === wanted && atMs < previous.atMs - STALE_MS) continue;

      latest.current = {
        symbol: wanted,
        price,
        quoteVol24h: toNum(row.quote_vol_24h),
        bid: toNum(row.bid),
        ask: toNum(row.ask),
        atMs,
        receivedMs: Date.now(),
      };
      setCount((n) => n + 1);

      // Re-render at most ~4x a second even if the feed is faster; the chart
      // reads the ref, so nothing is lost by batching the pill's clock.
      const now = Date.now();
      if (now - lastFlush.current >= 250) {
        lastFlush.current = now;
        setSnapshot((n) => n + 1);
      }
      return;
    }
  }, []);

  // A new subject starts from a clean slate: the old symbol's counter must never
  // be used as the baseline for the new symbol's bar.
  useEffect(() => {
    latest.current = null;
    lastFlush.current = 0;
    setSnapshot((n) => n + 1);
  }, [symbol]);

  const tick = useMemo(() => {
    void snapshot;
    const current = latest.current;
    return current !== null && current.symbol === symbol ? current : null;
  }, [snapshot, symbol]);

  return { tick, ingest, lastAtMs: tick?.receivedMs ?? null, count };
}
