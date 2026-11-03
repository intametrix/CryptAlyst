import { memo } from 'react';
import { TickerPicker } from './TickerPicker';
import type { Interval } from '../types';

export interface HorizonChoice {
  hours: number;
  label: string;
}

/**
 * The controls: which symbol, which frame, how far ahead, how much history.
 *
 * Extracted from the forecast panel so the panel is a layout of sections rather than a
 * layout plus nine interleaved controls. It owns no state: every value goes out through
 * a callback, which is what makes the panel's request object the single source of truth.
 */
export const ParameterBar = memo(function ParameterBar({
  symbol,
  onPickSymbol,
  interval,
  onPickInterval,
  hours,
  horizonChoices,
  onPickHorizon,
  trainHours,
  trainChoices,
  onPickTrain,
  windows,
  onWindows,
  iterations,
  onIterations,
  optimizing,
  onRun,
  onOptimize,
  forecastReady,
  onShowChart,
  liveText,
  isLive,
  requestPending,
}: {
  symbol: string;
  onPickSymbol: (symbol: string) => void;
  interval: Interval;
  onPickInterval: (interval: Interval) => void;
  hours: number;
  horizonChoices: readonly HorizonChoice[];
  onPickHorizon: (hours: number) => void;
  trainHours: number;
  trainChoices: readonly HorizonChoice[];
  onPickTrain: (hours: number) => void;
  windows: number;
  onWindows: (n: number) => void;
  iterations: number;
  onIterations: (n: number) => void;
  optimizing: boolean;
  onRun: () => void;
  onOptimize: () => void;
  forecastReady: boolean;
  onShowChart: () => void;
  liveText: string;
  isLive: boolean;
  requestPending: boolean;
}) {
  return (
    <div className="fl-controls">
      <span className="fl-ctl fl-ctl-ticker">
        ticker
        <TickerPicker value={symbol} onPick={onPickSymbol} />
      </span>
      <button type="button" className="btn fl-run-btn" onClick={onRun}>
        ▶ train + forecast
      </button>
      <button type="button" className="btn fl-opt-btn" disabled={optimizing} onClick={onOptimize}>
        {optimizing ? 'optimising…' : '⚙ optimise on GPU'}
      </button>
      <label className="fl-ctl">
        interval
        <select value={interval} onChange={(e) => onPickInterval(e.target.value as Interval)}>
          {(['1m', '5m', '15m', '1h', '4h', '1d'] as Interval[]).map((iv) => (
            <option key={iv} value={iv}>{iv}</option>
          ))}
        </select>
      </label>
      <label className="fl-ctl">
        forecast ahead
        <select value={hours} onChange={(e) => onPickHorizon(Number(e.target.value))}>
          {horizonChoices.map((c) => (
            <option key={c.hours} value={c.hours}>{c.label}</option>
          ))}
        </select>
      </label>
      <label className="fl-ctl">
        train over
        <select value={trainHours} onChange={(e) => onPickTrain(Number(e.target.value))}>
          {trainChoices.map((c) => (
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
          onChange={(e) => onWindows(Math.max(2, Math.min(30, Number(e.target.value) || 2)))}
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
          onChange={(e) => onIterations(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
        />
      </label>
      {forecastReady && (
        <button type="button" className="fl-jump" onClick={onShowChart}>
          ✓ forecast ready — show chart
        </button>
      )}
      <span className={`fl-live${isLive ? ' is-live' : ''}`}>{liveText}</span>
      {requestPending && <span className="fl-pending" aria-hidden="true" />}
    </div>
  );
});
