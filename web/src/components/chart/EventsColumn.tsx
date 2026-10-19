import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { clock, int, isoUtc, multiple, num, usd } from '../../format';
import type { ChartMarker } from '../../chartData';
import { prettifyEnum } from '../../types';
import type { AlertMsg, ToolsMsg } from '../../types';
import { KindBadge, SeverityBadge } from '../Badges';
import { EventGlyph } from '../EventGlyph';
import { AlertCard } from '../AlertCard';
import type { ColorCanon } from '../../colors';
import { Tooltip } from '../Tooltip';

/**
 * The list renders the SAME card the main-page alert feed uses, so an event
 * looks identical wherever it appears. A chart marker carries less than a
 * full alert message, but every AlertMsg field is optional, so the marker's
 * fields map across one-to-one and the card renders whatever is present.
 */
function markerToAlert(mark: ChartMarker, symbol: string | null): AlertMsg {
  return {
    type: 'alert',
    ts_ms: mark.time * 1000,
    symbol,
    kind: mark.kind,
    severity: mark.severity,
    score: mark.score,
    detector: mark.detector,
    color: mark.color,
    letter: mark.letter,
    direction: mark.direction,
    summary: mark.label,
    metrics:
      mark.flowQuote !== null || mark.burstMultiple !== null
        ? { flow_quote: mark.flowQuote, burst_multiple: mark.burstMultiple }
        : null,
  };
}

export interface EventsColumnProps {
  /** Every mark for the charted symbol, newest first. */
  events: readonly ChartMarker[];
  /** The charted symbol, stamped onto the cards the list renders. */
  symbol: string | null;
  /** The shared selection — the same value the chart highlights. */
  selected: number | null;
  onSelect: (time: number | null) => void;
  tools: ToolsMsg | null;
  /** The chart's own reason for a missing event, if any. */
  emptyNote?: string;
  /** The canonical palette, so the rows match the markers exactly. */
  canon?: ColorCanon | null;
}

/**
 * The EVENTS column of the bottom panel: the selected event's detail plus the
 * list of every event. Adapted from EventsPanel minus the zoom-preset row —
 * the presets live in the popup header now. Clicking a row selects the event
 * and the chart zooms to it.
 */
export const EventsColumn = memo(function EventsColumn({
  events,
  symbol,
  selected,
  onSelect,
  tools,
  emptyNote,
  canon = null,
}: EventsColumnProps) {
  // The cards print relative times ("42s ago"), so the column owns the same
  // one-second clock the main feed shares with its cards.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const selectedMark = useMemo(
    () => (selected === null ? null : events.find((m) => m.time === selected) ?? null),
    [events, selected],
  );
  const rowsRef = useRef<HTMLUListElement | null>(null);

  // Selecting a marker on the chart must reveal the matching row.
  useEffect(() => {
    if (selected === null) return;
    const list = rowsRef.current;
    if (list === null) return;
    const row = list.querySelector<HTMLElement>(`[data-event-time="${selected}"]`);
    row?.scrollIntoView({ block: 'nearest' });
  }, [selected]);

  return (
    <section className="events-panel cp-events" aria-label="chart events">
      {/* --- the selected event, or the affordance to pick one --------------- */}
      <div className="events-detail" data-has-selection={selectedMark !== null}>
        {selectedMark === null ? (
          <p className="events-hint">
            {events.length === 0
              ? (emptyNote ?? 'no events on this symbol in the loaded window')
              : `select an event — click a marker on the chart or a row below · ${int(events.length)} available`}
          </p>
        ) : (
          <div className="events-detail-body">
            <EventGlyph
              kind={selectedMark.kind}
              severity={selectedMark.severity}
              color={selectedMark.color}
              letter={selectedMark.letter}
              direction={selectedMark.direction}
              canon={canon}
              size="lg"
            />
            <div className="events-detail-fields">
              <div className="events-detail-head">
                <Tooltip keys={['kind']} tools={tools} fallbackTitle="Kind" value={prettifyEnum(selectedMark.kind)}>
                  <span tabIndex={0}>
                    <KindBadge kind={selectedMark.kind} color={selectedMark.color} canon={canon} />
                  </span>
                </Tooltip>
                <Tooltip
                  keys={['severity']}
                  tools={tools}
                  fallbackTitle="Severity"
                  value={prettifyEnum(selectedMark.severity, 'unrated')}
                >
                  <span tabIndex={0}>
                    <SeverityBadge severity={selectedMark.severity} canon={canon} />
                  </span>
                </Tooltip>
                <span className="events-detail-time num" title={isoUtc(selectedMark.time * 1000)}>
                  {clock(selectedMark.time * 1000)}
                </span>
                <button type="button" className="link-btn events-clear" onClick={() => onSelect(null)}>
                  clear selection
                </button>
              </div>
              <dl className="events-detail-grid">
                <div>
                  <dt>score</dt>
                  <dd className="num">{selectedMark.score === null ? '—' : num(selectedMark.score, 1)}</dd>
                </div>
                <div>
                  <dt>flow</dt>
                  <dd className="num">{usd(selectedMark.flowQuote)}</dd>
                </div>
                <div>
                  <dt>burst</dt>
                  <dd className="num">{multiple(selectedMark.burstMultiple)}</dd>
                </div>
                <div>
                  <dt>direction</dt>
                  <dd>{selectedMark.direction === 1 ? 'up' : 'down'}</dd>
                </div>
              </dl>
              {selectedMark.label !== '' && <p className="events-detail-summary">{selectedMark.label}</p>}
            </div>
          </div>
        )}
      </div>

      {/* --- the event list: the main page's card, one per event ------------- */}
      <div className="events-list-wrap">
        {events.length === 0 ? (
          <p className="empty">{emptyNote ?? 'no events on this symbol yet'}</p>
        ) : (
          <ul className="events-rows cp-alert-rows" id="cp-events-rows" ref={rowsRef}>
            {events.map((mark) => {
              const isSelected = mark.time === selected;
              return (
                <li key={`${mark.time}-${mark.letter}`}>
                  <div
                    className={`cp-alert-slot${isSelected ? ' selected' : ''}`}
                    data-event-time={mark.time}
                    role="button"
                    tabIndex={0}
                    aria-pressed={isSelected}
                    onClick={() => onSelect(isSelected ? null : mark.time)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        onSelect(isSelected ? null : mark.time);
                      }
                    }}
                    title={`zoom to ${clock(mark.time * 1000)}`}
                  >
                    <AlertCard
                      alert={markerToAlert(mark, symbol)}
                      flash={false}
                      tools={tools}
                      nowMs={nowMs}
                      canon={canon}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
});
