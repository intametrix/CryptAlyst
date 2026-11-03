import { useState } from 'react';
import { ForecastChart } from './ForecastChart';
import type { Interval } from '../types';

interface BacktestPoint { t: number; forecast: number; actual: number }
interface Metrics {
  mape_pct: number; mae: number; rmse: number; r2: number;
  flat_mape_pct: number; flat_mae: number; skill_vs_flat: number;
}
interface BacktestResult {
  ok: boolean; error?: string; method: string; points: BacktestPoint[];
  points_n: number; span_days: number; metrics: Metrics;
}

/**
 * A multi-day backtest: the model forecasts forward at every step, and what
 * actually happened is drawn beneath it. One forecast at the end of a series
 * proves nothing; ten days of them is a claim someone can check.
 */
export function BacktestPanel({ symbol, interval, method }: { symbol: string; interval: Interval; method: string }) {
  const [res, setRes] = useState<BacktestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // A backtest is a period: explicit dates beat a bar count.
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [horizon, setHorizon] = useState(24);
  const [windowBars, setWindowBars] = useState(240);

  const run = async (days: number) => {
    setBusy(true); setErr(null);
    try {
      // The window comes from the selector; explicit dates take precedence.
      const q = new URLSearchParams({ symbol, interval, horizon: String(horizon), bars: String(windowBars), method });
      void days;
      if (from !== '') q.set('from_ms', String(new Date(from).getTime()));
      if (to !== '') q.set('to_ms', String(new Date(to).getTime()));
      const r = await fetch(`/api/forecast/backtest?${q.toString()}`, { headers: { accept: 'application/json' } });
      const body = (await r.json()) as BacktestResult;
      if (!body.ok) { setErr(body.error ?? 'backtest failed'); setRes(null); }
      else setRes(body);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const m = res?.metrics;

  return (
    <div className="bt">
      <div className="bt-bar">
        <span className="bt-label">backtest</span>
        <button type="button" className="btn bt-run" disabled={busy} onClick={() => void run(7)}>
          {busy ? 'running…' : '7 days'}
        </button>
        <button type="button" className="btn bt-run" disabled={busy} onClick={() => void run(14)}>
          14 days
        </button>
        <span className="bt-note">rolls {method} across the window, forecast vs what happened</span>
      </div>

      <div className="bt-params">
        <label>from<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>to<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <label>
          horizon
          <select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
            {[6, 12, 24, 48].map((h) => <option key={h} value={h}>{h} bars</option>)}
          </select>
        </label>
        <label>
          window
          <select value={windowBars} onChange={(e) => setWindowBars(Number(e.target.value))}>
            {[120, 240, 400, 600, 900].map((b) => <option key={b} value={b}>{b} bars</option>)}
          </select>
        </label>
        <span className="bt-note">{from || to ? 'dates override the day buttons' : 'or use the day buttons'}</span>
      </div>

      {err !== null && <p className="fl-hint">backtest unavailable — {err}</p>}

      {res && m && (
        <>
          <div className="bt-metrics">
            <span><i>span</i>{res.span_days.toFixed(1)} days</span>
            <span><i>points</i>{res.points_n}</span>
            <span><i>MAPE</i><b>{m.mape_pct.toFixed(3)}%</b></span>
            <span><i>R²</i><b>{m.r2.toFixed(3)}</b></span>
            <span><i>vs flat</i><b>{m.skill_vs_flat >= 0 ? '+' : ''}{(m.skill_vs_flat * 100).toFixed(1)}%</b></span>
            <span><i>MAE</i>{m.mae.toFixed(2)}</span>
          </div>
          <ForecastChart
            times={res.points.map((p) => p.t)}
            actual={res.points.map((p) => p.actual)}
            forecasts={[{ label: `${res.method} (backtest)`, values: res.points.map((p) => p.forecast), mape: m.mape_pct }]}
            showWhiskers={false}
          />
        </>
      )}
    </div>
  );
}
