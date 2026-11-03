import { useState } from 'react';
import { postAction } from '../data/client';
import { ForecastChart } from './ForecastChart';
import { LiveForecastChart } from './LiveForecastChart';
import { ChartPanel } from './ChartPanel';
import { useResource } from '../hooks/useResource';
import { useNow } from '../hooks/useNow';
import { TickerPicker } from './TickerPicker';

interface Row {
  method: string; lookback: number; r2: number; mape_pct: number;
  bars: number; horizon: number; skill_vs_flat: number;
}
interface TracePoint { lookback: number; r2: number; mape: number }
interface LiveState {
  symbol: string;
  trace?: TracePoint[];
  now_testing?: number;
  phase?: string;
  phase_index?: number;
  phase_total?: number;
  best: Row | null;
  rows: Row[];
  series: {
    method: string; lookback: number; r2: number; mape_pct: number;
    points?: { t: number; forecast: number; actual: number }[];
  }[];
  tests: number;
  cycles: number;
  updated_ms: number;
  seconds: number;
  history: number[];
}
interface Payload {
  ok: boolean;
  training?: boolean;
  gpu?: string;
  interval: string;
  symbols: string[];
  parallel_workers: number;
  states: LiveState[];
}

/**
 * A viewer onto the server-side optimiser.
 *
 * The GPU workers run whether or not this page is open, so the panel does no
 * computation of its own — it polls a small state document and draws it. Nothing
 * here can stall the search, and closing the tab stops nothing.
 */
export function LiveEnginePanel() {
  // Selection is a symbol, not an index: an index silently snaps back to the
  // first tab whenever the panel re-renders, which is why the header kept
  // saying BTCUSDT after picking something else.
  const [activeSymbol, setActiveSymbol] = useState<string>(() => {
    try {
      return window.localStorage.getItem('cryptalas.live.symbol') ?? '';
    } catch {
      return '';
    }
  });
  const selectSymbol = (sym: string) => {
    setActiveSymbol(sym);
    try {
      window.localStorage.setItem('cryptalas.live.symbol', sym);
    } catch {
      /* a disabled localStorage must not break selection */
    }
  };
  const [full, setFull] = useState(false);
  const [draft, setDraft] = useState('');
  const [saved, setSaved] = useState<string | null>(null);
  const [stopArmed, setStopArmed] = useState(false);

  // Keep the configuration the optimiser settled on, so forecasting can reuse it
  // instead of deciding again.
  const saveConfig = async (st: LiveState) => {
    if (!st.best) return;
    const q = new URLSearchParams({
      symbol: st.symbol, interval: data?.interval ?? '4h',
      method: st.best.method, lookback: String(st.best.lookback),
      bars: String(st.best.bars), horizon: String(st.best.horizon),
      r2: String(st.best.r2), mape: String(st.best.mape_pct),
    });
    await postAction(`/api/forecast/configs?${q.toString()}`);
    setSaved(st.symbol);
  };

  // One resource for the engine state; the age label comes from the shared clock
  // rather than a second timer counting seconds by hand.
  const engine = useResource<Payload>('/api/forecast/live-state', { pollMs: 2000 });
  const nowMs = useNow();
  const data = engine.data;
  const age = engine.updatedMs === 0 ? 0 : Math.max(0, Math.round((nowMs - engine.updatedMs) / 1000));

  const states = data?.states ?? [];
  const current = states.find((st) => st.symbol === activeSymbol) ?? states[0];
  const series = current?.series?.[0];

  return (
    <div className={`le${full ? ' is-full' : ''}${states.length === 0 ? ' is-warming' : ''}`}>
      <div className="le-bar">
        <span className="le-live"><i />LIVE ENGINE</span>
        <span className="le-meta">{data?.parallel_workers ?? 0} parallel GPU workers · {data?.interval ?? '4h'} candles</span>
        <span className="le-meta">state {age}s old</span>
        {data?.gpu !== undefined && data.gpu !== '' && (
          <span className="le-gpu" title="what the GPU agent is computing right now">
            <i />{data.gpu}
          </span>
        )}
        <span className="le-add">
          {/* The same picker the rest of CryptAlyst uses: searchable, with the
              instrument's full name and type, rather than a bare text box. */}
          <TickerPicker
            value={draft}
            ariaLabel="add a symbol to the live engine"
            onPick={(sym) => {
              setDraft(sym);
              if (sym.trim() === '') return;
              void postAction(`/api/forecast/live-symbols?symbols=${encodeURIComponent(sym.trim())}`).then(() => {
                setDraft('');
                setSaved(null);
              });
            }}
          />
        </span>
        <button
          type="button"
          className={`le-train${data?.training === false ? ' is-off' : ''}`}
          onClick={() => {
            // Stopping leaves four GPU workers idle and needs a second click to
            // confirm; resuming does not, because resuming is safe.
            if (data?.training !== false && !stopArmed) {
              setStopArmed(true);
              window.setTimeout(() => setStopArmed(false), 4000);
              return;
            }
            setStopArmed(false);
            const next = data?.training === false ? 'on' : 'off';
            void postAction(`/api/forecast/engine-control?training=${next}`).then(() => engine.reload());
          }}
        >
          {data?.training === false ? 'resume training' : stopArmed ? 'stop? click again' : 'stop training'}
        </button>
        <button
          type="button"
          className="le-save"
          disabled={current?.best === null || current?.best === undefined}
          onClick={() => current && void saveConfig(current)}
          title="save this symbol's winning configuration and use it for forecasting"
        >
          {saved === current?.symbol ? 'config saved ✓' : 'save config'}
        </button>
        <button type="button" className="le-full" onClick={() => setFull((v) => !v)}>
          {full ? 'exit full screen' : 'full screen'}
        </button>
      </div>

      {states.length === 0 ? (
        <p className="fl-hint">the engine is warming up — the first sweep takes a few seconds</p>
      ) : (
        <>
          <div className="le-tabs">
            {states.map((s) => (
              <button
                key={s.symbol}
                type="button"
                className={`le-tab${s.symbol === current?.symbol ? ' is-on' : ''}`}
                onClick={() => selectSymbol(s.symbol)}
              >
                <b>{s.symbol}</b>
                <span>R² {(s.best?.r2 ?? 0).toFixed(3)}</span>
                <span>{(s.best?.mape_pct ?? 0).toFixed(3)}%</span>
              </button>
            ))}
          </div>

          {current && (
            <p className="le-ticker">
              <b>{current.symbol}</b>
              {current.best && (
                <>
                  <span>{current.best.method} · lookback {current.best.lookback} · {current.best.bars} bars · horizon {current.best.horizon}</span>
                  <span>R² {(current.best.r2 ?? 0).toFixed(4)}</span>
                  <span>MAPE {(current.best.mape_pct ?? 0).toFixed(3)}%</span>
                </>
              )}
            </p>
          )}

          {current !== undefined && (
            <div className="le-trace">
              <span className="le-trace-label">
                optimising <b>{current.phase ?? '—'}</b> ({current.phase_index ?? 0}/{current.phase_total ?? 0})
                {(current.trace ?? []).length === 0 && <i className="le-trace-wait">starting…</i>}
              </span>
              {(current.trace ?? []).map((t, i) => (
                <span
                  className={`le-step${i === (current.trace?.length ?? 0) - 1 ? ' is-now' : ''}`}
                  key={`${t.lookback}-${i}`}
                  title={`lookback ${t.lookback}: R² ${t.r2.toFixed(4)}, MAPE ${t.mape.toFixed(3)}%`}
                >
                  <i>lb{t.lookback}</i>
                  <b>{t.r2.toFixed(3)}</b>
                  <span className="le-step-bar" style={{ height: `${Math.max(4, Math.min(28, (t.r2 - 0.9) * 400))}px` }} />
                </span>
              ))}
            </div>
          )}

          {current && (
            <div className="le-facts">
              <span><i>best</i><b>{current.best?.method ?? '—'}</b></span>
              <span><i>lookback</i>{current.best?.lookback ?? '—'}</span>
              <span><i>window</i>{current.best?.bars ?? '—'} bars</span>
              <span><i>horizon</i>{current.best?.horizon ?? '—'} bars</span>
              <span><i>R²</i><b>{(current.best?.r2 ?? 0).toFixed(4)}</b></span>
              <span><i>MAPE</i><b>{(current.best?.mape_pct ?? 0).toFixed(3)}%</b></span>
              <span><i>tests</i>{current.tests}</span>
              <span><i>cycles</i>{current.cycles}</span>
              <span><i>sweep</i>{(current.seconds ?? 0).toFixed(2)}s</span>
            </div>
          )}

          {series && Array.isArray(series.points) && series.points.length > 0 && (
            <ChartPanel
              title="model forecast"
              meta={`${current?.symbol ?? ''} · ${series.method} lb${series.lookback} · MAPE ${series.mape_pct.toFixed(3)}%`}
            >
            <ForecastChart
              symbol={current?.symbol}
              times={(series.points ?? []).map((p) => p.t)}
              actual={(series.points ?? []).map((p) => p.actual)}
              forecasts={[{ label: `${series.method} lb${series.lookback}`, values: (series.points ?? []).map((p) => p.forecast), mape: series.mape_pct }]}
              showWhiskers={false}
            />
            </ChartPanel>
          )}

          {current !== undefined && (
            <ChartPanel title="next bars" meta={<span>{current.symbol} · live</span>}>
              <LiveForecastChart symbol={current.symbol} interval={data?.interval ?? '4h'} />
            </ChartPanel>
          )}

          {current && current.rows.length > 0 && (
            <div className="le-board">
              {current.rows.slice(0, 12).map((r, i) => (
                <div className={`le-row${i === 0 ? ' is-best' : ''}`} key={`${r.method}-${r.lookback}-${r.bars}-${r.horizon}`}>
                  <span className="le-rank">{i + 1}</span>
                  <span className="le-name">{r.method} · lb{r.lookback}</span>
                  <span className="le-params">w{r.bars} h{r.horizon}</span>
                  <span className="le-r2">{r.r2 >= 0 ? '+' : ''}{r.r2.toFixed(4)}</span>
                  <span className="le-mape">{r.mape_pct.toFixed(3)}%</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
