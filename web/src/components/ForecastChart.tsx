import { useMemo, useState } from 'react';
import { useElementSize } from '../hooks/useElementSize';
import { useZoomPan } from '../hooks/useZoomPan';
import { ZOOM_PLAN_FALLBACK } from '../zoom';

/** One model's forecast path, drawn against the same actual bars. */
export interface ForecastSeries {
  label: string;
  values: number[];
  /** Optional score shown in the legend. */
  mape?: number;
}

const PAD = { top: 14, right: 78, bottom: 34, left: 16 };

/** A palette that stays distinguishable on the dark panel. */
const COLORS = ['#ffb300', '#4fc3f7', '#e879f9', '#2ee06a', '#ff8a4c', '#a78bfa', '#22d3ee'];

/** Compact price label that keeps its significant digits at any scale. */
export function priceLabel(v: number): string {
  if (!Number.isFinite(v)) return '—';
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toPrecision(4);
}

/** Date and time, e.g. "18 Oct 06:00" — a date without a clock hides the intraday shape. */
function timeLabel(ms: number, withTime = true): string {
  if (!Number.isFinite(ms) || ms <= 0) return '';
  const d = new Date(ms);
  const day = `${String(d.getDate()).padStart(2, '0')} ${d.toLocaleString('en-GB', { month: 'short' })}`;
  if (!withTime) return day;
  return `${day} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * Forecast vs actual, drawn to the size of its container.
 *
 * Several models can be drawn at once: keeping the previous forecast when a new
 * model is run is the point, because paths are only comparable against the same
 * actual bars. Every series shares one y-domain, so the gaps are errors rather
 * than an artefact of independent scales.
 */
export function ForecastChart({
  times,
  actual,
  forecasts,
  sigma,
  fan,
  showWhiskers = true,
  history,
  historyTimes,
  paths,
  symbol,
  future,
}: {
  times: number[];
  actual: number[];
  forecasts: ForecastSeries[];
  sigma?: number;
  /** Bootstrap percentiles: the shape a single point forecast cannot have. */
  fan?: { p10: number[]; p25: number[]; p50: number[]; p75: number[]; p90: number[] } | null;
  /** Off hides the per-bar error whiskers. */
  showWhiskers?: boolean;
  /** Ticker, shown on the chart so a screenshot identifies itself. */
  symbol?: string;
  /** Projection past the newest candle: times and predicted values. */
  future?: { t: number[]; v: number[] } | null;
  /** Individual simulated futures, drawn faintly behind the forecast. */
  paths?: number[][] | null;
  /** Price leading up to the forecast: the "before" that makes it a forecast. */
  history?: number[];
  historyTimes?: number[];
}) {
  // The wrapper also holds the legend, so measuring it made the SVG taller than
  // the space it had and clipped the date labels off the bottom. Measure a box
  // that contains only the plot.
  const { ref, size } = useElementSize<HTMLDivElement>({ box: 'content-box' });
  const width = Math.max(240, size.width || 0);
  const height = Math.max(180, size.height || 240);

  // Hover highlights a line, click hides it: with several models drawn over each
  // other, pointing at one is the only way to see which is which.
  const [hovered, setHovered] = useState<string | null>(null);
  /** Pointer position in series index space, for the crosshair readout. */
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const toggle = (label: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });

  const zoomPan = useZoomPan(Math.max((history?.length ?? 0) + actual.length, 1), width);

  const allTimes = [...(historyTimes ?? []), ...times];

  const geom = useMemo(() => {
    const hist = history ?? [];
    if (actual.length === 0) return null;
    // One continuous x-axis: history bars, then the forecast window.
    const timesAll = [...(historyTimes ?? []), ...times];
    const actualAll = [...hist, ...actual];
    const values: number[] = [...actualAll];
    for (const f of forecasts) values.push(...f.values);
    if (fan !== null && fan !== undefined && Array.isArray(fan.p10) && Array.isArray(fan.p90)) {
      values.push(...fan.p10, ...fan.p90);
    }
    if (values.length === 0) return null;

    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (hi === lo) {
      hi = lo + Math.max(1, Math.abs(lo) * 0.001);
      lo -= Math.max(1, Math.abs(lo) * 0.001);
    }
    const pad = (hi - lo) * 0.12;
    const y0 = lo - pad;
    const y1 = hi + pad;

    const plotW = Math.max(10, width - PAD.left - PAD.right);
    const plotH = Math.max(10, height - PAD.top - PAD.bottom);
    const n = actualAll.length;
    // Zoom maps a visible window of the series into the plot: the same geometry,
    // a narrower domain. Points outside are clipped by the plot's clip path.
    const zSpan = Math.max(zoomPan.d1 - zoomPan.d0, 1e-6);
    const x = (i: number) => PAD.left + ((i - zoomPan.d0) / zSpan) * plotW;
    const y = (v: number) => PAD.top + plotH - ((v - y0) / (y1 - y0)) * plotH;

    const ticks: { y: number; label: string }[] = [];
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const v = y0 + ((y1 - y0) * i) / steps;
      ticks.push({ y: y(v), label: priceLabel(v) });
    }

    const actualLine = actualAll.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const histLine = hist.length
      ? hist.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
      : '';

    // Whiskers from the newest forecast to the actual bar, so the gap is
    // readable without a second axis.
    const newest = forecasts[forecasts.length - 1];
    const fut = future?.v ?? [];
    const nTotal = n + fut.length;
    const xTotal = (i: number) =>
      nTotal <= 1 ? PAD.left : PAD.left + (i / (nTotal - 1)) * (width - PAD.left - PAD.right);
    const off = hist.length;

    // The projection starts at the last actual point so the line joins the price.
    const futureLine =
      fut.length > 0
        ? [
            `${xTotal(n - 1).toFixed(1)},${y(actualAll[n - 1] ?? 0).toFixed(1)}`,
            ...fut.map((v, i) => `${xTotal(n + i).toFixed(1)},${y(v).toFixed(1)}`),
          ].join(' ')
        : '';
    const whiskers =
      !showWhiskers || newest === undefined
        ? []
        : actual
            .map((a, i) => {
              const f = newest.values[i];
              if (f === undefined) return null;
              return { x: x(i + off), yf: y(f), ya: y(a) };
            })
            .filter((w): w is { x: number; yf: number; ya: number } => w !== null);

    const sd = sigma !== undefined && sigma > 0 ? sigma : 0;
    let upper = '';
    let lower = '';
    if (sd > 0 && newest !== undefined) {
      upper = newest.values
        .map((f, i) => `${x(i + off).toFixed(1)},${y(f + sd * Math.sqrt(i + 1)).toFixed(1)}`)
        .join(' ');
      lower = newest.values
        .map((f, i) => `${x(i + off).toFixed(1)},${y(f - sd * Math.sqrt(i + 1)).toFixed(1)}`)
        .join(' ');
    }

    // Bands are drawn from the percentiles, so the path visibly widens with the
    // horizon instead of being a bare line.
    let band90 = '';
    let band50 = '';
    let median = '';
    if (fan !== null && fan !== undefined && Array.isArray(fan.p10) && Array.isArray(fan.p90) && fan.p10.length === actual.length) {
      const up = fan.p90.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`);
      const dn = fan.p10.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).reverse();
      band90 = [...up, ...dn].join(' ');
      const up50 = fan.p75.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`);
      const dn50 = fan.p25.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).reverse();
      band50 = [...up50, ...dn50].join(' ');
      median = fan.p50.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    }

    const offPaths = hist.length;
    const simLines = (paths ?? [])
      .filter((pth) => pth.length === actual.length)
      .map((pth) => pth.map((v, i) => `${x(i + offPaths).toFixed(1)},${y(v).toFixed(1)}`).join(' '));

    const vis = (label: string) => !hidden.has(label);
    const lines = forecasts.map((f, idx) => ({
      label: f.label,
      color: COLORS[idx % COLORS.length],
      points: f.values.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).join(' '),
      mape: f.mape,
    }));

    const span = Math.max(n - 1, 1);
    const xTicks = [0, Math.round(span * 0.25), Math.round(span * 0.5), Math.round(span * 0.75), span].filter(
      (i, idx, arr) => i >= 0 && arr.indexOf(i) === idx,
    );

    return {
      vis,
      actualLine, histLine, lines, simLines, futureLine, nTotal, xTotal, futTimes: future?.t ?? [], upper, lower, whiskers, ticks, xTicks, x, y, band90, band50, median, series: actualAll,
      nAll: actualAll.length, timesAll,
    };
  }, [times, actual, forecasts, width, height, sigma, fan, showWhiskers, history, historyTimes, paths, future, zoomPan.d0, zoomPan.d1]);

  return (
    <div className={`fchart${geom === null ? ' is-empty' : ''}`}>
      {geom === null ? null : (
        <div className="fchart-plot" ref={ref}>
        <svg
          width={width}
          height={height}
          className="fchart-svg"
          role="img"
          onWheel={zoomPan.onWheel}
          onPointerDown={zoomPan.onPointerDown}
          onPointerMove={(e) => {
            zoomPan.onPointerMove(e);
            if (e.buttons !== 0) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const plotW = Math.max(1, rect.width - PAD.left - PAD.right);
            const frac = Math.min(1, Math.max(0, (e.clientX - rect.left - PAD.left) / plotW));
            setHover(Math.round(zoomPan.d0 + frac * (zoomPan.d1 - zoomPan.d0)));
          }}
          onPointerLeave={() => setHover(null)}
          onPointerUp={zoomPan.onPointerUp}
          onDoubleClick={zoomPan.reset}
          style={{ cursor: zoomPan.zoomed ? 'grab' : 'default', touchAction: 'none' }}
        >
          <defs>
            <clipPath id="fchart-clip">
              <rect x={PAD.left} y={PAD.top} width={Math.max(0, width - PAD.left - PAD.right)} height={Math.max(0, height - PAD.top - PAD.bottom)} />
            </clipPath>
          </defs>
          <title>{`forecast vs actual — ${forecasts.map((f) => f.label).join(', ') || 'none'}`}</title>

          {symbol !== undefined && symbol !== '' && (
            <>
            <text x={PAD.left + 4} y={20} className="fchart-ticker">
              {symbol}
            </text>
            <text x={width - PAD.right} y={16} textAnchor="end" className="fchart-ticker-sm">
              {symbol}
            </text>
            </>
          )}

          {geom.ticks.map((tk) => (
            <g key={tk.label + tk.y.toFixed(0)}>
              <line x1={PAD.left} y1={tk.y} x2={width - PAD.right} y2={tk.y} className="fchart-grid" />
              <text x={width - PAD.right + 8} y={tk.y + 3} textAnchor="start" className="fchart-axis-text">
                {tk.label}
              </text>
            </g>
          ))}

          <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={height - PAD.bottom} className="fchart-axis" />
          <line x1={PAD.left} y1={height - PAD.bottom} x2={width - PAD.right} y2={height - PAD.bottom} className="fchart-axis" />

          {geom.xTicks.map((i) => (
            <line
              key={`xg${i}`}
              x1={geom.futTimes.length > 0 ? geom.xTotal(i) : geom.x(i)}
              y1={PAD.top}
              x2={geom.futTimes.length > 0 ? geom.xTotal(i) : geom.x(i)}
              y2={height - PAD.bottom}
              className="fchart-grid-v"
            />
          ))}

          {geom.xTicks.map((i) => (
            <text
              key={`t${i}`}
              x={geom.futTimes.length > 0 ? geom.xTotal(i) : geom.x(i)}
              y={height - 6}
              textAnchor={i === 0 ? 'start' : i === geom.nAll - 1 ? 'end' : 'middle'}
              className="fchart-axis-text"
            >
              {timeLabel(geom.timesAll[i] ?? 0)}
            </text>
          ))}

          {geom.whiskers.map((w, i) => (
            <line key={`w${i}`} x1={w.x} y1={w.yf} x2={w.x} y2={w.ya} className="fchart-err" />
          ))}

          {geom.futureLine !== '' && (
            <polyline points={geom.futureLine} className="fchart-future" fill="none" />
          )}
          {geom.simLines.map((pts, i) => (
            <polyline key={`sim${i}`} points={pts} className="fchart-sim" fill="none" />
          ))}
          <g clipPath="url(#fchart-clip)">
            {hover !== null && hover >= 0 && hover < geom.series.length && (
              <g>
                <line
                  x1={geom.x(hover)}
                  y1={PAD.top}
                  x2={geom.x(hover)}
                  y2={height - PAD.bottom}
                  className="fchart-cross"
                />
                <circle cx={geom.x(hover)} cy={geom.y(geom.series[hover] ?? 0)} r={3} className="fchart-cross-dot" />
              </g>
            )}
          {geom.upper !== '' && <polyline points={geom.upper} className="fchart-cone" fill="none" />}
          {geom.lower !== '' && <polyline points={geom.lower} className="fchart-cone" fill="none" />}

          {geom.band90 !== '' && <polygon points={geom.band90} className="fchart-band90" />}
          {geom.band50 !== '' && <polygon points={geom.band50} className="fchart-band50" />}
          {geom.median !== '' && <polyline points={geom.median} className="fchart-median" fill="none" />}
          {geom.histLine !== '' && <polyline points={geom.histLine} className="fchart-history" fill="none" />}
          <polyline
            points={geom.actualLine}
            className={`fchart-actual${hovered !== null ? ' is-dim' : ''}`}
            fill="none"
          />
          </g>
          {geom.lines
            .filter((l) => !hidden.has(l.label))
            .map((l) => (
              <polyline
                key={l.label}
                points={l.points}
                className={`fchart-forecast${hovered !== null && hovered !== l.label ? ' is-dim' : ''}${hovered === l.label ? ' is-hot' : ''}`}
                style={{ stroke: l.color }}
                fill="none"
              />
            ))}
        </svg>
        </div>
      )}

      {geom !== null && (
      <div className="fchart-legend">
        {history !== undefined && history.length > 0 && (
          <span className="fchart-key"><span className="fchart-swatch swatch-hist" />history</span>
        )}
        <span className="fchart-key"><span className="fchart-swatch swatch-actual" />actual</span>
        {(geom?.lines ?? []).map((l) => (
          <button
            type="button"
            className={`fchart-key is-clickable${hidden.has(l.label) ? ' is-off' : ''}${hovered === l.label ? ' is-hot' : ''}`}
            key={l.label}
            onMouseEnter={() => setHovered(l.label)}
            onMouseLeave={() => setHovered(null)}
            onFocus={() => setHovered(l.label)}
            onBlur={() => setHovered(null)}
            onClick={() => toggle(l.label)}
            title={hidden.has(l.label) ? 'show this line' : 'hide this line'}
          >
            <span className="fchart-swatch" style={{ background: l.color }} />
            {l.label}
            {l.mape !== undefined && <span className="fchart-mape">{l.mape.toFixed(3)}%</span>}
          </button>
        ))}
        {paths !== undefined && paths !== null && paths.length > 0 && (
          <span className="fchart-key"><span className="fchart-swatch swatch-sim" />plausible paths ({paths.length})</span>
        )}
        {future !== undefined && future !== null && future.v.length > 0 && (
          <span className="fchart-key"><span className="fchart-swatch swatch-future" />forecast (next {future.v.length})</span>
        )}
        {zoomPan.zoomed && (
          <button type="button" className="fchart-key is-clickable" onClick={zoomPan.reset} title="fit the whole series">
            ⤢ fit
          </button>
        )}
        <span className="fchart-zoom">
          <span>zoom</span>
          {ZOOM_PLAN_FALLBACK.presets
            .filter((p) => p.mode === 'fit' || (p.bars ?? 0) > 0)
            .map((p) => (
              <button
                key={p.preset}
                type="button"
                className="fchart-zoom-btn"
                onClick={() => {
                  if (p.mode === 'fit' || p.bars === undefined) zoomPan.reset();
                  else zoomPan.show(p.bars);
                }}
                title={p.mode === 'fit' ? 'fit the whole series' : `show the newest ${p.bars} bars`}
              >
                {p.preset}
              </button>
            ))}
        </span>
        {hover !== null && hover >= 0 && hover < geom.series.length ? (
          <span className="fchart-readout num">
            {priceLabel(geom.series[hover] ?? 0)}
            <span className="fchart-readout-time">
              {new Date(allTimes[hover] ?? 0).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
            </span>
          </span>
        ) : (
          <span className="fchart-note">scroll to zoom · drag to pan · double-click to fit</span>
        )}
        <span className="fchart-key"><span className="fchart-swatch swatch-err" />error</span>
        {fan !== null && fan !== undefined && (
          <span className="fchart-key"><span className="fchart-swatch swatch-fan" />predictive range 10-90%</span>
        )}
        {sigma !== undefined && sigma > 0 && (
          <span className="fchart-key"><span className="fchart-swatch swatch-cone" />±1σ band</span>
        )}
      </div>
      )}
    </div>
  );
}
