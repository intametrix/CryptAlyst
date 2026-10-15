import { memo, useEffect, useMemo, useRef } from 'react';
import { clock, int, isoUtc, multiple, num, usd } from '../format';
import { presetAction, type ZoomPreset } from '../zoom';
import type { ChartMarker } from '../chartData';
import { detectorLabel, prettifyEnum } from '../types';
import type { ToolsMsg } from '../types';
import { KindBadge, SeverityBadge } from './Badges';
import { EventGlyph } from './EventGlyph';
import type { ColorCanon } from '../colors';
import { Tooltip } from './Tooltip';

/** A compact one-line summary of an event, for the list rows. */
function rowSummary(mark: ChartMarker): string {
  const bits: string[] = [];
  if (mark.score !== null) bits.push(`score ${num(mark.score, 0)}`);
  if (mark.burstMultiple !== null) bits.push(`${multiple(mark.burstMultiple)} baseline`);
  if (mark.flowQuote !== null) bits.push(`${usd(mark.flowQuote)} flow`);
  return bits.join(' · ');
}

export type { ZoomPreset };

export interface EventsPanelProps {
  /** Every mark for the charted symbol, newest first. */
  events: readonly ChartMarker[];
  /** The shared selection — the same value the chart highlights. */
  selected: number | null;
  onSelect: (time: number | null) => void;
  /** The presets to offer, straight from the server's plan. */
  presets: readonly ZoomPreset[];
  /** Apply a preset. */
  onZoomPreset: (preset: ZoomPreset) => void;
  /** Restore the default view — including the default interval. */
  onResetZoom: () => void;
  /** Which preset is active, for the pressed state. */
  activePreset: string | null;
  /** True while a preset is waiting for its interval to load. */
  presetPending: boolean;
  /** The interval currently loaded, so a preset can say what it needs. */
  loadedInterval: string;
  expanded: boolean;
  onToggleExpanded: () => void;
  tools: ToolsMsg | null;
  /** The chart's own reason for a missing event, if any. */
  emptyNote?: string;
  /** The canonical palette, so the rows match the markers exactly. */
  canon?: ColorCanon | null;
}

/**
 * The bottom section of the chart pop-out: the selected event's detail, the zoom
 * presets, and an expandable list of every event.
 *
 * It is deliberately compact — a fixed detail card plus a list that is capped in
 * height — because the chart must keep the dominant share of the pop-out's
 * vertical space. The row for the selected event scrolls itself into view, so a
 * selection made by clicking a marker on the chart is visible here too.
 */
export const EventsPanel = memo(function EventsPanel({
  events,
  selected,
  onSelect,
  presets,
  onZoomPreset,
  onResetZoom,
  activePreset,
  presetPending,
  loadedInterval,
  expanded,
  onToggleExpanded,
  tools,
  emptyNote,
  canon = null,
}: EventsPanelProps) {
  const selectedMark = useMemo(
    () => (selected === null ? null : events.find((m) => m.time === selected) ?? null),
    [events, selected],
  );
  const rowsRef = useRef<HTMLUListElement | null>(null);

  // Selecting a marker on the chart must reveal the matching row.
  useEffect(() => {
    if (selected === null || !expanded) return;
    const list = rowsRef.current;
    if (list === null) return;
    const row = list.querySelector<HTMLElement>(`[data-event-time="${selected}"]`);
    row?.scrollIntoView({ block: 'nearest' });
  }, [selected, expanded]);

  return (
    <section className="events-panel" aria-label="chart events">
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
                <Tooltip
                  keys={['detector']}
                  tools={tools}
                  fallbackTitle="Detector"
                  label="Which model produced this event."
                  value={selectedMark.detector === null ? null : detectorLabel(selectedMark.detector)}
                >
                  <span className="events-detail-det" tabIndex={0}>
                    {selectedMark.detector === null ? 'detector not reported' : detectorLabel(selectedMark.detector)}
                  </span>
                </Tooltip>
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

      {/* --- zoom presets ---------------------------------------------------- */}
      <div className="events-presets" role="group" aria-label="zoom presets">
        <span className="events-presets-label">zoom</span>
        {presets.map((preset) => {
          // The button says what it will actually do: a preset already covered by
          // the loaded interval is a pure zoom, anything else is an interval
          // switch — which is a fetch, and must not be presented as free.
          const action = presetAction(preset, loadedInterval);
          const needsSwitch = action === 'switch';
          return (
            <Tooltip
              key={preset.preset}
              keys={['zoom']}
              tools={tools}
              fallbackTitle={`Zoom · ${preset.preset}`}
              label={
                preset.mode === 'fit'
                  ? 'Fit the whole loaded history.'
                  : needsSwitch
                    ? `Show the newest ${preset.preset} (${int(preset.bars)} bars of ${preset.interval} candles). It switches the chart to ${preset.interval} candles, which loads new data.`
                    : `Show the newest ${preset.preset} — ${int(preset.bars)} bars already in memory, so this is a pure zoom.`
              }
              value={activePreset === preset.preset ? 'active' : null}
            >
              <button
                type="button"
                className={`chip chip-toggle${activePreset === preset.preset ? ' on' : ''}${needsSwitch ? ' chip-switch' : ''}`}
                aria-pressed={activePreset === preset.preset}
                disabled={presetPending}
                onClick={() => onZoomPreset(preset)}
              >
                {preset.preset}
                {needsSwitch && (
                  <span className="chip-needs" aria-hidden="true">
                    {preset.interval}
                  </span>
                )}
              </button>
            </Tooltip>
          );
        })}
        <Tooltip
          keys={['zoom']}
          tools={tools}
          fallbackTitle="Reset"
          label="Restore the view the chart opens with — the default interval and the full loaded history. Escape and reopening the chart do the same."
        >
          <button type="button" className="btn" onClick={onResetZoom}>
            reset
          </button>
        </Tooltip>
        {presetPending && (
          <span className="events-limited" title="switching interval to satisfy the preset">
            loading…
          </span>
        )}
      </div>

      {/* --- the event list -------------------------------------------------- */}
      <div className="events-list-wrap">
        <button
          type="button"
          className="events-toggle"
          aria-expanded={expanded}
          onClick={onToggleExpanded}
          title={expanded ? 'collapse the event list' : 'expand the event list'}
        >
          <span className="caret" aria-hidden="true">
            {expanded ? '▾' : '▸'}
          </span>
          events <span className="num">{int(events.length)}</span>
        </button>
        {expanded &&
          (events.length === 0 ? (
            <p className="empty">{emptyNote ?? 'no events on this symbol yet'}</p>
          ) : (
            <ul className="events-rows" ref={rowsRef}>
              {events.map((mark) => {
                const isSelected = mark.time === selected;
                return (
                  <li key={`${mark.time}-${mark.letter}`}>
                    <button
                      type="button"
                      className={`event-row${isSelected ? ' selected' : ''}`}
                      data-event-time={mark.time}
                      aria-pressed={isSelected}
                      onClick={() => onSelect(isSelected ? null : mark.time)}
                      title={`zoom to ${clock(mark.time * 1000)}`}
                    >
                      <EventGlyph
                        kind={mark.kind}
                        severity={mark.severity}
                        color={mark.color}
                        letter={mark.letter}
                        direction={mark.direction}
                        canon={canon}
                        size="sm"
                      />
                      <span className="event-time num">{clock(mark.time * 1000)}</span>
                      <KindBadge kind={mark.kind} color={mark.color} canon={canon} />
                      <SeverityBadge severity={mark.severity} canon={canon} />
                      <span className="event-score num">{mark.score === null ? '—' : num(mark.score, 0)}</span>
                      <span className="event-summary">{rowSummary(mark)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ))}
      </div>
    </section>
  );
});
