import { useEffect, useState } from 'react';
import type { UTCTimestamp } from 'lightweight-charts';
import type { AlignedSeries, LinePoint } from '../chartData';
import type { Interval } from '../types';

/** One graded step: what the model said, and what actually happened. */
interface VerifyPoint {
  t: number;
  forecast: number;
  actual: number;
  error: number;
  error_pct: number;
}

export interface VerifyFan {
  p10: number[];
  p25: number[];
  p50: number[];
  p75: number[];
  p90: number[];
  simulations?: number;
  basis?: string;
}

export interface VerifyResult {
  ok: boolean;
  error?: string;
  symbol: string;
  interval: string;
  method: string;
  source: string;
  device: string;
  compute: string;
  agent: string;
  job: string;
  horizon: number;
  last_train: number;
  points: VerifyPoint[];
  /** A few individual simulated futures: what "possible" looks like. */
  paths?: number[][] | null;
  /** Bars leading up to the forecast, so the chart can show a past and a future. */
  history?: number[];
  history_times?: number[];
  /** The last bar the model saw: where the forecast line has to start. */
  anchor_t?: number;
  anchor_price?: number;
  /** Error-minimising scale fitted on resolved windows, applied to this path. */
  calibration?: { lambda: number; windows: number; basis?: string } | null;
  /** Bootstrap predictive range, when there was enough history to resample. */
  fan?: VerifyFan | null;
  errors: {
    mae: number;
    rmse: number;
    mape_pct: number;
    bias: number;
    max_error: number;
    direction: number;
  };
}

const HORIZON = 12;

/**
 * Runs a forecast on an enrolled agent over a holdout split and returns it as a
 * chart overlay, so the forecast is drawn on the same candles it is being
 * graded against rather than on a separate picture.
 *
 * The request is deliberately made through the platform (`/api/forecast/verify`)
 * and not the agent directly: the platform owns the history, so it can hold out
 * the last N bars and grade the reply against them.
 */
export function useForecastOverlay(
  symbol: string,
  interval: Interval,
  enabled: boolean,
  horizon: number = HORIZON,
  trainBars: number = 0,
  method: string = 'best',
): { series: AlignedSeries | null; result: VerifyResult | null; error: string | null } {
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setResult(null);
      setError(null);
      return;
    }
    let dead = false;
    const run = async () => {
      try {
        const res = await fetch('/api/forecast/verify', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          // "best" lets the platform draw whichever model currently wins the
          // leaderboard, so the chart cannot silently show a worse forecast.
          body: JSON.stringify({ symbol, interval, horizon, train_bars: trainBars, method }),
        });
        const body = (await res.json()) as VerifyResult;
        if (dead) return;
        if (body.ok === false) {
          setResult(null);
          setError(body.error ?? 'forecast failed');
          return;
        }
        setResult(body);
        setError(null);
      } catch {
        if (!dead) setError('forecast unavailable');
      }
    };
    void run();
    return () => {
      dead = true;
    };
  }, [symbol, interval, enabled, horizon, trainBars, method]);

  const series: AlignedSeries | null = result
    ? {
        key: 'forecast',
        data: result.points.map<LinePoint>((p) => ({
          time: Math.floor(p.t / 1000) as UTCTimestamp,
          value: p.forecast,
        })),
      }
    : null;

  return { series, result, error };
}
