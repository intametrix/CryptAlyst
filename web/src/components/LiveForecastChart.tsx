import { useEffect, useMemo, useState } from 'react';
import { useElementSize } from '../hooks/useElementSize';
import { useZoomPan } from '../hooks/useZoomPan';

interface Payload {
  ok: boolean;
  error?: string;
  symbol: string;
  interval: string;
  method: string;
  lookback: number;
  horizon: number;
  last_close: number;
  times: number[];
  actual: number[];
  forecast_times: number[];
  forecast: number[];
  fan?: { p10: number[]; p50: number[]; p90: number[] };
  /** Individual sampled futures: the visible fan-out from the current price. */
  branches?: number[][];
}

const PAD = { top: 18, right: 14, bottom: 26, left: 64 };

/**
 * Price now, and the next few bars with a funnel around them.
 *
 * The optimiser panel answers "which model"; this answers "so what next". The
 * funnel is a bootstrap of the symbol's own one-bar returns, so its width is a
 * measured spread rather than a drawn guess, and it widens with every step it
 * claims to predict.
 */
export function LiveForecastChart({ symbol, interval }: { symbol: string; interval: string }) {
  // The engine optimises on 4h candles; the live chart is about the next few
  // seconds, so it has its own time-frame switch.
  const [tf, setTf] = useState<string>('5s');
  const active = tf === '' ? interval : tf;
  const [data, setData] = useState<Payload | null>(null);
  const [live, setLive] = useState<{ price: number; at: number } | null>(null);
  const [tick, setTick] = useState(0);
  const { ref, size } = useElementSize<HTMLDivElement>({ box: 'content-box' });
  const width = Math.max(320, size.width || 0);
  const height = Math.max(180, size.height || 220);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const q = new URLSearchParams({ symbol, interval: active, horizon: '6', bars: '60' });
        const r = await fetch(`/api/forecast/live-chart?${q.toString()}`, { headers: { accept: 'application/json' } });
        const body = (await r.json()) as Payload;
        if (alive && body.ok) setData(body);
      } catch {
        /* keep the last frame */
      }
    };
    // The candles change rarely; the traded price changes constantly. Polling the
    // ticker is what makes the right edge of the chart move in real time.
    const loadPrice = async () => {
      try {
        const r = await fetch(`/api/ticker?symbol=${encodeURIComponent(symbol)}`, { headers: { accept: 'application/json' } });
        const body = (await r.json()) as { rows?: { price?: number; ts_ms?: number }[] };
        const row = body.rows?.[0];
        if (alive && row?.price !== undefined) setLive({ price: row.price, at: row.ts_ms ?? Date.now() });
      } catch {
        /* the candle chart still works without the tick */
      }
    };
    void load();
    void loadPrice();
    const poll = window.setInterval(() => void load(), 30000);
    const pricePoll = window.setInterval(() => void loadPrice(), 1500);
    const clock = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => {
      alive = false;
      window.clearInterval(poll);
      window.clearInterval(pricePoll);
      window.clearInterval(clock);
    };
  }, [symbol, active]);

  // The same navigation the forecast chart has: wheel zooms at the cursor, drag
  // pans, double-click resets. Without it a live chart is a picture rather than a
  // chart.
  const total = (data?.actual.length ?? 0) + (data?.forecast.length ?? 0);
  const zoomPan = useZoomPan(Math.max(total, 1), width);

  const geom = useMemo(() => {
    if (data === null) return null;
    const base = data.actual;
    const n = base.length;
    const h = data.forecast.length;
    if (n < 5 || h === 0) return null;

    // The forecast is NOT rescaled by the live price. Doing that moved every
    // point of the projection on every tick, which read as the chart jumping
    // while the shape was unchanged. The projection stays where the model put
    // it; only the price tip moves, and the join line between them bends.
    const lastClose = data.last_close || base[n - 1] || 1;
    const livePrice = live?.price ?? lastClose;
    const ratio = 1;
    const forecast = data.forecast.map((v) => v * ratio);
    const actual = [...base, livePrice];

    const totalBars = actual.length + h;
    const total = totalBars;
    const values = [...actual, ...forecast];
    if (data.fan) values.push(...data.fan.p10, ...data.fan.p90);
    const visFrom = Math.max(0, Math.floor(zoomPan.d0));
    const visTo = Math.min(values.length - 1, Math.ceil(zoomPan.d1));
    const visible = zoomPan.zoomed && visTo > visFrom ? values.slice(visFrom, visTo + 1) : values;
    const lo = Math.min(...visible);
    const hi = Math.max(...visible);
    const span = Math.max(hi - lo, 1e-9);
    const zSpan = Math.max(zoomPan.d1 - zoomPan.d0, 1e-6);
    const x = (i: number) => PAD.left + ((i - zoomPan.d0) / zSpan) * (width - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + (1 - (v - lo) / span) * (height - PAD.top - PAD.bottom);

    const last = actual[actual.length - 1] ?? 0;
    const price = actual.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const anchorIdx = actual.length - 1;
    const future = [
      `${x(anchorIdx).toFixed(1)},${y(last).toFixed(1)}`,
      ...forecast.map((v, i) => `${x(anchorIdx + 1 + i).toFixed(1)},${y(v).toFixed(1)}`),
    ].join(' ');

    // Each sampled future becomes a line from the live price, so the projection
    // branches rather than being one line with a band around it.
    const branches = (data.branches ?? [])
      .filter((b) => b.length === h)
      .map((b) =>
        [
          `${x(anchorIdx).toFixed(1)},${y(last).toFixed(1)}`,
          ...b.map((v, i) => `${x(anchorIdx + 1 + i).toFixed(1)},${y(v * ratio).toFixed(1)}`),
        ].join(' '),
      );

    let cone = '';
    let inner = '';
    if (data.fan) {
      const up = data.fan.p90.map((v, i) => `${x(anchorIdx + 1 + i).toFixed(1)},${y(v * ratio).toFixed(1)}`);
      const dn = data.fan.p10.map((v, i) => `${x(anchorIdx + 1 + i).toFixed(1)},${y(v * ratio).toFixed(1)}`).reverse();
      cone = [...up, ...dn].join(' ');
      const mid = data.fan.p50.map((v, i) => `${x(anchorIdx + 1 + i).toFixed(1)},${y(v * ratio).toFixed(1)}`);
      inner = mid.join(' ');
    }

    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const v = hi - span * f;
      return { y: y(v), label: v >= 1000 ? v.toFixed(0) : v.toPrecision(4) };
    });
    const label = (ms: number): string => {
      const d = new Date(ms);
      return `${String(d.getDate()).padStart(2, '0')} ${d.toLocaleString('en-GB', { month: 'short' })} ${String(d.getHours()).padStart(2, '0')}:00`;
    };
    const xTicks = [0, Math.floor((total - 1) / 2), total - 1].map((i) => ({
      x: x(i),
      label:
        i < n
          ? label(data.times[i] ?? data.times[0] ?? 0)
          : i === n
            ? 'now'
            : label(data.forecast_times[i - n - 1] ?? 0),
    }));

    return { price, future, cone, inner, branches, ticks, xTicks, x, y, n, h, lo, hi };
  }, [data, width, height, tick, live, zoomPan.d0, zoomPan.d1]);

  const lastForecast = data?.forecast[data.forecast.length - 1];
  const changePct =
    data !== null && lastForecast !== undefined && data.last_close > 0
      ? (lastForecast / data.last_close - 1) * 100
      : 0;

  return (
    <div className="lfc">
      <div className="lfc-bar">
        <span className="lfc-title">NEXT BARS</span>
        {data !== null && (
          <>
            <span className="lfc-sym">{data.symbol}</span>
            <span className="lfc-meta">{data.method} · lb{data.lookback} · {data.interval}</span>
            <span className="lfc-tf">
              {[
                { k: '5s', label: '5s' },
                { k: '30s', label: '30s' },
                { k: '1m', label: '1m' },
                { k: '', label: 'engine' },
              ].map((o) => (
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
            <span className="lfc-meta">last {(live?.price ?? data.last_close).toPrecision(6)}</span>
            {live !== null && (
              <span className="lfc-live">
                <i />LIVE {Math.max(0, Math.round((Date.now() - live.at) / 1000))}s
              </span>
            )}
            <span className={`lfc-delta${changePct >= 0 ? ' is-up' : ' is-down'}`}>
              {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}% over {data.horizon} bars
            </span>
          </>
        )}
      </div>

      <div className="lfc-plot" ref={ref}>
        {geom === null ? (
          <p className="fl-hint">loading the next bars…</p>
        ) : (
          <svg
            width={width}
            height={height}
            className="lfc-svg"
            role="img"
            onWheel={zoomPan.onWheel}
            onPointerDown={zoomPan.onPointerDown}
            onPointerMove={zoomPan.onPointerMove}
            onPointerUp={zoomPan.onPointerUp}
            onDoubleClick={zoomPan.reset}
            style={{ cursor: zoomPan.zoomed ? 'grab' : 'default', touchAction: 'none' }}
          >
            {geom.ticks.map((t) => (
              <g key={t.label + t.y.toFixed(0)}>
                <line x1={PAD.left} y1={t.y} x2={width - PAD.right} y2={t.y} className="fchart-grid" />
                <text x={PAD.left - 8} y={t.y + 3} textAnchor="end" className="fchart-axis-text">{t.label}</text>
              </g>
            ))}
            {geom.branches.map((pts, i) => (
              <polyline key={`b${i}`} points={pts} className="lfc-branch" fill="none" />
            ))}
            {geom.cone !== '' && <polygon points={geom.cone} className="lfc-cone" />}
            <polyline points={geom.price} className="fchart-actual" fill="none" />
            {geom.inner !== '' && <polyline points={geom.inner} className="lfc-median" fill="none" />}
            <polyline points={geom.future} className="lfc-future" fill="none" />
            {geom.xTicks.map((t, i) => (
              <text key={`x${i}`} x={t.x} y={height - 8} textAnchor={i === 0 ? 'start' : i === geom.xTicks.length - 1 ? 'end' : 'middle'} className="fchart-axis-text">
                {t.label}
              </text>
            ))}
          </svg>
        )}
      </div>

      <div className="fchart-legend">
        <span className="fchart-key"><span className="fchart-swatch swatch-actual" />price</span>
        <span className="fchart-key"><span className="fchart-swatch swatch-future" />forecast</span>
        <span className="fchart-key"><span className="fchart-swatch swatch-fan" />funnel 10–90%</span>
        {data?.branches !== undefined && data.branches.length > 0 && (
          <span className="fchart-key"><span className="fchart-swatch swatch-branch" />{data.branches.length} branches</span>
        )}
        {data?.fan !== undefined && <span className="fchart-key"><span className="fchart-swatch swatch-median" />fan median</span>}
        <span className="fchart-zoom">
          <span>zoom</span>
          {[30, 60, 120].map((n) => (
            <button key={n} type="button" className="fchart-zoom-btn" onClick={() => zoomPan.show(n)} title={`show the newest ${n} points`}>
              {n}
            </button>
          ))}
          <button type="button" className="fchart-zoom-btn" onClick={zoomPan.reset} title="fit everything">
            all
          </button>
        </span>
        <span className="fchart-note">scroll to zoom · drag to pan · double-click to fit</span>
      </div>
    </div>
  );
}
