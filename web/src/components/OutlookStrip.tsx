import { duration, num, pct, px, text } from '../format';
import type { Outlook } from '../hooks/useOutlook';
import type { ToolsMsg } from '../types';
import { Tooltip } from './Tooltip';

export interface OutlookStripProps {
  outlook: Outlook | null;
  tools: ToolsMsg | null;
}

/**
 * The support/resistance map, the ETA to reversal, and the short-horizon read.
 *
 * The two textual fields are displayed **verbatim**. `short_horizon.edge`
 * currently reads "no measurable edge at this horizon", and that sentence is the
 * honest answer — a number would be an invention the model explicitly disclaims.
 * `basis` and `eta_basis` say where each figure comes from, and are shown
 * alongside rather than hidden in a tooltip.
 */
export function OutlookStrip({ outlook, tools }: OutlookStripProps) {
  if (outlook === null) {
    return (
      <div className="outlook-strip outlook-empty">
        <span className="outlook-label">outlook</span>
        <span className="outlook-muted">no levels or forecast for this symbol yet</span>
      </div>
    );
  }

  const event = outlook.event;
  const horizon = outlook.shortHorizon;
  const strongestSupport = outlook.supports[0] ?? null;
  const strongestResistance = outlook.resistances[0] ?? null;
  const forecastPoints = outlook.forecast.points.length;

  return (
    <div className="outlook-strip">
      <span className="outlook-label">outlook</span>

      {/* Supports and resistances: the prices the user asked for, at their real
          values, with how strong and how well-tested each one is. */}
      <Tooltip
        keys={['support']}
        tools={tools}
        fallbackTitle="Nearest support"
        label="The strongest measured support below price. Its strength and touch count are what the level is worth; the line on the chart is drawn more opaque the stronger it is."
        value={strongestSupport === null ? 'none measured' : px(strongestSupport.price)}
        tone="stat"
      >
        <span className="outlook-chip outlook-support" tabIndex={0}>
          <b>S</b>
          {strongestSupport === null ? '—' : px(strongestSupport.price)}
          {strongestSupport !== null && (
            <span className="outlook-sub">
              {text(strongestSupport.touches)}x · {pct(strongestSupport.distancePct)}
            </span>
          )}
        </span>
      </Tooltip>

      <Tooltip
        keys={['resistance']}
        tools={tools}
        fallbackTitle="Nearest resistance"
        label="The strongest measured resistance above price. A level's touch count is the evidence behind its strength."
        value={strongestResistance === null ? 'none measured' : px(strongestResistance.price)}
        tone="stat"
      >
        <span className="outlook-chip outlook-resistance" tabIndex={0}>
          <b>R</b>
          {strongestResistance === null ? '—' : px(strongestResistance.price)}
          {strongestResistance !== null && (
            <span className="outlook-sub">
              {text(strongestResistance.touches)}x · {pct(strongestResistance.distancePct)}
            </span>
          )}
        </span>
      </Tooltip>

      {/* The ETA to reversal, with its range and the basis for it. */}
      {event !== null && (
        <Tooltip
          keys={['eta_reversal']}
          tools={tools}
          fallbackTitle="ETA to reversal"
          label={
            event.etaBasis !== ''
              ? event.etaBasis
              : 'How long the current excursion is expected to run, from the forecast half-life.'
          }
          value={
            event.inProgress && event.etaReversalSec !== null && event.etaReversalSec > 0
              ? `${duration(event.etaReversalSec)} (${duration(event.etaLowSec)}–${duration(event.etaHighSec)})`
              : 'no event in progress'
          }
          tone="stat"
        >
          <span className="outlook-chip" tabIndex={0}>
            <b>ETA</b>
            {event.inProgress && event.etaReversalSec !== null && event.etaReversalSec > 0
              ? `${duration(event.etaReversalSec)} ±`
              : 'idle'}
            <span className="outlook-sub">
              {event.inProgress ? `${text(event.state)} · ${num(event.confidence, 2)}` : 'no excursion'}
            </span>
          </span>
        </Tooltip>
      )}

      {/* The short-horizon read. `edge` and `basis` are quoted, not summarised. */}
      {horizon !== null && (
        <Tooltip
          keys={['short_horizon']}
          tools={tools}
          fallbackTitle="Short horizon"
          label={horizon.basis !== '' ? horizon.basis : 'How the short-horizon estimate is produced.'}
          value={horizon.edge !== '' ? horizon.edge : null}
          tone="stat"
        >
          <span className="outlook-chip outlook-horizon" tabIndex={0}>
            <b>{horizon.horizonSec === null ? '—' : text(horizon.horizonSec)}s</b>
            <span className="outlook-sub">
              up {horizon.pUp === null ? '—' : num(horizon.pUp, 2)} / down{' '}
              {horizon.pDown === null ? '—' : num(horizon.pDown, 2)}
            </span>
            {/*
              The server's own sentence, rendered as text — not only inside a
              tooltip. "No measurable edge at this horizon" is the honest answer
              and must be readable at a glance; a number here would be an
              invention the model explicitly disclaims.
            */}
            {horizon.edge !== '' && <span className="outlook-said">{horizon.edge}</span>}
          </span>
        </Tooltip>
      )}

      {/* The basis, quoted: where each figure comes from, in the server's words. */}
      {(horizon?.basis !== '' && horizon?.basis !== undefined) || (event?.etaBasis !== '' && event?.etaBasis !== undefined) ? (
        <p className="outlook-basis">
          {horizon?.basis !== '' && horizon?.basis !== undefined ? <span>model: {horizon.basis}</span> : null}
          {event?.etaBasis !== '' && event?.etaBasis !== undefined ? <span>eta: {event.etaBasis}</span> : null}
        </p>
      ) : null}

      {forecastPoints > 0 && (
        <Tooltip
          keys={['forecast']}
          tools={tools}
          fallbackTitle="Forecast"
          label="A projection, not history: the dashed line is the model's expected path and the shaded band is its uncertainty. The band is drawn because a bare line would imply precision the model disclaims."
          value={`${text(forecastPoints)} points · ${text(outlook.forecast.kind)}`}
          tone="stat"
        >
          <span className="outlook-chip outlook-forecast" tabIndex={0}>
            <b>forecast</b>
            <span className="outlook-sub">{text(outlook.forecast.kind)}</span>
          </span>
        </Tooltip>
      )}
    </div>
  );
}
