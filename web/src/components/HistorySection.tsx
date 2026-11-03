import { memo } from 'react';
import { num } from '../format';

export interface Coverage {
  interval: string;
  bars: number;
  max_fetch: number;
  span_days: number;
  span_hours: number;
  from_ms: number;
  to_ms: number;
}

/** Seconds since a timestamp, matching the app's age formatter. */
function ageOf(ms: number): string {
  if (!ms) return '—';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
}

function stamp(ms: number): string {
  if (!ms) return '—';
  const d = new Date(ms);
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * How much history exists, and whether it is enough to hold bars back for grading.
 *
 * Extracted from the forecast panel: it is a view over one coverage object plus two
 * numbers, and it owns no state beyond the download button's busy flag, which the
 * caller passes in.
 */
export const HistorySection = memo(function HistorySection({
  coverage,
  barsPerDay,
  needed,
  lookback,
  horizon,
  shortOnHistory,
  downloading,
  horizonText,
  fallbackLine,
  onDownload,
}: {
  coverage: Coverage | null;
  barsPerDay: (interval: string) => number;
  needed: number;
  lookback: number;
  horizon: number;
  shortOnHistory: boolean;
  downloading: boolean;
  horizonText: string;
  fallbackLine: string;
  onDownload: () => void;
}) {
  if (coverage === null) return <span className="fl-history-value num">{fallbackLine}</span>;

  const cells: [string, string, string][] = [
    ['frequency', coverage.interval, `${barsPerDay(coverage.interval)} bars / day`],
    ['bars held', coverage.bars.toLocaleString(), `of ${coverage.max_fetch.toLocaleString()} fetchable`],
    ['span', `${coverage.span_days.toFixed(1)} days`, `${coverage.span_hours.toFixed(0)} hours`],
    ['from', stamp(coverage.from_ms), 'first bar'],
    ['to', stamp(coverage.to_ms), 'newest bar'],
    ['freshness', ageOf(coverage.to_ms), 'since last bar'],
    ['bars required', num(needed, 0), `train ${lookback || 'all'} + hold back ${horizon}`],
  ];

  return (
    <>
      <div className="fl-stats">
        {cells.map(([label, value, sub]) => (
          <div className="fl-stat" key={label}>
            <span className="fl-stat-label">{label}</span>
            <span className={`fl-stat-value num${label === 'bars required' && shortOnHistory ? ' is-short' : ''}`}>{value}</span>
            <span className="fl-stat-sub">{sub}</span>
          </div>
        ))}
      </div>
      <div className="fl-history-foot">
        {shortOnHistory ? (
          <>
            <span className="fl-cov-warn">not enough history to hold back {horizon} bars for grading</span>
            <button type="button" className="btn fl-dl" disabled={downloading} onClick={onDownload}>
              {downloading ? 'downloading…' : 'download more'}
            </button>
          </>
        ) : (
          <span className="fl-cov-ok">
            enough history to forecast {horizonText} ahead ({horizon} bars)
          </span>
        )}
      </div>
    </>
  );
});
