import { useMemo, useState } from 'react';
import { useResource } from '../hooks/useResource';
import { useNow } from '../hooks/useNow';
import { ForecastChart } from './ForecastChart';

interface Payload {
  ok: boolean;
  symbol: string;
  interval: string;
  method: string;
  lookback: number;
  horizon: number;
  times: number[];
  actual: number[];
  forecast: number[];
  forecast_times: number[];
  last_close: number;
  fan?: { p10: number[]; p25: number[]; p50: number[]; p75: number[]; p90: number[] };
  branches?: number[][];
}

/**
 * The next bars, live: a data wrapper around the one SVG renderer.
 *
 * This used to be a second renderer with its own axes, cone, branches, zoom, pan,
 * crosshair and clip — all of which ForecastChart already had. It keeps only what is
 * specific to being live: the frame switch, the ticker and the LIVE age.
 */
export function LiveForecastChart({ symbol, interval }: { symbol: string; interval: string }) {
  const [tf, setTf] = useState<string>('5s');
  const nowMs = useNow();
  const active = tf === '' ? interval : tf;

  const chart = useResource<Payload>(
    `/api/forecast/live-chart?${new URLSearchParams({ symbol, interval: active, horizon: '6', bars: '60' }).toString()}`,
    { pollMs: 30000 },
  );
  const ticker = useResource<{ rows?: { price?: number; ts_ms?: number }[] }>(
    `/api/ticker?symbol=${encodeURIComponent(symbol)}`,
    { pollMs: 1500 },
  );

  const data = chart.data !== null && chart.data.ok ? chart.data : null;
  const live = ticker.data?.rows?.[0];

  const view = useMemo(() => {
    if (data === null) return null;
    const prices = data.actual.slice();
    if (live?.price !== undefined && prices.length > 0) prices[prices.length - 1] = live.price;
    return { prices, anchor: live?.price ?? data.last_close };
  }, [data, live?.price]);

  if (data === null || view === null) {
    return (
      <div className="lfc">
        <div className="lfc-bar">
          <span className="lfc-title">NEXT BARS</span>
          <span className="lfc-meta">loading…</span>
        </div>
        <p className="fl-hint">loading the next bars…</p>
      </div>
    );
  }

  const lastForecast = data.forecast[data.forecast.length - 1] ?? view.anchor;
  const changePct = view.anchor > 0 ? ((lastForecast / view.anchor) - 1) * 100 : 0;
  const ageS = live?.ts_ms !== undefined ? Math.max(0, Math.round((nowMs - live.ts_ms) / 1000)) : null;

  return (
    <div className="lfc">
      <div className="lfc-bar">
        <span className="lfc-title">NEXT BARS</span>
        <span className="lfc-sym">{data.symbol}</span>
        <span className="lfc-meta">{data.method} · lb{data.lookback} · {data.interval}</span>
        <span className="lfc-tf">
          {[{ k: '5s', label: '5s' }, { k: '30s', label: '30s' }, { k: '1m', label: '1m' }, { k: '', label: 'engine' }].map((o) => (
            <button
              key={o.label}
              type="button"
              className={`lfc-tf-btn${(o.k === '' && tf === '') || o.k === tf ? ' is-on' : ''}`}
              onClick={() => setTf(o.k)}
            >
              {o.label}
            </button>
          ))}
        </span>
        <span className="lfc-meta">last {view.anchor.toPrecision(6)}</span>
        {ageS !== null && (
          <span className="lfc-live">
            <i />LIVE {ageS}s
          </span>
        )}
        <span className={`lfc-delta${changePct >= 0 ? ' is-up' : ' is-down'}`}>
          {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}% over {data.horizon} bars
        </span>
      </div>

      <ForecastChart
        symbol={data.symbol}
        times={data.times}
        actual={view.prices}
        forecasts={[]}
        showWhiskers={false}
        projection={{
          values: data.forecast,
          fan: data.fan ?? null,
          branches: data.branches ?? null,
        }}
      />
    </div>
  );
}
