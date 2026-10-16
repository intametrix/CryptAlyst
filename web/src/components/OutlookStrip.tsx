import { duration, num, pct, px, text } from '../format';
import type { Outlook, OutlookLevel } from '../hooks/useOutlook';
import type { ToolsMsg } from '../types';
import { Tooltip } from './Tooltip';

export interface OutlookStripProps {
  outlook: Outlook | null;
  tools: ToolsMsg | null;
}

/** How many levels of each kind to state before summarising the rest. */
const LEVELS_SHOWN = 2;

/**
 * One level, stated in full.
 *
 * The earlier rendering was a bare price between two letters — `S — R 0.355855` —
 * which does not say whether that is one level or two, which side it belongs to,
 * or what the number means. Each level now names its side, its price, how often
 * it has been tested and how strong it is, so the line is readable without
 * knowing the layout.
 */
function LevelRow({ level, tools }: { level: OutlookLevel; tools: ToolsMsg | null }) {
  const side = level.kind === 'support' ? 'S' : 'R';
  const strengthPct = Math.round(level.strength * 100);
  return (
    <Tooltip
      keys={[level.kind]}
      tools={tools}
      fallbackTitle={level.kind === 'support' ? 'Support' : 'Resistance'}
      label={
        level.kind === 'support'
          ? 'A price below the current level where buying has repeatedly absorbed selling. Strength and touch count are what the level is worth; the chart line is drawn more opaque the stronger it is.'
          : 'A price above the current level where selling has repeatedly capped buying. Strength and touch count are what the level is worth.'
      }
      value={`${px(level.price)} · ${text(level.touches)} touches · ${strengthPct}%`}
      tone="stat"
    >
      <span className={`level-row level-${level.kind}`} tabIndex={0}>
        <b className="level-side">{side}</b>
        <span className="level-price num">{px(level.price)}</span>
        <span className="level-detail num">
          {text(level.touches)}× tested · {strengthPct}% strong
          {level.distancePct === null ? '' : ` · ${pct(level.distancePct)} away`}
        </span>
      </span>
    </Tooltip>
  );
}

/**
 * The support/resistance map, the ETA to reversal, and the short-horizon read.
 *
 * The two textual fields are displayed **verbatim**: `short_horizon.edge`
 * currently reads "no measurable edge at this horizon", and that sentence is the
 * honest answer — a number would be an invention the model disclaims. `basis` and
 * `eta_basis` say where each figure comes from and are shown alongside.
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
  const supports = outlook.supports.slice(0, LEVELS_SHOWN);
  const resistances = outlook.resistances.slice(0, LEVELS_SHOWN);
  const extraSupports = outlook.supports.length - supports.length;
  const extraResistances = outlook.resistances.length - resistances.length;
  const forecastPoints = outlook.forecast.points.length;

  return (
    <div className="outlook-strip">
      <span className="outlook-label">outlook</span>

      {/* The levels, each one named. An empty side says so in words rather than
          rendering a dash that could mean "none" or "not loaded". */}
      <div className="outlook-levels" role="group" aria-label="support and resistance levels">
        <span className="outlook-levels-label">levels</span>
        {supports.length === 0 && resistances.length === 0 ? (
          <span className="outlook-muted">
            no support or resistance measured for this symbol in the current window
          </span>
        ) : (
          <>
            {supports.map((level) => (
              <LevelRow key={`s-${level.price}`} level={level} tools={tools} />
            ))}
            {supports.length === 0 && <span className="outlook-muted">no support measured</span>}
            {extraSupports > 0 && <span className="outlook-more num">+{text(extraSupports)} more support</span>}
            {resistances.map((level) => (
              <LevelRow key={`r-${level.price}`} level={level} tools={tools} />
            ))}
            {resistances.length === 0 && <span className="outlook-muted">no resistance measured</span>}
            {extraResistances > 0 && (
              <span className="outlook-more num">+{text(extraResistances)} more resistance</span>
            )}
          </>
        )}
      </div>

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
            {/* The server's own sentence, as text rather than tooltip-only. */}
            {horizon.edge !== '' && <span className="outlook-said">{horizon.edge}</span>}
          </span>
        </Tooltip>
      )}

      {(horizon?.basis !== '' && horizon?.basis !== undefined) ||
      (event?.etaBasis !== '' && event?.etaBasis !== undefined) ? (
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
