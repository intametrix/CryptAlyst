import { memo, useMemo, useState } from 'react';
import {
  clock,
  int,
  isoUtc,
  joinList,
  multiple,
  num,
  pct,
  px,
  share,
  text,
  toNum,
  tone,
  usd,
} from '../format';
import { useElementSize } from '../hooks/useElementSize';
import { useSelection } from '../selection';
import { SCREEN_COLUMNS, SCREEN_MAX_ROWS, visibleColumns, type Column, type SortKey } from '../columns';
import type { ScreenMsg, ScreenRow, ToolsMsg } from '../types';
import { KindBadge } from './Badges';
import { HeatBorder } from './HeatBorder';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

interface Props {
  screen: ScreenMsg | null;
  onRefresh: () => void;
  tools: ToolsMsg | null;
  /** Opens the trade drill-down for a symbol, seeded with this row. */
  onDrill?: (symbol: string, seed?: { screen: ScreenRow }) => void;
}

/** Extra documented fields, folded into the row tooltip rather than more columns. */
function rowDetail(row: ScreenRow): string {
  return [
    `24h ${pct(row.ret_24h)}`,
    `vol z ${num(row.vol_z, 1)}`,
    `range ${share(row.range_pct, 1)}`,
    `atr ${share(row.atr_pct, 2)}`,
    `from 24h high ${pct(row.from_24h_high)}`,
    `from 24h low ${pct(row.from_24h_low)}`,
    `bars since extreme ${int(row.bars_since_extreme)}`,
    `baseline vol ${usd(row.baseline_quote_volume)}`,
    `trades ratio ${multiple(row.trades_ratio)}`,
    joinList(row.reasons, ' | '),
  ].join(' · ');
}

export const ScreenTable = memo(function ScreenTable({ screen, onRefresh, tools, onDrill }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('score');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const { ref, size } = useElementSize<HTMLDivElement>();
  const selection = useSelection();

  const rows = useMemo(() => {
    const source = Array.isArray(screen?.rows) ? screen.rows : [];
    const dir = sortDir === 'asc' ? 1 : -1;
    const sorted = source.slice();
    sorted.sort((a, b) => {
      if (sortKey === 'symbol' || sortKey === 'kind') {
        return dir * text(a[sortKey]).localeCompare(text(b[sortKey]));
      }
      const av = toNum(a[sortKey]);
      const bv = toNum(b[sortKey]);
      if (av === null && bv === null) return 0;
      // Rows missing the sort field always sink to the bottom.
      if (av === null) return 1;
      if (bv === null) return -1;
      return dir * (av - bv);
    });
    return sorted;
  }, [screen, sortKey, sortDir]);

  const shown = rows.slice(0, SCREEN_MAX_ROWS);
  const columns = useMemo(() => visibleColumns(size.width), [size.width]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortKey(key);
    setSortDir(key === 'symbol' || key === 'kind' ? 'asc' : 'desc');
  };

  const headerFor = (c: Column) => {
    const active = sortKey === c.key;
    const arrow = active ? (sortDir === 'asc' ? '▲' : '▼') : '▲▼';
    return (
      <th
        key={c.key}
        scope="col"
        className={`${c.numeric ? 'num-col' : ''}${active ? ' sorted' : ''}`}
        aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        <Tooltip
          keys={c.toolKeys}
          tools={tools}
          fallbackTitle={c.label}
          label={`Sort by ${c.label}. Shown columns adapt to the panel width.`}
          tone="header"
        >
          <button type="button" className="th-btn" onClick={() => toggleSort(c.key)}>
            {c.label}
            <span className={`arrow${active ? ' arrow-active' : ''}`} aria-hidden="true">
              {arrow}
            </span>
          </button>
        </Tooltip>
      </th>
    );
  };

  const cellFor = (row: ScreenRow, c: Column, symbol: string, selected: boolean) => {
    switch (c.key) {
      case 'symbol':
        return (
          <td className="sym" key={c.key}>
            <HeatBorder symbol={symbol} tools={tools}>
              <button
                type="button"
                className={`sym-btn${selected ? ' selected' : ''}`}
                aria-pressed={selected}
                onClick={() => {
                  selection.selectSymbol(symbol);
                  onDrill?.(symbol, { screen: row });
                }}
                title={`${symbol} — click for the trade drill-down`}
              >
                {text(row.symbol)}
              </button>
            </HeatBorder>
          </td>
        );
      case 'kind':
        return (
          <td key={c.key}>
            <KindBadge kind={row.kind} />
          </td>
        );
      case 'score':
        return (
          <td key={c.key} className="num">
            {int(row.score)}
          </td>
        );
      case 'price':
        return (
          <td key={c.key} className="num">
            {px(row.price)}
          </td>
        );
      case 'ret_5m':
        return (
          <td key={c.key} className={`num ${tone(row.ret_5m)}`}>
            {pct(row.ret_5m)}
          </td>
        );
      case 'ret_15m':
        return (
          <td key={c.key} className={`num ${tone(row.ret_15m)}`}>
            {pct(row.ret_15m)}
          </td>
        );
      case 'ret_60m':
        return (
          <td key={c.key} className={`num ${tone(row.ret_60m)}`}>
            {pct(row.ret_60m)}
          </td>
        );
      case 'vol_ratio':
        return (
          <td key={c.key} className="num">
            {multiple(row.vol_ratio)}
          </td>
        );
      case 'taker_buy_ratio':
        return (
          <td key={c.key} className="num">
            {share(row.taker_buy_ratio, 1)}
          </td>
        );
      case 'close_position':
        return (
          <td key={c.key} className="num">
            {share(row.close_position, 0)}
          </td>
        );
      case 'recent_quote_volume':
        return (
          <td key={c.key} className="num">
            {usd(row.recent_quote_volume)}
          </td>
        );
      case 'quote_volume_24h':
        return (
          <td key={c.key} className="num">
            {usd(row.quote_volume_24h)}
          </td>
        );
      default:
        return <td key={c.key} />;
    }
  };

  return (
    <ResizablePanel id="screen.panel" axis="y" minHeight={180} maxHeight={1200}>
      {() => (
        <section className="panel screen">
          <div className="panel-head">
            <h2>pump / dump screen</h2>
            <div className="panel-meta">
              <span className="sub num" title={`screen.ts_ms ${isoUtc(screen?.ts_ms)}`}>
                {int(screen?.symbols_scanned)} scanned · {int(rows.length)} rows · {clock(screen?.ts_ms)}
              </span>
            </div>
            <div className="actions">
              <span className="cols-note" title="columns are shown progressively as the panel is widened">
                {int(columns.length)}/{int(SCREEN_COLUMNS.length)} cols
              </span>
              <button
                type="button"
                className="btn"
                onClick={onRefresh}
                title="POST /api/screen/refresh — the new screen arrives over SSE (202)"
              >
                refresh
              </button>
            </div>
          </div>

          {rows.length === 0 ? (
            <p className="empty">no screen yet — it runs every 60s (or press refresh)</p>
          ) : (
            <div className="table-wrap" ref={ref}>
              <table className="screen-table">
                <thead>
                  <tr>{columns.map((c) => headerFor(c))}</tr>
                </thead>
                <tbody>
                  {shown.map((row, i) => {
                    const symbol = typeof row.symbol === 'string' ? row.symbol : '';
                    const selected = selection.isSelected(symbol);
                    return (
                      <tr
                        key={symbol !== '' ? symbol : `row-${i}`}
                        className={`screen-row${selected ? ' selected' : ''}`}
                        title={`${text(row.symbol)} · ${rowDetail(row)}`}
                      >
                        {columns.map((c) => cellFor(row, c, symbol, selected))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {rows.length > shown.length && (
                <p className="table-note">
                  showing the first {int(shown.length)} of {int(rows.length)} rows
                </p>
              )}
            </div>
          )}
        </section>
      )}
    </ResizablePanel>
  );
});
