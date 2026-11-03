import { useEffect, useState } from 'react';
import { ForecastChart } from './ForecastChart';

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
  const [data, setData] = useState<Payload | null>(null);
  const [age, setAge] = useState(0);
  const [active, setActive] = useState(0);
  const [full, setFull] = useState(false);
  const [draft, setDraft] = useState('');
  const [saved, setSaved] = useState<string | null>(null);

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
    await fetch(`/api/forecast/configs?${q.toString()}`, { method: 'POST' });
    setSaved(st.symbol);
  };

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch('/api/forecast/live-state', { headers: { accept: 'application/json' } });
        const body = (await r.json()) as Payload;
        if (alive) {
          setData(body);
          setAge(0);
        }
      } catch {
        /* keep the last state on screen */
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 2000);
    const tick = window.setInterval(() => setAge((a) => a + 1), 1000);
    return () => {
      alive = false;
      window.clearInterval(id);
      window.clearInterval(tick);
    };
  }, []);

  const states = data?.states ?? [];
  const current = states[active] ?? states[0];
  const series = current?.series?.[0];

  return (
    <div className={`le${full ? ' is-full' : ''}`}>
      <div className="le-bar">
        <span className="le-live"><i />LIVE ENGINE</span>
        <span className="le-meta">{data?.parallel_workers ?? 0} parallel GPU workers · {data?.interval ?? '4h'} candles</span>
        <span className="le-meta">state {age}s old</span>
        <span className="le-add">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value.toUpperCase())}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && draft.trim() !== '') {
                void fetch(`/api/forecast/live-symbols?symbols=${encodeURIComponent(draft.trim())}`)
                  .then(() => setDraft(''));
              }
            }}
            placeholder="add symbol"
            aria-label="add a symbol to the live engine"
          />
          <button
            type="button"
            onClick={() => {
              if (draft.trim() === '') return;
              void fetch(`/api/forecast/live-symbols?symbols=${encodeURIComponent(draft.trim())}`)
                .then(() => setDraft(''));
            }}
          >
            add
          </button>
        </span>
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
            {states.map((s, i) => (
              <button
                key={s.symbol}
                type="button"
                className={`le-tab${i === active ? ' is-on' : ''}`}
                onClick={() => setActive(i)}
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
                {current.trace !== undefined && current.trace.length === 0 && <i className="le-trace-wait">starting…</i>}
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
            <ForecastChart
              symbol={current?.symbol}
              times={(series.points ?? []).map((p) => p.t)}
              actual={(series.points ?? []).map((p) => p.actual)}
              forecasts={[{ label: `${series.method} lb${series.lookback}`, values: (series.points ?? []).map((p) => p.forecast), mape: series.mape_pct }]}
              showWhiskers={false}
            />
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
