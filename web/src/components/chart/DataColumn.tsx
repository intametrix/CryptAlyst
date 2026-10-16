import { useMemo } from 'react';
import { duration, int, multiple, num, pct, px, share, text } from '../../format';
import type { Outlook } from '../../hooks/useOutlook';
import type { AlignedSeries, LatestSummary } from '../../chartData';
import type { ToolsMsg } from '../../types';
import { Tooltip } from '../Tooltip';

export interface DataColumnProps {
  /** The `latest` bag summarised (close, trend, volume ratio, donchian 20). */
  latest: LatestSummary;
  /** The aligned overlay series; each row reads its own last value. */
  overlaySeries: readonly AlignedSeries[];
  /** The /api/outlook payload, polled by the popup. */
  outlook: Outlook | null;
  tools: ToolsMsg | null;
}

interface Row {
  key: string;
  label: string;
  value: string;
  /** Glossary keys, for the tooltip. */
  toolsKeys: string[];
}

/**
 * The DATA column of the bottom panel: label/value rows for the indicator
 * readouts (ported from ChartPanel's footer chips) plus the outlook sections
 * (levels, ETA, forecast and model basis — ported from OutlookStrip's data
 * sources, rendered as rows).
 */
export function DataColumn({ latest, overlaySeries, outlook, tools }: DataColumnProps) {
  const seriesMap = useMemo(() => new Map(overlaySeries.map((s) => [s.key, s.data])), [overlaySeries]);

  const lastOf = (key: string): number | null => {
    const points = seriesMap.get(key) ?? [];
    return points.length > 0 ? points[points.length - 1].value : null;
  };

  const indicatorRows: Row[] = [
    { key: 'close', label: 'close', value: px(latest.close), toolsKeys: ['price', 'close'] },
    { key: 'volume_ratio', label: 'vol ratio', value: multiple(latest.volumeRatio), toolsKeys: ['volume_ratio'] },
    { key: 'trend', label: 'trend', value: latest.trend !== null ? text(latest.trend) : '—', toolsKeys: ['trend'] },
    {
      key: 'atr20',
      label: 'ATR 20',
      value: (() => {
        const v = lastOf('atr20');
        return v === null ? '—' : px(v);
      })(),
      toolsKeys: ['atr20'],
    },
    {
      key: 'atr_pct',
      label: 'ATR %',
      value: (() => {
        const v = lastOf('atr_pct');
        return v === null ? '—' : share(v, 3);
      })(),
      toolsKeys: ['atr_pct'],
    },
    {
      key: 'adx14',
      label: 'ADX 14',
      value: (() => {
        const v = lastOf('adx14');
        return v === null ? '—' : num(v, 1);
      })(),
      toolsKeys: ['adx14'],
    },
    {
      key: 'plus_di',
      label: '+DI',
      value: (() => {
        const v = lastOf('plus_di');
        return v === null ? '—' : num(v, 1);
      })(),
      toolsKeys: ['plus_di'],
    },
    {
      key: 'minus_di',
      label: '−DI',
      value: (() => {
        const v = lastOf('minus_di');
        return v === null ? '—' : num(v, 1);
      })(),
      toolsKeys: ['minus_di'],
    },
    {
      key: 'rsi14',
      label: 'RSI 14',
      value: (() => {
        const v = lastOf('rsi14');
        return v === null ? '—' : num(v, 1);
      })(),
      toolsKeys: ['rsi14'],
    },
    {
      key: 'donchian20_upper',
      label: 'don 20 hi',
      value: latest.donchian20Upper !== null ? px(latest.donchian20Upper) : '—',
      toolsKeys: ['donchian_channel'],
    },
    {
      key: 'donchian20_lower',
      label: 'don 20 lo',
      value: latest.donchian20Lower !== null ? px(latest.donchian20Lower) : '—',
      toolsKeys: ['donchian_channel'],
    },
  ];

  const event = outlook?.event ?? null;
  const horizon = outlook?.shortHorizon ?? null;
  const levels = [...(outlook?.supports ?? []), ...(outlook?.resistances ?? [])].sort(
    (a, b) => b.strength - a.strength,
  );
  const forecastPoints = outlook?.forecast.points.length ?? 0;

  return (
    <section className="cp-data" aria-label="indicator data">
      <div className="cp-data-rows">
        {indicatorRows.map((row) => (
          <Tooltip
            key={row.key}
            tone="stat"
            keys={row.toolsKeys}
            tools={tools}
            fallbackTitle={row.label}
            value={row.value}
          >
            <div className="cp-data-row" tabIndex={0}>
              <span className="cp-data-label">{row.label}</span>
              <span className="cp-data-value num">{row.value}</span>
            </div>
          </Tooltip>
        ))}
      </div>

      <div className="cp-data-rows cp-data-outlook">
        <h3 className="cp-data-heading">outlook</h3>
        {outlook === null ? (
          <p className="empty">no levels or forecast for this symbol yet</p>
        ) : (
          <>
            {levels.length === 0 ? (
              <div className="cp-data-row">
                <span className="cp-data-label">levels</span>
                <span className="cp-data-value">none measured</span>
              </div>
            ) : (
              levels.map((level) => (
                <Tooltip
                  key={`${level.kind}-${level.price}`}
                  tone="stat"
                  keys={[level.kind]}
                  tools={tools}
                  fallbackTitle={level.kind === 'support' ? 'Support' : 'Resistance'}
                  value={`${px(level.price)} · ${text(level.touches)} touches · ${Math.round(level.strength * 100)}%`}
                >
                  <div className={`cp-data-row cp-level-${level.kind}`} tabIndex={0}>
                    <span className="cp-data-label">{level.kind === 'support' ? 'S' : 'R'}</span>
                    <span className="cp-data-value num">
                      {px(level.price)}
                      <span className="cp-data-sub">
                        {' '}
                        {text(level.touches)}× · {Math.round(level.strength * 100)}%
                        {level.distancePct === null ? '' : ` · ${pct(level.distancePct)} away`}
                      </span>
                    </span>
                  </div>
                </Tooltip>
              ))
            )}
            {event !== null && (
              <Tooltip
                tone="stat"
                keys={['eta_reversal']}
                tools={tools}
                fallbackTitle="ETA to reversal"
                label={event.etaBasis !== '' ? event.etaBasis : 'How long the current excursion is expected to run.'}
                value={
                  event.inProgress && event.etaReversalSec !== null && event.etaReversalSec > 0
                    ? `${duration(event.etaReversalSec)} (${duration(event.etaLowSec)}–${duration(event.etaHighSec)})`
                    : 'no event in progress'
                }
              >
                <div className="cp-data-row" tabIndex={0}>
                  <span className="cp-data-label">ETA reversal</span>
                  <span className="cp-data-value num">
                    {event.inProgress && event.etaReversalSec !== null && event.etaReversalSec > 0
                      ? `${duration(event.etaReversalSec)} ±`
                      : 'idle'}
                    <span className="cp-data-sub">
                      {' '}
                      {event.inProgress ? `${text(event.state)} · ${num(event.confidence, 2)}` : 'no excursion'}
                    </span>
                  </span>
                </div>
              </Tooltip>
            )}
            {horizon !== null && (
              <Tooltip
                tone="stat"
                keys={['short_horizon']}
                tools={tools}
                fallbackTitle="Short horizon"
                label={horizon.basis !== '' ? horizon.basis : 'How the short-horizon estimate is produced.'}
                value={horizon.edge !== '' ? horizon.edge : null}
              >
                <div className="cp-data-row" tabIndex={0}>
                  <span className="cp-data-label">horizon</span>
                  <span className="cp-data-value num">
                    {horizon.horizonSec === null ? '—' : `${text(horizon.horizonSec)}s`}
                    <span className="cp-data-sub">
                      {' '}
                      up {horizon.pUp === null ? '—' : num(horizon.pUp, 2)} / down{' '}
                      {horizon.pDown === null ? '—' : num(horizon.pDown, 2)}
                    </span>
                  </span>
                </div>
              </Tooltip>
            )}
            {forecastPoints > 0 && (
              <div className="cp-data-row">
                <span className="cp-data-label">forecast</span>
                <span className="cp-data-value num">
                  {int(forecastPoints)} points · {text(outlook?.forecast.kind)}
                </span>
              </div>
            )}
            {(horizon?.basis !== '' || event?.etaBasis !== '') && (
              <p className="cp-data-basis">
                {horizon?.basis !== '' ? <span>model: {horizon?.basis}</span> : null}
                {event?.etaBasis !== '' ? <span>eta: {event?.etaBasis}</span> : null}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
