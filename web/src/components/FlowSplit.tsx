import { memo, useEffect, useMemo, useState } from 'react';
import { multiple, num, pct, px, text, toNum, tone, usd } from '../format';
import { useSelection } from '../selection';
import type { FlowMsg, FlowSplitMsg, FlowSplitRow, ToolsMsg } from '../types';
import { EventGlyph } from './EventGlyph';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

/** One side of the split. */
interface SideProps {
  title: string;
  rows: readonly FlowSplitRow[];
  /** `PUMP` / `DUMP`, for the glyph and the empty-state wording. */
  kind: 'PUMP' | 'DUMP';
  /** The sentence shown when the list is empty, built from the drop counters. */
  emptyText: string;
  onDrill: (symbol: string, seed?: { flow: FlowSplitRow }) => void;
  tools: ToolsMsg | null;
}

/**
 * The empty state is the point.
 *
 * A quiet market is not a bug, and an empty box reads like one. The backend
 * publishes exactly why rows were dropped (`dropped_thin`, `dropped_stale`,
 * `dropped_quiet`), so the panel states the fact instead of showing nothing:
 * "nothing moved >= 0.10% in the last 10 seconds" is information, a blank box is
 * a support ticket.
 */
export function emptyReason(split: FlowSplitMsg | null): string {
  const thin = toNum(split?.dropped_thin) ?? 0;
  const stale = toNum(split?.dropped_stale) ?? 0;
  const quiet = toNum(split?.dropped_quiet) ?? 0;
  const move = toNum(split?.min_move_pct);
  const flow = toNum(split?.min_flow_usd);
  const window = toNum(split?.window_sec);

  const gates: string[] = [];
  // `min_move_pct` is already a percentage (0.1 means 0.10%), not a fraction.
  if (move !== null) gates.push(`moved at least ${num(move, 2)}%`);
  if (flow !== null) gates.push(`traded at least ${usd(flow)}`);

  const gateText = gates.length === 0 ? 'cleared the thresholds' : gates.join(' and ');
  const windowText = window === null ? 'the last window' : `the last ${text(window)}s`;
  const dropped = thin + stale + quiet;

  if (dropped === 0 && split?.symbols != null) {
    return `all ${text(split.symbols)} tracked symbols were assessed; none ${gateText} in ${windowText}`;
  }
  const parts: string[] = [];
  if (thin > 0) parts.push(`${thin} too thin`);
  if (stale > 0) parts.push(`${stale} stale`);
  if (quiet > 0) parts.push(`${quiet} below the move threshold`);
  return `nothing ${gateText} in ${windowText} — ${parts.join(', ')} of ${text(split?.symbols)} symbols dropped`;
}

function Side({ title, rows, kind, emptyText, onDrill, tools }: SideProps) {
  // `change_24h_pct` sits beside the 10s move on purpose: a bounce inside a
  // downtrend must not read as a pump, and the daily figure is what says so.
  return (
    <section className={`flow-split-side flow-split-${kind.toLowerCase()}`}>
      <h3 className="flow-split-title">
        <EventGlyph kind={kind} canon={null} arrow size="sm" />
        {title}
        <span className="num">{text(rows.length)}</span>
      </h3>
      {rows.length === 0 ? <p className="flow-split-empty">{emptyText}</p> : (
        <ul className="flow-split-rows">
          {rows.map((row) => {
            const symbol = typeof row.symbol === 'string' ? row.symbol : '';
            const change10 = toNum(row.change_10s);
            const change24 = toNum(row.change_24h_pct);
            return (
              <li key={symbol === '' ? JSON.stringify(row).slice(0, 24) : symbol}>
                <button
                  type="button"
                  className="flow-split-row"
                  onClick={() => onDrill(symbol, { flow: row })}
                  title={`${symbol} — click for the trade drill-down`}
                >
                  <span className={`flow-split-sym sym-btn${''}`}>{symbol === '' ? '—' : symbol}</span>
                  <span className="flow-split-price num">{px(row.last_price)}</span>
                  <span className={`flow-split-change num ${tone(change10)}`}>{pct(change10)}</span>
                  <Tooltip
                    keys={['ret_24h']}
                    tools={tools}
                    fallbackTitle="24h change"
                    label="The daily move, shown beside the 10-second one so a bounce inside a downtrend is not read as a pump."
                    value={pct(change24)}
                    tone="stat"
                  >
                    <span className={`flow-split-daily num ${tone(change24)}`} tabIndex={0}>
                      {pct(change24)}
                    </span>
                  </Tooltip>
                  <span className="flow-split-quote num">{usd(row.flow_quote)}</span>
                  <span className="flow-split-mult num">{multiple(row.burst_multiple)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export interface FlowSplitProps {
  /** The latest `flow` message, which is `split`-shaped when the server sends it. */
  flow: FlowMsg | FlowSplitMsg | null;
  tools: ToolsMsg | null;
  onDrill: (symbol: string, seed?: { flow: FlowSplitRow }) => void;
}

/**
 * The two-panel flow split: the biggest pumps and the biggest dumps in the
 * current window.
 *
 * Fed by the `flow` SSE message, which carries `pumps`/`dumps` when it is
 * `split`-shaped, and by `GET /api/flow?split=1` as a seed — the same set either
 * way. Rows are clickable through to the trade drill-down, like every other
 * symbol in the dashboard.
 */
export const FlowSplit = memo(function FlowSplit({ flow, tools, onDrill }: FlowSplitProps) {
  const selection = useSelection();
  /**
   * A REST seed, because the SSE `flow` message only carries the split once the
   * server has produced one.
   *
   * Without this the panels render from a message that may never arrive in a
   * split shape, which looks identical to the feature not existing — the exact
   * failure this component was built to fix.
   */
  const [seed, setSeed] = useState<FlowSplitMsg | null>(null);
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const res = await fetch('/api/flow?split=1&limit=25', {
          headers: { accept: 'application/json' },
          signal: controller.signal,
        });
        if (!res.ok) return;
        if (!(res.headers.get('content-type') ?? '').toLowerCase().includes('json')) return;
        const body: unknown = await res.json();
        if (!cancelled && typeof body === 'object' && body !== null) setSeed(body as FlowSplitMsg);
      } catch {
        // The stream is the primary source; a failed seed just leaves the panels
        // on their (explanatory) empty state.
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  // The live stream wins when it carries the split; otherwise the seed stands in.
  const stream = flow as FlowSplitMsg | null;
  const streamHasSplit = Array.isArray(stream?.pumps) || Array.isArray(stream?.dumps) || stream?.split === true;
  const split = streamHasSplit ? stream : (seed ?? stream);
  const pumps = useMemo(() => (Array.isArray(split?.pumps) ? split.pumps : []), [split]);
  const dumps = useMemo(() => (Array.isArray(split?.dumps) ? split.dumps : []), [split]);

  const emptyText = useMemo(() => emptyReason(split), [split]);

  const hasCounts =
    split?.dropped_thin != null || split?.dropped_stale != null || split?.dropped_quiet != null || split?.symbols != null;

  return (
    <ResizablePanel id="flowsplit.panel" axis="y" minHeight={160} maxHeight={900}>
      {() => (
        <section className="panel flow-split">
          <div className="panel-head">
            <h2>flow split · 10s</h2>
            <div className="panel-meta">
              <span className="sub num">
                {hasCounts
                  ? `scanned ${text(split?.symbols)} · dropped ${text(split?.dropped_thin)} thin / ${text(split?.dropped_stale)} stale / ${text(split?.dropped_quiet)} quiet`
                  : 'waiting for the first split…'}
              </span>
            </div>
            <div className="actions">
              <Tooltip
                keys={['flow_split']}
                tools={tools}
                fallbackTitle="Flow split"
                label="The strongest upward and downward bursts in the current window. Rows carry the 24-hour change beside the 10-second move, so a bounce inside a downtrend is not misread as a pump. The drop counters explain an empty panel."
              >
                <span className="sub" tabIndex={0}>
                  {selection.symbol ?? 'no symbol'}
                </span>
              </Tooltip>
            </div>
          </div>
          <div className="flow-split-body">
            <Side
              title="biggest pumps"
              rows={pumps}
              kind="PUMP"
              emptyText={emptyText}
              onDrill={onDrill}
              tools={tools}
            />
            <Side
              title="biggest dumps"
              rows={dumps}
              kind="DUMP"
              emptyText={emptyText}
              onDrill={onDrill}
              tools={tools}
            />
          </div>
        </section>
      )}
    </ResizablePanel>
  );
});
