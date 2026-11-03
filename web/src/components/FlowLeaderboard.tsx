import { ChartIcon } from './ChartIcon';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { clock, int, isoUtc, multiple, num, pct, px, share, toNum, tone, usd } from '../format';
import { visibleFlowColumns } from '../columns';
import { useElementSize } from '../hooks/useElementSize';
import { useSelection } from '../selection';
import type { FlowMsg, FlowRow, ToolsMsg } from '../types';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';
import { Freshness } from './Freshness';
import { typeLabel, typeOf, useInstrumentType, useInstrumentTypesReady } from '../hooks/useInstrumentTypes';

interface Props {
  flow: FlowMsg | null;
  tools: ToolsMsg | null;
}

/** API.md: the leaderboard carries "top ~25" rows. */
const MAX_ROWS = 25;

/**
 * Flow leaderboard — inline bar sized by `burst_multiple`, coloured by the sign
 * of `change_10s`.
 *
 * Every row's symbol is a button that charts it and stays highlighted while it
 * is the selection. The column set follows the measured container width, so a
 * narrowed box degrades gracefully instead of clipping.
 */

/**
 * The sort choice lives outside the component.
 *
 * The panel is re-created when a new flow snapshot arrives, and component state
 * went with it: a reader who sorted by name saw the list snap back to "burst"
 * within a second. Keeping the choice here means a live push cannot undo it.
 */
interface FlowSort {
  key: string;
  dir: 1 | -1;
}

const FLOW_SORT_KEY = 'cryptalyst.flow.sort';

function readFlowSort(): FlowSort {
  try {
    const raw = window.localStorage.getItem(FLOW_SORT_KEY);
    if (raw === null) return { key: 'burst', dir: -1 };
    const parsed = JSON.parse(raw) as { key?: string; dir?: number };
    return {
      key: typeof parsed.key === 'string' ? parsed.key : 'burst',
      dir: parsed.dir === 1 ? 1 : -1,
    };
  } catch {
    return { key: 'burst', dir: -1 };
  }
}

let flowSort: FlowSort = readFlowSort();
const flowSortListeners = new Set<() => void>();

function setFlowSort(next: FlowSort): void {
  flowSort = next;
  try {
    window.localStorage.setItem(FLOW_SORT_KEY, JSON.stringify(next));
  } catch {
    /* a private window still sorts, it just forgets */
  }
  for (const l of flowSortListeners) l();
}

function useFlowSort(): FlowSort {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((v) => v + 1);
    flowSortListeners.add(l);
    return () => {
      flowSortListeners.delete(l);
    };
  }, []);
  return flowSort;
}

/** PERP / SPOT, next to the symbol so a mixed list says what it is. */
const InstrumentChip = memo(function InstrumentChip({ symbol }: { symbol: string }) {
  const inst = useInstrumentType(symbol);
  const label = typeLabel(inst?.cls);
  if (label === '') return null;
  return (
    <span
      className={`inst-chip${label === 'PERP' ? ' is-perp' : ' is-spot'}`}
      title={inst?.name !== '' ? inst?.name : `${symbol} ${label.toLowerCase()}`}
    >
      {label}
    </span>
  );
});

export const FlowLeaderboard = memo(function FlowLeaderboard({ flow, tools }: Props) {
  const selection = useSelection();
  const { ref, size } = useElementSize<HTMLDivElement>();
  // 13: perps and spot behave differently (funding, liquidation, borrow), so the
  // list can be narrowed to one kind.
  const [kind, setKind] = useState<'all' | 'perp' | 'spot'>('all');
  const isReady = useInstrumentTypesReady();
  const kindOf = (sym: string): string => typeLabel(typeOf(sym));

  const all = Array.isArray(flow?.rows) ? flow.rows : [];
  const raw = (kind === 'all' ? all : all.filter((r) => kindOf(String(r.symbol ?? '')) === (kind === 'perp' ? 'PERP' : 'SPOT')))
    .slice(0, MAX_ROWS);

  // Sorting is per snapshot and local: the leaderboard is a live list, and a
  // reader who asked for "biggest move first" wants that order kept as the data
  // changes underneath, not reset by the next push.
  const sort = useFlowSort();
  const toggleSort = (key: string) =>
    setFlowSort(
      sort.key === key
        ? { key, dir: sort.dir === 1 ? -1 : 1 }
        : { key, dir: key === 'symbol' ? 1 : -1 },
    );

  // The list reorders once a second. While the pointer is over it, the last
  // snapshot is held: a row that moves under the cursor is a mis-click waiting to
  // happen.
  const [frozen, setFrozen] = useState<FlowRow[] | null>(null);

  const live = useMemo(() => {
    const value = (r: Record<string, unknown>): number | string => {
      switch (sort.key) {
        case 'symbol':
          return String(r.symbol ?? '');
        case 'type':
          return '';
        case 'change':
          return toNum(r.change_10s) ?? -Infinity;
        case 'flow':
          return toNum(r.flow_quote) ?? -Infinity;
        case 'price':
          return toNum(r.last_price) ?? -Infinity;
        case 'z':
          return toNum(r.burst_z) ?? -Infinity;
        case 'taker':
          return toNum(r.taker_buy_ratio) ?? -Infinity;
        case 'vol24':
          return toNum(r.quote_volume_24h) ?? -Infinity;
        default:
          return toNum(r.burst_multiple) ?? -Infinity;
      }
    };
    const copy = [...raw];
    copy.sort((a, b) => {
      const av = value(a as Record<string, unknown>);
      const bv = value(b as Record<string, unknown>);
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv)) * sort.dir;
      }
      return (av - bv) * sort.dir;
    });
    return copy;
  }, [raw, sort]);

  const rows = frozen ?? live;

  // One row is reachable by keyboard: arrows move, Enter selects, C charts.
  const [cursor, setCursor] = useState(-1);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);
  const onListKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (rows.length === 0) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor((prev) => {
        const next = e.key === 'ArrowDown' ? Math.min(prev + 1, rows.length - 1) : Math.max(prev - 1, 0);
        rowRefs.current[next]?.focus();
        return next;
      });
      return;
    }
    const row = rows[cursor];
    if (row === undefined) return;
    const sym = String(row.symbol ?? '');
    if (e.key === 'Enter') {
      e.preventDefault();
      selection.selectSymbol(sym);
    } else if (e.key === 'c' || e.key === 'C') {
      e.preventDefault();
      selection.openChart(sym);
    }
  };

  // Bars are scaled against the strongest multiple in this snapshot.
  let peak = 1;
  for (const r of rows) {
    const v = toNum(r.burst_multiple);
    if (v !== null && v > peak) peak = v;
  }

  const columns = useMemo(() => visibleFlowColumns(size.width), [size.width]);
  const template = columns.map((c) => `minmax(0, ${c.fr}fr)`).join(' ');

  return (
    <ResizablePanel id="flow.column" axis="x" minWidth={240} maxWidth={900} fillHeight>
      {() => (
        <section className="panel flow">
          <div className="panel-head">
            <h2>flow leaderboard</h2>
            <span className="sub num" title={`flow.ts_ms ${isoUtc(flow?.ts_ms)} · window ${int(flow?.window_sec)}s`}>
              {int(flow?.window_sec)}s · {clock(flow?.ts_ms)}
            </span>
            <Freshness atMs={flow?.ts_ms} label="flow" staleAfterS={8} warnAfterS={3} />
            <span className="flow-kind" role="group" aria-label="filter by instrument type">
              {(['all', 'perp', 'spot'] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  className={`flow-kind-btn${kind === k ? ' is-on' : ''}`}
                  aria-pressed={kind === k}
                  disabled={!isReady && k !== 'all'}
                  onClick={() => setKind(k)}
                  title={k === 'all' ? 'every instrument' : `${k === 'perp' ? 'perpetuals' : 'spot'} only`}
                >
                  {k.toUpperCase()}
                </button>
              ))}
            </span>
          </div>

          {rows.length === 0 ? (
            <p className="empty">no flow snapshot yet — it pushes every second once the stream is live</p>
          ) : (
            <div
              className={`flow-rows${frozen !== null ? ' is-frozen' : ''}`}
              ref={ref}
              onPointerEnter={() => setFrozen(live)}
              onPointerLeave={() => setFrozen(null)}
              onKeyDown={onListKey}
              tabIndex={0}
              role="grid"
              aria-label="flow leaderboard rows"
            >
              <div className="flow-row flow-head" style={{ gridTemplateColumns: template }} role="row">
                {columns.map((c) => (
                  <Tooltip
                    key={c.key}
                    tone="header"
                    keys={c.toolKeys}
                    tools={tools}
                    fallbackTitle={c.label}
                    label="Click to sort by this column. Click a row to chart that symbol."
                  >
                    <button
                      type="button"
                      className={`col-head col-sort${sort.key === c.key ? ' is-sorted' : ''}`}
                      onClick={() => toggleSort(c.key)}
                      aria-sort={sort.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
                      title={`sort by ${c.label}`}
                    >
                      {c.label}
                      <span className="col-sort-mark">{sort.key === c.key ? (sort.dir === 1 ? '▲' : '▼') : ''}</span>
                    </button>
                  </Tooltip>
                ))}
              </div>
              {rows.map((r, i) => {
                const symbol = typeof r.symbol === 'string' ? r.symbol : '';
                const m = toNum(r.burst_multiple) ?? 0;
                const width = Math.max(3, Math.min(100, (m / peak) * 100));
                const t = tone(r.change_10s);
                const label = symbol !== '' ? symbol : `row ${i + 1}`;
                const selected = selection.isSelected(symbol);
                return (
                  <div
                    key={symbol !== '' ? symbol : `row-${i}`}
                    ref={(el) => {
                      rowRefs.current[i] = el;
                    }}
                    className={`flow-row${selected ? ' selected' : ''}${cursor === i ? ' is-cursor' : ''}${t === 'up' ? ' up' : t === 'down' ? ' down' : ''}`}
                    style={{ gridTemplateColumns: template }}
                  >
                    {columns.map((c) => {
                      switch (c.key) {
                        case 'symbol':
                          return (
                            <button
                              key={c.key}
                              type="button"
                              className={`sym-btn${selected ? ' selected' : ''}`}
                              aria-pressed={selected}
                              disabled={symbol === ''}
                              onClick={() => selection.selectSymbol(symbol)}
                              title={`${label} — click to select`}
                            >
                              {symbol !== '' ? symbol : '—'}
                            </button>
                          );
                        case 'type':
                          return (
                            <span key={c.key} className="flow-type">
                              <InstrumentChip symbol={symbol} />
                              <button
                                type="button"
                                className="chart-open"
                                onClick={() => selection.openChart(symbol)}
                                disabled={symbol === ''}
                                title={`open the chart for ${label}`}
                                aria-label={`open chart for ${label}`}
                              >
                                <ChartIcon />
                              </button>
                            </span>
                          );
                        case 'burst':
                          return (
                            <span key={c.key} className="flow-bar" title={`${multiple(r.burst_multiple)} baseline`}>
                              <span className={`bar-fill ${t === '' ? 'flat' : t}`} style={{ width: `${width}%` }} />
                            </span>
                          );
                        case 'flow':
                          return (
                            <span key={c.key} className="flow-quote num" title={`flow ${usd(r.flow_quote)}`}>
                              {usd(r.flow_quote)}
                            </span>
                          );
                        case 'change':
                          return (
                            <span key={c.key} className={`flow-change num ${t}`} title={`10s move ${pct(r.change_10s)}`}>
                              {pct(r.change_10s)}
                            </span>
                          );
                        case 'price':
                          return (
                            <span key={c.key} className="flow-price num">
                              {px(r.last_price)}
                            </span>
                          );
                        case 'z':
                          return (
                            <span key={c.key} className="flow-price num" title={`z ${num(r.burst_z, 1)}`}>
                              {num(r.burst_z, 1)}
                            </span>
                          );
                        case 'taker':
                          return (
                            <span key={c.key} className="flow-price num">
                              {share(r.taker_buy_ratio, 0)}
                            </span>
                          );
                        case 'vol24':
                          return (
                            <span key={c.key} className="flow-price num">
                              {usd(r.quote_volume_24h)}
                            </span>
                          );
                        default:
                          return <span key={c.key} />;
                      }
                    })}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}
    </ResizablePanel>
  );
});
