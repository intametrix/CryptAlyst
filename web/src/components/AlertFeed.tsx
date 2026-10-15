import { memo, useEffect, useMemo, useState } from 'react';
import { activeFilters, clearFilter, hasFilters, matchesFilter, type FeedFilter, type FilterKey } from '../filters';
import { int } from '../format';
import { useSelection } from '../selection';
import type { AlertMsg, ToolsMsg } from '../types';
import { alertKey, prettifyEnum } from '../types';
import { AlertCard } from './AlertCard';
import { ResizablePanel } from './Panel';

interface Props {
  /** Newest first, already capped by the hook. Frozen while `paused`. */
  alerts: AlertMsg[];
  flashIds: ReadonlySet<string>;
  paused: boolean;
  pending: number;
  onTogglePause: () => void;
  onClear: () => void;
  maxAlerts: number;
  tools: ToolsMsg | null;
  /** The shared filter, driven by clicking badges anywhere in the app. */
  filter: FeedFilter;
  onFilterChange: (next: FeedFilter) => void;
  /** A badge was clicked: narrow the feed to that value. */
  onFilterToggle: (key: FilterKey, value: string) => void;
  /** A metric row or badge was activated: drill into that symbol. */
  onDrill: (symbol: string, seed?: { alerts: AlertMsg[] }) => void;
}

export const AlertFeed = memo(function AlertFeed({
  alerts,
  flashIds,
  paused,
  pending,
  onTogglePause,
  onClear,
  maxAlerts,
  tools,
  filter,
  onFilterChange,
  onFilterToggle,
  onDrill,
}: Props) {
  const selection = useSelection();
  const [onlySelected, setOnlySelected] = useState(false);
  // One clock for the whole feed: cards are memoised on it, so a tick re-renders
  // the feed's cards but not the surrounding panels, and unmounted cost is zero.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const visible = useMemo(() => {
    const byBadges = alerts.filter((a) => matchesFilter(a, filter));
    if (!onlySelected || selection.symbol === null) return byBadges;
    return byBadges.filter((a) => selection.isSelected(a.symbol));
  }, [alerts, onlySelected, selection, filter]);

  const chips = activeFilters(filter);

  const emptyText = useMemo(() => {
    if (alerts.length === 0) return 'waiting for the first alert…';
    if (onlySelected && selection.symbol !== null) return `no buffer alerts for ${selection.symbol}`;
    return 'no alerts in the buffer';
  }, [alerts.length, onlySelected, selection.symbol]);

  return (
    <ResizablePanel id="alerts.column" axis="x" minWidth={260} maxWidth={1100} fillHeight>
      {() => (
        <section className="panel alert-feed">
          <div className="panel-head">
            <h2>live alerts</h2>
            <span
              className="count num"
              title={`showing ${visible.length} · ${alerts.length} held in memory (cap ${maxAlerts}, newest first)`}
            >
              {int(visible.length)}
              {onlySelected ? ` / ${int(alerts.length)}` : ''}
            </span>
            <div className="actions">
              <button
                type="button"
                className={`chip${onlySelected ? ' on' : ''}`}
                aria-pressed={onlySelected}
                onClick={() => setOnlySelected((v) => !v)}
                title="show only alerts for the charted symbol"
              >
                {selection.symbol ?? 'no symbol'} only
              </button>
              <button
                type="button"
                className="btn"
                onClick={onClear}
                title="DELETE /api/alerts — clears the backend buffer and this feed"
              >
                clear
              </button>
              <button
                type="button"
                className={`btn${paused ? ' btn-warn' : ''}`}
                onClick={onTogglePause}
                aria-pressed={paused}
                title={
                  paused
                    ? 'resume — insert everything buffered while paused'
                    : 'stop inserting new alerts so you can read one'
                }
              >
                {paused ? '▶ resume' : '⏸ pause'}
              </button>
            </div>
          </div>

          {paused && (
            <button type="button" className="pending" onClick={onTogglePause}>
              paused · <strong className="num">{int(pending)}</strong> new alert{pending === 1 ? '' : 's'} waiting —
              click to resume
            </button>
          )}

          {hasFilters(filter) && (
            <div className="filter-bar" role="group" aria-label="alert filters">
              <span className="filter-bar-label">filtered by</span>
              {chips.map((chip) => (
                <span key={chip.key} className="filter-chip">
                  <span className="filter-chip-key">{chip.label}</span>
                  <span className="filter-chip-value">{prettifyEnum(chip.value)}</span>
                  <button
                    type="button"
                    className="filter-chip-x"
                    aria-label={`clear the ${chip.label} filter`}
                    onClick={() => onFilterChange(clearFilter(filter, chip.key))}
                  >
                    ×
                  </button>
                </span>
              ))}
              <button type="button" className="link-btn" onClick={() => onFilterChange({ severity: null, detector: null, kind: null })}>
                clear all
              </button>
            </div>
          )}

          <div className="alert-list">
            {visible.length === 0 ? (
              <p className="empty">{emptyText}</p>
            ) : (
              visible.map((a) => {
                const key = alertKey(a);
                return (
                  <AlertCard
                    key={key}
                    alert={a}
                    flash={flashIds.has(key)}
                    tools={tools}
                    nowMs={nowMs}
                    onFilterToggle={onFilterToggle}
                    onDrill={onDrill}
                  />
                );
              })
            )}
          </div>
        </section>
      )}
    </ResizablePanel>
  );
});
