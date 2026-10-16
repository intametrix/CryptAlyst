import { ChartIcon } from './ChartIcon';
import { memo, useMemo } from 'react';
import { clock, int, isoUtc, multiple, num, pct, px, share, toNum, tone, usd } from '../format';
import { visibleFlowColumns } from '../columns';
import { useElementSize } from '../hooks/useElementSize';
import { useSelection } from '../selection';
import type { FlowMsg, ToolsMsg } from '../types';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

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
export const FlowLeaderboard = memo(function FlowLeaderboard({ flow, tools }: Props) {
  const selection = useSelection();
  const { ref, size } = useElementSize<HTMLDivElement>();
  const rows = Array.isArray(flow?.rows) ? flow.rows.slice(0, MAX_ROWS) : [];

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
          </div>

          {rows.length === 0 ? (
            <p className="empty">no flow snapshot yet — it pushes every second once the stream is live</p>
          ) : (
            <div className="flow-rows" ref={ref}>
              <div className="flow-row flow-head" style={{ gridTemplateColumns: template }} role="row">
                {columns.map((c) => (
                  <Tooltip
                    key={c.key}
                    tone="header"
                    keys={c.toolKeys}
                    tools={tools}
                    fallbackTitle={c.label}
                    label="Click a row to chart that symbol."
                  >
                    <span className="col-head" tabIndex={0}>
                      {c.label}
                    </span>
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
                    className={`flow-row${selected ? ' selected' : ''}${t === 'up' ? ' up' : t === 'down' ? ' down' : ''}`}
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
                              onClick={() => {
                                // The symbol click opens the CHART and nothing
                                // else (human ruling, 2026-10-16): the drill-down
                                // is reachable from an icon inside the chart
                                // popup, not fired alongside it.
                                selection.selectSymbol(symbol);
                              }}
                              title={`${label} — click to chart`}
                            >
                              <ChartIcon />{' '}
                              {symbol !== '' ? symbol : '—'}
                            </button>
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
