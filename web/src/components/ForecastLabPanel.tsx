import { memo, useEffect, useState } from 'react';
import { num, text } from '../format';
import { useSelection } from '../selection';
import { useForecastOverlay } from '../hooks/useForecastOverlay';
import { TickerPicker } from './TickerPicker';
import { ForecastChart, priceLabel } from './ForecastChart';
import { BacktestPanel } from './BacktestPanel';
import { LiveEnginePanel } from './LiveEnginePanel';
import { DataPanel } from './DataPanel';
import { Section } from './Section';
import { useEffect as useFx, useRef as useRf } from 'react';
import type { Interval } from '../types';
import { INTERVAL_MS, HORIZON_CHOICES, TRAIN_CHOICES, barsFor, historyNeeded, horizonLabel, trainBars, trainLabel } from '../forecastHorizon';

/* --------------------------------------------------------------- wire types */

interface LabScore {
  model: string;
  note: string;
  windows: number;
  mae: number;
  rmse: number;
  mape: number;
  bias: number;
  direction: number;
  max_error: number;
  band80: number;
  coverage80: number;
  band95: number;
  coverage95: number;
}

interface LabWindow {
  train_end: number;
  train_from: number;
  train_to: number;
  times: number[];
  last_train: number;
  real: number[];
  forecasts: Record<string, number[]>;
}

interface OptimizeParams {
  weights: number[];
  sma: number;
  alpha: number;
  damp: number;
}

interface OptimizeCandidate {
  weights: number[];
  sma: number;
  alpha: number;
  damp: number;
  mae: number;
  rmse: number;
}

interface OptimizeHistory {
  iteration: number;
  mae: number;
  step?: number;
  params: OptimizeParams;
}

interface OptimizeResult {
  ok: boolean;
  iterations?: number;
  evaluations?: number;
  history?: OptimizeHistory[];
  error?: string;
  agent?: string;
  device?: string;
  compute?: string;
  candidates?: number;
  windows?: number;
  horizon?: number;
  best?: { params: OptimizeParams; mae: number; rmse: number; mape_pct: number };
  top?: OptimizeCandidate[];
}

interface Coverage {
  symbol: string;
  interval: string;
  bars: number;
  from_ms: number;
  to_ms: number;
  span_hours: number;
  span_days: number;
  max_fetch: number;
  sufficient: boolean;
}

interface LabImprovement {
  ms: number;
  iteration: number;
  model: string;
  mape: number;
  prev_mape: number;
  prev_model: string;
}

interface LabTracker {
  iterations: number;
  best: string;
  best_mape: number;
  models: number;
  fetches: number;
  reused: number;
  last_ms: number;
  history: LabImprovement[];
}

/**
 * The lab can answer with a partial payload (an error, or a window too short to
 * rank). Every collection is therefore nullable: the type says so, and `tsc`
 * refuses the unguarded access that used to crash the panel at runtime.
 */
interface LabStudy {
  symbol: string;
  interval: string;
  horizon: number;
  points?: number | null;
  best?: string | null;
  windows?: LabWindow[] | null;
  scores?: LabScore[] | null;
  tracker?: LabTracker | null;
}

/**
 * The parameters a model exposes, and the values the lab can actually run.
 * These are the same grids the Go lab scores, so changing one here swaps to a
 * variant that exists end to end rather than inventing a name nothing serves.
 */
const MODEL_PARAMS: { kind: string; label: string; values: string[]; unit: string }[] = [
  { kind: 'sma', label: 'lookback', values: ['3', '5', '8', '10', '15', '20', '30', '50', '80', '100', '150'], unit: 'bars' },
  { kind: 'ema', label: 'alpha', values: ['5', '10', '15', '20', '30', '40', '50', '60'], unit: '/100' },
  { kind: 'trendN', label: 'lookback', values: ['20', '30', '50', '75', '100', '150'], unit: 'bars' },
  { kind: 'trend', label: 'lookback', values: ['all'], unit: 'bars' },
  { kind: 'damped', label: 'damp', values: ['70', '75', '80', '85', '90', '95', '98'], unit: '% / step' },
  { kind: 'analog', label: 'neighbours', values: ['5', '10', '20'], unit: 'windows' },
  { kind: 'ens', label: 'components', values: ['3'], unit: 'blended' },
  { kind: 'flat', label: 'none', values: [], unit: '' },
];

/** Which parameter family a model name belongs to, and its current value. */
function modelParam(name: string): { kind: string; value: string } | null {
  if (name === 'best') return null;
  if (name === 'naive' || name === 'mean') return { kind: 'flat', value: '' };
  if (name.startsWith('sma')) return { kind: 'sma', value: name.slice(3) };
  if (name.startsWith('ema')) return { kind: 'ema', value: name.slice(3) };
  if (name === 'trend') return { kind: 'trend', value: 'all' };
  if (name.startsWith('trend')) return { kind: 'trendN', value: name.slice(5) };
  if (name.startsWith('damped')) return { kind: 'damped', value: name.slice(6) };
  if (name.startsWith('analog')) return { kind: 'analog', value: name.slice(6) };
  if (name.startsWith('ens_')) return { kind: 'ens', value: '3' };
  return null;
}

/** The facts behind a model, derived from its name so every card can show them. */
function modelDetails(name: string): { family: string; lookback: string; param: string; shape: string } {
  if (name === 'best') return { family: 'auto', lookback: '—', param: 'picked by ranking', shape: 'varies' };
  if (name === 'arima') return { family: 'ARIMA', lookback: 'p,1,q', param: 'order by BIC', shape: 'dynamic' };
  if (name === 'mlr') return { family: 'regression', lookback: '20 bars', param: 'price+volume+time', shape: 'path' };
  if (name === 'super') return { family: 'composite', lookback: '16 models', param: 'inverse-error weights', shape: 'path' };
  if (name === 'vwap') return { family: 'volume-weighted', lookback: '24 bars', param: 'volume weights', shape: 'path' };
  if (name === 'vwtrend') return { family: 'volume-weighted', lookback: '50 bars', param: 'volume weights', shape: 'slope' };
  if (name === 'vwdrift') return { family: 'volume-weighted', lookback: '24 bars', param: 'volume z-score', shape: 'path' };
  if (name.startsWith('timestral')) return { family: 'foundation model', lookback: 'up to 32k', param: 'pretrained', shape: 'quantiles' };
  if (name === 'naive') return { family: 'random walk', lookback: '1', param: 'none', shape: 'flat' };
  if (name === 'mean') return { family: 'average', lookback: 'all', param: 'none', shape: 'flat' };
  if (name.startsWith('sma')) return { family: 'moving average', lookback: `${name.slice(3)} bars`, param: 'none', shape: 'flat' };
  if (name.startsWith('ema')) {
    const a = Number(name.slice(3)) / 100;
    return { family: 'exponential', lookback: 'all', param: `alpha ${a.toFixed(2)}`, shape: 'flat' };
  }
  if (name === 'trend') return { family: 'regression', lookback: 'all', param: 'slope', shape: 'straight' };
  if (name.startsWith('trend')) return { family: 'regression', lookback: `${name.slice(5)} bars`, param: 'slope', shape: 'straight' };
  if (name.startsWith('damped')) {
    const d = Number(name.slice(6)) / 100;
    return { family: 'damped trend', lookback: 'all', param: `damp ${d.toFixed(2)}`, shape: 'decaying' };
  }
  if (name.startsWith('ens_')) return { family: 'ensemble', lookback: 'mixed', param: '3 blended', shape: 'mixed' };
  if (name.startsWith('analog')) return { family: 'retrieval', lookback: '12 bars', param: `${name.slice(6)} neighbours`, shape: 'path' };
  return { family: name, lookback: '—', param: '—', shape: '—' };
}

/** True when a model's output is a level, i.e. a flat line by construction. */
function isLevelModel(name: string | undefined | null): boolean {
  if (!name) return false;
  return (
    name === 'naive' ||
    name === 'mean' ||
    name.startsWith('sma') ||
    name.startsWith('ema')
  );
}

/** How many bars of this interval fit in a day. */
function barsPerDay(interval: Interval): number {
  const ms = INTERVAL_MS[interval] ?? 3_600_000;
  return Math.round(86_400_000 / ms);
}

/** Date and time, because a date alone hides whether the last bar is fresh. */
function stamp(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Age of the newest bar, in the unit a reader thinks in. */
function age(ms: number): string {
  const mins = Math.max(0, Math.round((Date.now() - ms) / 60_000));
  if (mins < 60) return `${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} d`;
}

/** What a model actually produces, in one line. */
function describeModel(name: string): string {
  if (name === 'best') return 'whichever model wins the ranking';
  if (name === 'arima') return 'ARIMA fitted on the series, order chosen by BIC';
  if (name === 'mlr') return 'price, volume and time regression fitted walk-forward';
  if (name === 'super') return 'blends every family by measured skill, falling back to the best';
  if (name === 'vwap') return 'converges to the volume-weighted average price';
  if (name === 'vwtrend') return 'trend fitted with every bar weighted by its volume';
  if (name === 'vwdrift') return 'recent move scaled by how unusual the volume is';
  if (name.startsWith('timestral')) return 'Mistral Timestral foundation model, zero-shot on the GPU';
  if (name === 'naive') return 'last close, held flat';
  if (name === 'mean') return 'mean of the training window, flat';
  if (name.startsWith('sma')) return `${name.slice(3)}-bar mean, held flat`;
  if (name.startsWith('ema')) return `exponential mean (alpha ${(Number(name.slice(3)) / 100).toFixed(2)}), flat`;
  if (name === 'trend') return 'least-squares line, extrapolated';
  if (name.startsWith('trend')) return `line fitted to the last ${name.slice(5)} bars`;
  if (name.startsWith('damped')) return `line damped ${name.slice(6)}% per step`;
  if (name.startsWith('ens_')) return 'blend of mean, exponential and damped line';
  if (name.startsWith('analog')) return `mean outcome of the ${name.slice(6)} most similar past windows`;
  return name;
}

const POLL_MS = 5000;

/** Every model the lab can rank, plus "best" to let it choose. */
const MODELS = [
  'best', 'naive', 'mean', 'arima', 'mlr', 'timestral',
  'super', 'vwap', 'vwtrend', 'vwdrift',
  'sma3', 'sma5', 'sma8', 'sma10', 'sma15', 'sma20', 'sma30', 'sma50', 'sma80', 'sma100', 'sma150',
  'ema5', 'ema10', 'ema15', 'ema20', 'ema30', 'ema40', 'ema50', 'ema60',
  'trend', 'trend20', 'trend30', 'trend50', 'trend75', 'trend100', 'trend150',
  'damped70', 'damped75', 'damped80', 'damped85', 'damped90', 'damped95', 'damped98',
  'ens_a', 'ens_b', 'ens_c',
  'analog5', 'analog10', 'analog20',
];

/**
 * Forecast lab — every model graded against the bars that actually followed.
 *
 * The table is the leaderboard that `best` is taken from. The forecast line
 * itself is drawn by the candle chart (see useForecastOverlay), so there is one
 * charting implementation in the app rather than two.
 */
export const ForecastLabPanel = memo(function ForecastLabPanel() {
  const { symbol, interval, selectInterval } = useSelection();
  /**
   * The chosen symbol is LOCAL. It must not touch the shared selection: that is
   * what opens the chart popup, and choosing a symbol here must not do that.
   */
  const [ticker, setTicker] = useState(symbol ?? 'BTCUSDT');
  const [study, setStudy] = useState<LabStudy | null>(null);
  /** How much history exists for the chosen symbol, and whether it is enough. */
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [downloading, setDownloading] = useState(false);
  /** Result of the agent-side GPU hyperparameter search. */
  const [optimized, setOptimized] = useState<OptimizeResult | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [live, setLive] = useState(false);
  const [hours, setHours] = useState(24);
  /** Which model draws the line; 'best' lets the lab choose. */
  // Volume-weighted: measured to beat the flat line on 18 of 20 symbols, which
  // no price-only model managed.
  // ARIMA by request: order chosen by BIC on the window, forecast by the fitted
  // recursion. vwtrend remains the most accurate model measured (0.362% vs
  // 0.939%), so it is one click away in the grid.
  const [method, setMethod] = useState('arima');
  /** Walk-forward cuts used when ranking. */
  const [windows, setWindows] = useState(8);
  /** Optimiser rounds. */
  const [iterations, setIterations] = useState(50);
  const horizon = barsFor(hours, interval);
  /** Training window in hours; 0 = everything available. */
  const [trainHours, setTrainHours] = useState(0);
  const lookback = trainBars(trainHours, interval);
  const sym = ticker;
  // The chart's symbol IS the selection, so this control writes to the same
  // state rather than keeping a second copy. `draft` only holds what is being
  // typed; committing on Enter or blur avoids charting a half-typed ticker.
  /**
   * Choosing a symbol records it and nothing more. No forecast, no chart, no
   * navigation — the panel then reports how much history exists for it.
   */
  const commit = (value: string) => {
    const next = value.trim().toUpperCase();
    if (next === '') return;
    setTicker(next);
  };
  /**
   * The frozen request. Null until the user presses forecast: choosing a symbol
   * must never start a forecast on its own.
   */
  const [request, setRequest] = useState<{
    symbol: string;
    interval: Interval;
    horizon: number;
    lookback: number;
    method: string;
    token: number;
  } | null>(null);

  /** The graded forecast vs the bars that actually followed. */
  const graded = useForecastOverlay(
    request?.symbol ?? '',
    request?.interval ?? interval,
    request !== null,
    request?.horizon,
    request?.lookback,
    request?.method,
  );

  useEffect(() => {
    // The ranking is a read: it grades every model on the history already held,
    // so each card can show its score before anything is trained or sent.
    let dead = false;
    const tick = async () => {
      try {
        const q = new URLSearchParams({
          symbol: sym,
          interval,
          horizon: String(horizon),
          windows: String(windows),
          limit: '400',
          train: String(lookback),
        });
        const res = await fetch(`/api/forecast/lab?${q.toString()}`, { headers: { accept: 'application/json' } });
        if (!res.ok) {
          if (!dead) setLive(false);
          return;
        }
        const body = (await res.json()) as LabStudy;
        if (!dead) {
          setStudy(body);
          setLive(true);
        }
      } catch {
        if (!dead) setLive(false);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      dead = true;
      window.clearInterval(id);
    };
  }, [sym, interval, horizon, lookback, windows]);

  // How much history exists for this symbol, refreshed after a download.
  useEffect(() => {
    let dead = false;
    const run = async () => {
      try {
        const q = new URLSearchParams({ symbol: sym, interval, limit: '400' });
        const res = await fetch(`/api/history/coverage?${q.toString()}`);
        if (!res.ok) return;
        const body = (await res.json()) as Coverage;
        if (!dead) setCoverage(body);
      } catch {
        /* coverage is advisory: the panel still works without it */
      }
    };
    void run();
    return () => {
      dead = true;
    };
  }, [sym, interval, downloading]);

  /** A graded payload may be partial; never dereference it directly. */
  const gradedPoints = graded.result?.points ?? [];
  /** Every forecast run this session, oldest first: a new model adds a line. */
  const [runs, setRuns] = useState<{ method: string; points: typeof gradedPoints; mape: number }[]>([]);
  useEffect(() => {
    const r = graded.result;
    if (!r || gradedPoints.length === 0) return;
    setRuns((prev) => {
      if (prev.some((x) => x.method === r.method)) return prev;
      return [...prev, { method: r.method, points: gradedPoints, mape: r.errors?.mape_pct ?? 0 }];
    });
  }, [graded.result, gradedPoints]);
  /** A lab payload may be partial; never index into it directly. */
  const scores = study?.scores ?? [];
  const windowCount = study?.windows?.length ?? 0;
  const errs = graded.result?.errors;
  const optWeights = optimized?.best?.params?.weights ?? [];
  const chartRef = useRf<HTMLDivElement | null>(null);
  const modelsRef = useRf<HTMLDivElement | null>(null);
  /** The side panel shows the selected model's adjustable parameters. */
  const [panelOpen, setPanelOpen] = useState(false);
  /** Chart layers, each independently switchable. */
  const [showFan, setShowFan] = useState(true);
  const [showSigma, setShowSigma] = useState(true);
  const [showError, setShowError] = useState(true);
  /** The model list always opens at the top: it is a list, not a log. */
  useFx(() => {
    if (modelsRef.current) modelsRef.current.scrollTop = 0;
  }, []);
  /** A result arriving brings its own chart into view — no hunting for it. */
  useFx(() => {
    if (!graded.result || gradedPoints.length === 0) return;
    // scrollIntoView was landing short (the section above can be very tall once
    // it has been resized), so scroll the window to an explicit offset and
    // retry once after layout settles.
    const reveal = () => {
      const el = chartRef.current;
      if (!el) return;
      // The page scrolls inside a container, not the window, so walk up to the
      // real scrollable ancestor and set its scrollTop.
      let box: HTMLElement | null = el.parentElement;
      while (box !== null) {
        const st = window.getComputedStyle(box);
        if (/(auto|scroll)/.test(st.overflowY) && box.scrollHeight > box.clientHeight + 4) break;
        box = box.parentElement;
      }
      if (box !== null) {
        const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 120;
        box.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
        return;
      }
      const y = el.getBoundingClientRect().top + window.scrollY - 120;
      window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
    };
    const a = window.setTimeout(reveal, 250);
    const b = window.setTimeout(reveal, 900);
    return () => {
      window.clearTimeout(a);
      window.clearTimeout(b);
    };
  }, [graded.result, gradedPoints.length]);

  /** History is the only thing a symbol choice reports. */
  const historyLine = coverage
    ? `${coverage.bars.toLocaleString()} bars · ${coverage.span_days.toFixed(1)} days in the database`
    : 'reading history…';

  // The server can legitimately return null for these; treating them as empty
  // is what keeps a missing improvement list from blanking the panel.
  const tracker = study?.tracker ?? null;
  const trackerHistory = tracker?.history ?? [];

  const needed = historyNeeded(horizon) + lookback;
  const shortOnHistory = coverage !== null && coverage.bars < needed;

  async function runOptimizer() {
    setOptimizing(true);
    setOptimized(null);
    try {
      const res = await fetch('/api/forecast/optimize', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ symbol: sym, interval, horizon, train_bars: lookback, windows, iterations }),
      });
      const body = (await res.json()) as OptimizeResult;
      setOptimized(body);
    } catch (e) {
      setOptimized({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setOptimizing(false);
    }
  }

  async function downloadMore() {
    setDownloading(true);
    try {
      const res = await fetch('/api/history/download', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ symbol: sym, interval, bars: Math.min(1000, Math.max(needed * 2, 600)) }),
      });
      if (res.ok) setCoverage((await res.json()) as Coverage);
    } catch {
      /* the unchanged coverage line reports the failure */
    } finally {
      setDownloading(false);
    }
  }



  return (
    <div className="fl-workspace">
      <header className="fl-workspace-head">
        <h2>forecast lab</h2>
        <span className="fl-workspace-sub">
          {sym} · {interval} · {horizonLabel(hours, interval)}
          {study?.best ? ` · best ${study.best}` : ''}
        </span>
      </header>


      <Section
        id="fl.parameters"
        title="Parameters"
        meta={<span>{trainLabel(trainHours, interval)} → {horizonLabel(hours, interval)}</span>}
      >
        <div className="fl-controls">
          <span className="fl-ctl fl-ctl-ticker">
            ticker
            <TickerPicker value={sym} onPick={commit} />
          </span>
          <button
            type="button"
            className="btn fl-run-btn"
            onClick={() =>
              setRequest({ symbol: sym, interval, horizon, lookback, method, token: Date.now() })
            }
          >
            ▶ train + forecast
          </button>
          <button
            type="button"
            className="btn fl-opt-btn"
            disabled={optimizing}
            onClick={() => void runOptimizer()}
          >
            {optimizing ? 'optimising…' : '⚙ optimise on GPU'}
          </button>
          <label className="fl-ctl">
            interval
            <select value={interval} onChange={(e) => selectInterval(e.target.value as Interval)}>
              {(['1m', '5m', '15m', '1h', '4h', '1d'] as Interval[]).map((iv) => (
                <option key={iv} value={iv}>{iv}</option>
              ))}
            </select>
          </label>
          <label className="fl-ctl">
            forecast ahead
            <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {HORIZON_CHOICES.map((c) => (
                <option key={c.hours} value={c.hours}>{c.label}</option>
              ))}
            </select>
          </label>
          <label className="fl-ctl">
            train over
            <select value={trainHours} onChange={(e) => setTrainHours(Number(e.target.value))}>
              {TRAIN_CHOICES.map((c) => (
                <option key={c.hours} value={c.hours}>{c.label}</option>
              ))}
            </select>
          </label>
          <label className="fl-ctl">
            windows
            <input
              type="number"
              min={2}
              max={30}
              value={windows}
              onChange={(e) => setWindows(Math.max(2, Math.min(30, Number(e.target.value) || 2)))}
            />
          </label>
          <label className="fl-ctl">
            iterations
            <input
              type="number"
              min={1}
              max={500}
              step={10}
              value={iterations}
              onChange={(e) => setIterations(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
            />
          </label>
          {graded.result && (
            <button
              type="button"
              className="fl-jump"
              onClick={() => chartRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })}
            >
              ✓ forecast ready — show chart
            </button>
          )}
          <span className={`fl-live${live ? ' is-live' : ''}`}>
            {request === null ? 'idle' : live ? 'live · 5s' : 'offline'}
          </span>
        </div>
      </Section>

      <Section id="fl.live" title="Live engine" meta={<span>GPU workers · always running</span>}>
        <LiveEnginePanel />
      </Section>

      <Section id="fl.data" title="Data" meta={<span>what is stored locally</span>}>
        <DataPanel symbol={sym} />
      </Section>

      <Section id="fl.history" title="History" meta={<span>{coverage ? `${coverage.bars} bars` : '—'}</span>}>
        {coverage ? (
          <>
            <div className="fl-stats">
              <div className="fl-stat">
                <span className="fl-stat-label">frequency</span>
                <span className="fl-stat-value num">{coverage.interval}</span>
                <span className="fl-stat-sub">{barsPerDay(interval)} bars / day</span>
              </div>
              <div className="fl-stat">
                <span className="fl-stat-label">bars held</span>
                <span className="fl-stat-value num">{coverage.bars.toLocaleString()}</span>
                <span className="fl-stat-sub">of {coverage.max_fetch.toLocaleString()} fetchable</span>
              </div>
              <div className="fl-stat">
                <span className="fl-stat-label">span</span>
                <span className="fl-stat-value num">{coverage.span_days.toFixed(1)} days</span>
                <span className="fl-stat-sub">{coverage.span_hours.toFixed(0)} hours</span>
              </div>
              <div className="fl-stat">
                <span className="fl-stat-label">from</span>
                <span className="fl-stat-value num">{stamp(coverage.from_ms)}</span>
                <span className="fl-stat-sub">first bar</span>
              </div>
              <div className="fl-stat">
                <span className="fl-stat-label">to</span>
                <span className="fl-stat-value num">{stamp(coverage.to_ms)}</span>
                <span className="fl-stat-sub">newest bar</span>
              </div>
              <div className="fl-stat">
                <span className="fl-stat-label">freshness</span>
                <span className="fl-stat-value num">{age(coverage.to_ms)}</span>
                <span className="fl-stat-sub">since last bar</span>
              </div>
              <div className="fl-stat">
                <span className="fl-stat-label">bars required</span>
                <span className={`fl-stat-value num${shortOnHistory ? ' is-short' : ''}`}>{needed}</span>
                <span className="fl-stat-sub">train {lookback || 'all'} + hold back {horizon}</span>
              </div>
            </div>
            <div className="fl-history-foot">
              {shortOnHistory ? (
                <>
                  <span className="fl-cov-warn">
                    not enough history to hold back {horizon} bars for grading
                  </span>
                  <button type="button" className="btn fl-dl" disabled={downloading} onClick={() => void downloadMore()}>
                    {downloading ? 'downloading…' : 'download more'}
                  </button>
                </>
              ) : (
                <span className="fl-cov-ok">
                  enough history to forecast {horizonLabel(hours, interval).split(' · ')[0]} ahead
                  ({horizon} bars)
                </span>
              )}
            </div>
          </>
        ) : (
          <span className="fl-history-value num">{historyLine}</span>
        )}
      </Section>

      <Section
        id="fl.models"
        title="Model to train"
        meta={<span>selected: {method}</span>}
      >
        <p className="fl-model-note">
          Each model produces a different forecast. Choose one to train, then press forecast. The
          score column is how that model has actually performed on this symbol.
        </p>
        <div className="fl-models" ref={modelsRef}>
          {MODELS.map((mn) => {
            const row = scores.find((sc) => sc.model === mn);
            const chosen = method === mn;
            return (
              <button
                type="button"
                key={mn}
                className={`fl-model${chosen ? ' is-chosen' : ''}`}
                aria-pressed={chosen}
                onClick={() => {
                  setMethod(mn);
                  setPanelOpen(true);
                }}
              >
                <span className="fl-model-name">
                  {mn}
                  {study?.best === mn && <span className="fl-model-best">best</span>}
                  {chosen && <span className="fl-model-chosen">selected</span>}
                </span>
                <span className="fl-model-desc">{describeModel(mn)}</span>
                <span className="fl-model-facts">
                  <span className="fl-fact"><i>family</i>{modelDetails(mn).family}</span>
                  <span className="fl-fact"><i>lookback</i>{modelDetails(mn).lookback}</span>
                  <span className="fl-fact"><i>params</i>{modelDetails(mn).param}</span>
                  <span className="fl-fact"><i>output</i>{modelDetails(mn).shape}</span>
                  {row && (
                    <>
                      <span className="fl-fact fl-fact-score"><i>mape</i>{row.mape.toFixed(3)}%</span>
                      <span className="fl-fact"><i>mae</i>{priceLabel(row.mae)}</span>
                      <span className="fl-fact"><i>80% band</i>±{priceLabel(row.band80)}</span>
                      <span className="fl-fact"><i>coverage</i>{(row.coverage80 * 100).toFixed(0)}%</span>
                    </>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      </Section>

      <Section id="fl.forecast" title="Forecast" forceOpen={request?.token ?? 0} meta={<span>{request ? `${request.symbol} · ${request.method}` : 'not run'}</span>}>
        {request === null && (
          <p className="fl-hint">
            press <b>train + forecast</b> at the top — the model trains on the history, then
            forecasts the most recent {horizon} bars whose outcome is already known.
          </p>
        )}

        {optimized && !optimized.ok && <p className="fl-hint">optimiser: {optimized.error}</p>}
        {optimized?.ok && optimized.best && (
          <p className="fl-opt-out">
            <b>optimised</b> w[{optWeights.map((v) => v.toFixed(2)).join(',')}]
            {' '}sma{optimized.best.params.sma} α{optimized.best.params.alpha.toFixed(2)}
            {' '}damp{optimized.best.params.damp.toFixed(2)} · MAE {optimized.best.mae.toFixed(2)}
            {' '}· {optimized.iterations} iterations / {optimized.evaluations} evals
            {' '}· {optimized.device}/{optimized.compute}
          </p>
        )}

        <div ref={chartRef} className="fl-chart-slot">
          {runs.length > 0 && (
            <div className="fl-runs">
              <span className="fl-runs-label">{runs.length} model{runs.length === 1 ? '' : 's'} on this chart</span>
              <button type="button" className={`chip chip-toggle${showFan ? ' on' : ''}`} aria-pressed={showFan} onClick={() => setShowFan((v) => !v)}>
                fan
              </button>
              <button type="button" className={`chip chip-toggle${showSigma ? ' on' : ''}`} aria-pressed={showSigma} onClick={() => setShowSigma((v) => !v)}>
                ±1σ
              </button>
              <button type="button" className={`chip chip-toggle${showError ? ' on' : ''}`} aria-pressed={showError} onClick={() => setShowError((v) => !v)}>
                error
              </button>
              <button type="button" className="btn fl-runs-clear" onClick={() => setRuns([])}>
                clear overlays
              </button>
            </div>
          )}
          {graded.result && isLevelModel(graded.result.method) && (
            <p className="fl-level-note">
              <b>{graded.result.method}</b> forecasts a <b>level</b>, so its line is flat by
              construction — that is the model's output, not a rendering fault. The spread comes from
              the fan, which is where this forecast's real information lives.
            </p>
          )}
          <ForecastChart
            times={
              graded.result?.anchor_t
                ? [graded.result.anchor_t, ...gradedPoints.map((p) => p.t)]
                : gradedPoints.map((p) => p.t)
            }
            actual={
              graded.result?.anchor_price !== undefined
                ? [graded.result.anchor_price, ...gradedPoints.map((p) => p.actual)]
                : gradedPoints.map((p) => p.actual)
            }
            forecasts={runs.map((r) => ({
              label: r.method,
              values:
                graded.result?.anchor_price !== undefined
                  ? [graded.result.anchor_price, ...r.points.map((p) => p.forecast)]
                  : r.points.map((p) => p.forecast),
              mape: r.mape,
            }))}
            sigma={showSigma ? errs?.rmse : undefined}
            paths={
              graded.result?.paths?.map((pth) =>
                graded.result?.anchor_price !== undefined ? [graded.result.anchor_price, ...pth] : pth,
              ) ?? null
            }
            history={graded.result?.history}
            historyTimes={graded.result?.history_times}
            showWhiskers={showError}
            fan={
              !showFan
                ? null
                : graded.result?.fan && graded.result?.anchor_price !== undefined
                ? {
                    p10: [graded.result.anchor_price, ...graded.result.fan.p10],
                    p25: [graded.result.anchor_price, ...graded.result.fan.p25],
                    p50: [graded.result.anchor_price, ...graded.result.fan.p50],
                    p75: [graded.result.anchor_price, ...graded.result.fan.p75],
                    p90: [graded.result.anchor_price, ...graded.result.fan.p90],
                  }
                : graded.result?.fan ?? null
            }
          />
        </div>

        {graded.error && <p className="fl-hint">forecast unavailable — {graded.error}</p>}

        {graded.result && gradedPoints.length > 0 && (
          <div className="fl-cmp">
            <div className="fl-cmp-head">
              <span>
                forecast vs actual · {graded.result.method} · {graded.result.symbol}
              </span>
              <span className="num fl-cmp-mape">
                {graded.result.source === 'agent' ? 'agent' : 'platform'} · {graded.result.device ?? 'cpu'}/{graded.result.compute ?? 'go'}
                {' '}· MAPE {(errs?.mape_pct ?? 0).toFixed(3)}%
                {graded.result?.calibration ? ` · λ ${graded.result.calibration.lambda.toFixed(2)}` : ''}
              </span>
            </div>
            <div className="fl-cmp-table">
              <div className="fl-cmp-row fl-cmp-row-head">
                <span>#</span><span>forecast</span><span>actual</span><span>error</span>
              </div>
              {gradedPoints.map((p, i) => (
                <div className="fl-cmp-row" key={p.t}>
                  <span className="num">{i + 1}</span>
                  <span className="num fl-cmp-fc">{priceLabel(p.forecast)}</span>
                  <span className="num fl-cmp-real">{priceLabel(p.actual)}</span>
                  <span className={`num ${p.error >= 0 ? 'fl-over' : 'fl-under'}`}>
                    {p.error >= 0 ? '+' : ''}{priceLabel(p.error)}
                  </span>
                </div>
              ))}
            </div>
            <div className="fl-cmp-stats">
              <span>MAE <b className="num">{priceLabel(errs?.mae ?? 0)}</b></span>
              <span>RMSE <b className="num">{priceLabel(errs?.rmse ?? 0)}</b></span>
              <span>bias <b className="num">{(errs?.bias ?? 0) >= 0 ? '+' : ''}{priceLabel(errs?.bias ?? 0)}</b></span>
              <span>max <b className="num">{priceLabel(errs?.max_error ?? 0)}</b></span>
            </div>
          </div>
        )}
      </Section>

      {windowCount > 0 && (
        <Section
          id="fl.ranking"
          title="Model ranking"
          meta={<span>{scores.length} models · {windowCount} windows</span>}
        >
          {tracker && (
            <div className="fl-track">
              <span className="fl-track-line">
                searching {tracker.models} models · {tracker.iterations} iterations ·{' '}
                {tracker.fetches} fetches / {tracker.reused} reused
                {trackerHistory.length > 0
                  ? ` · ${trackerHistory.length} improvements`
                  : ' · no improvement yet'}
              </span>
              {trackerHistory.slice(-3).reverse().map((h) => (
                <span className="fl-track-item" key={`${h.ms}-${h.model}`}>
                  <b>{text(h.model ?? '—')}</b> {num(h.mape, 3)}%{' '}
                  <span className="fl-track-prev">was {text(h.prev_model)} {num(h.prev_mape, 3)}%</span>
                </span>
              ))}
            </div>
          )}
          {optimized?.ok && optimized.history && optimized.history.length > 1 && (
            <div className="fl-hist">
              <span className="fl-hist-label">convergence</span>
              {(optimized.history ?? [])
                .filter((_h, i) => {
                  const n = (optimized.history ?? []).length;
                  return i === 0 || i === n - 1 || i % Math.ceil(n / 6) === 0;
                })
                .map((h) => (
                  <span className="num fl-hist-item" key={h.iteration}>#{h.iteration} {h.mae.toFixed(2)}</span>
                ))}
            </div>
          )}
          <div className="fl-table" role="table" aria-label="model leaderboard">
            <div className="fl-row fl-row-head" role="row">
              <span role="columnheader">model</span>
              <span role="columnheader">mape</span>
              <span role="columnheader">80% band</span>
              <span role="columnheader">cov</span>
            </div>
            {scores.map((s) => {
              const peak = scores[0]?.mape || 1;
              const width = Math.max(2, Math.min(100, (s.mape / peak) * 100));
              return (
                <div className={`fl-row${s.model === study?.best ? ' is-best' : ''}`} role="row" key={s.model}>
                  <span className="fl-model" role="cell" title={s.note}>{text(s.model)}</span>
                  <span className="fl-metric" role="cell">
                    <span className="fl-bar" style={{ width: `${width}%` }} aria-hidden="true" />
                    <span className="num">{num(s.mape, 3)}%</span>
                  </span>
                  <span className="num" role="cell">±{num(s.band80, 0)}</span>
                  <span className={`num${s.coverage80 >= 0.75 && s.coverage80 <= 0.85 ? ' fl-cov-ok' : ''}`} role="cell">
                    {num(s.coverage80 * 100, 0)}%
                  </span>
                </div>
              );
            })}
          </div>
          <p className="fl-note">
            graded on {windowCount} walk-forward windows · {study?.points ?? 0} bars · out-of-sample only
          </p>
        </Section>
      )}

      <Section id="fl.backtest" title="Backtest" meta={<span>multi-day, forecast vs actual</span>}>
        <BacktestPanel symbol={sym} interval={interval} method={method} />
      </Section>

      <NewInputs symbol={sym} interval={interval} horizon={horizon} />

      {panelOpen && (
        <aside className="fl-side" role="dialog" aria-label="model parameters">
          <header className="fl-side-head">
            <h3>{method}</h3>
            <button type="button" className="fl-side-close" onClick={() => setPanelOpen(false)} aria-label="close">
              ×
            </button>
          </header>
          <p className="fl-side-desc">{describeModel(method)}</p>
          <ModelParams method={method} onChange={setMethod} />
          <div className="fl-side-facts">
            {(() => {
              const d = modelDetails(method);
              return (
                <>
                  <span className="fl-fact"><i>family</i>{d.family}</span>
                  <span className="fl-fact"><i>lookback</i>{d.lookback}</span>
                  <span className="fl-fact"><i>output</i>{d.shape}</span>
                </>
              );
            })()}
          </div>
          {(() => {
            const row = scores.find((sc) => sc.model === method);
            if (!row) {
              return (
                <p className="fl-side-note">
                  this model has no score yet on this symbol — press train + forecast, or look for it in
                  the ranking below
                </p>
              );
            }
            return (
              <div className="fl-side-score">
                <span>MAPE <b>{row.mape.toFixed(3)}%</b></span>
                <span>MAE <b>{priceLabel(row.mae)}</b></span>
                <span>80% band <b>±{priceLabel(row.band80)}</b></span>
                <span>coverage <b>{(row.coverage80 * 100).toFixed(0)}%</b></span>
              </div>
            );
          })()}
          <button type="button" className="btn fl-side-run" onClick={() => { setRequest({ symbol: sym, interval, horizon, lookback, method, token: Date.now() }); setPanelOpen(false); }}>
            ▶ train + forecast {method}
          </button>
        </aside>
      )}

      <p className="fl-footnote">
        A forecast is only meaningful next to what actually happened, so every run holds out the
        last {horizon} bars, forecasts them from the training window, and grades the result.
      </p>
    </div>
  );
});

/**
 * The parameters of the selected model, editable, plus what the optimiser does
 * with them. The grid values are the ones the lab can run; the optimiser's job
 * is the continuous space between them.
 */
function ModelParams({ method, onChange }: { method: string; onChange: (next: string) => void }) {
  const param = modelParam(method);
  if (param === null) {
    return (
      <div className="fl-params">
        <span className="fl-params-label">parameters</span>
        <span className="fl-params-hint">
          <b>best</b> has no parameters of its own: it ranks every model and takes the winner.
          Select a model to adjust it.
        </span>
      </div>
    );
  }
  const spec = MODEL_PARAMS.find((m) => m.kind === param.kind);
  if (spec === undefined || spec.values.length === 0) {
    return (
      <div className="fl-params">
        <span className="fl-params-label">parameters</span>
        <span className="fl-params-hint">
          <b>{method}</b> has no tunable parameter — it is a fixed rule.
        </span>
      </div>
    );
  }

  const build = (v: string): string => {
    switch (param.kind) {
      case 'sma': return `sma${v}`;
      case 'ema': return `ema${v}`;
      case 'trendN': return `trend${v}`;
      case 'damped': return `damped${v}`;
      case 'analog': return `analog${v}`;
      default: return method;
    }
  };

  return (
    <div className="fl-params">
      <span className="fl-params-label">parameters</span>
      <label className="fl-param-field">
        {spec.label}
        <select value={param.value} onChange={(e) => onChange(build(e.target.value))}>
          {spec.values.map((v) => (
            <option key={v} value={v}>{v} {spec.unit}</option>
          ))}
        </select>
      </label>
      <span className="fl-params-model num">{method}</span>
      <span className="fl-params-hint">
        the optimiser searches this space continuously — weights, lookback, alpha and damping —
        over 50 rounds × 64 candidates on the GPU
      </span>
    </div>
  );
}

interface FeatureRow {
  name: string;
  label: string;
  mape: number;
  beta: number;
  helped: boolean;
}

/**
 * New inputs, graded.
 *
 * Price alone behaves like a random walk at this horizon, so the interesting
 * question is whether anything else does better. These are measurements the
 * exchange publishes — funding, open interest, taker imbalance — scored on the
 * same held-out window as the price models. The verdict is whatever the numbers
 * say, including "no".
 */
function NewInputs({ symbol, interval, horizon }: { symbol: string; interval: Interval; horizon: number }) {
  const [state, setState] = useState<{ rows: FeatureRow[]; verdict: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useFx(() => {
    let dead = false;
    setLoading(true);
    setError(null);
    const q = new URLSearchParams({ symbol, interval, horizon: String(horizon) });
    fetch(`/api/forecast/features?${q.toString()}`, { headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`status ${r.status}`))))
      .then((body: { ok?: boolean; error?: string; rows?: FeatureRow[]; verdict?: string }) => {
        if (dead) return;
        if (body.ok === false) {
          setError(body.error ?? 'features unavailable');
          setState(null);
          return;
        }
        setState({ rows: body.rows ?? [], verdict: body.verdict ?? '' });
      })
      .catch((e: Error) => {
        if (!dead) setError(e.message);
      })
      .finally(() => {
        if (!dead) setLoading(false);
      });
    return () => {
      dead = true;
    };
  }, [symbol, interval, horizon]);

  return (
    <Section id="fl.inputs" title="New inputs" meta={<span>funding · open interest · order flow</span>}>
      {loading && <p className="fl-hint">reading the exchange's futures series…</p>}
      {error !== null && !loading && <p className="fl-hint">unavailable — {error}</p>}
      {state !== null && !loading && (
        <>
          <div className="fl-inputs">
            {state.rows.map((r) => (
              <div className={`fl-input${r.helped ? ' is-helping' : ''}`} key={r.name}>
                <span className="fl-input-name">{r.label}</span>
                <span className="fl-input-mape num">{Number.isFinite(r.mape) ? `${r.mape.toFixed(3)}%` : '—'}</span>
                <span className="fl-input-beta num">β {r.beta.toFixed(6)}</span>
                <span className={`fl-input-flag${r.helped ? ' is-helping' : ''}`}>
                  {r.helped ? 'beat flat line' : 'no gain'}
                </span>
              </div>
            ))}
          </div>
          <p className="fl-note">{state.verdict}</p>
        </>
      )}
      <p className="fl-note">
        graded on the same held-out bars as the price models: a feature has to beat a flat line to count
      </p>
    </Section>
  );
}
