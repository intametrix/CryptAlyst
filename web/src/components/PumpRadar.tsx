import { ChartIcon } from './ChartIcon';
import { memo, useMemo } from 'react';
import { clock, int, isoUtc, multiple, pct, px, toNum, tone, usd } from '../format';
import { useElementSize } from '../hooks/useElementSize';
import { useSelection } from '../selection';
import type { FlowMsg } from '../types';
import { ResizablePanel } from './Panel';

interface Props {
  flow: FlowMsg | null;
}

/**
 * Flow radar — every watched symbol as one cell in a heat grid, so the whole
 * burst landscape reads at a glance instead of as a sorted list. Cells are
 * toned by the 10s move direction and filled by burst multiple (scaled to the
 * hottest cell in this snapshot). Quiet markets show a dim, mostly-flat grid;
 * a forming burst lights one cell before it crosses any alert tier — the
 * at-a-glance answer to "is anything happening?".
 */
export const PumpRadar = memo(function PumpRadar({ flow }: Props) {
  const selection = useSelection();
  const { ref, size } = useElementSize<HTMLDivElement>();
  const rows = Array.isArray(flow?.rows) ? flow.rows.slice(0, 25) : [];

  let peak = 1;
  for (const r of rows) {
    const v = toNum(r.burst_multiple);
    if (v !== null && v > peak) peak = v;
  }

  // Cell size follows the measured container: ~5 columns on a half-width
  // panel, more on a full dashboard row.
  const cols = useMemo(() => Math.max(3, Math.min(8, Math.round(size.width / 150))), [size.width]);

  return (
    <ResizablePanel id="pd.radar" axis="x" minWidth={260} maxWidth={1400} fillHeight>
      {() => (
        <section className="panel radar">
          <div className="panel-head">
            <h2>pump / dump radar</h2>
            <span className="sub num" title={`flow.ts_ms ${isoUtc(flow?.ts_ms)} · window ${int(flow?.window_sec)}s`}>
              {int(flow?.window_sec)}s · {clock(flow?.ts_ms)} · top {rows.length}
            </span>
          </div>
          {rows.length === 0 ? (
            <p className="empty">no flow snapshot yet — it pushes every second once the stream is live</p>
          ) : (
            <div className="radar-grid" ref={ref} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
              {rows.map((r, i) => {
                const symbol = typeof r.symbol === 'string' ? r.symbol : '';
                const m = toNum(r.burst_multiple) ?? 0;
                const width = Math.max(3, Math.min(100, (m / peak) * 100));
                const t = tone(r.change_10s);
                const label = symbol !== '' ? symbol : `cell ${i + 1}`;
                const selected = selection.isSelected(symbol);
                return (
                  <button
                    key={symbol !== '' ? symbol : `cell-${i}`}
                    type="button"
                    className={`radar-cell${selected ? ' selected' : ''}${t === 'up' ? ' up' : t === 'down' ? ' down' : ''}`}
                    disabled={symbol === ''}
                    aria-pressed={selected}
                    onClick={() => selection.selectSymbol(symbol)}
                    title={`${label} — ${multiple(r.burst_multiple)} baseline, move ${pct(r.change_10s)}, flow ${usd(r.flow_quote)} — click to chart`}
                  >
                    <span className="radar-sym">
                      <ChartIcon /> {symbol !== '' ? symbol : '—'}
                    </span>
                    <span className={`radar-bar ${t === '' ? 'flat' : t}`}>
                      <span className="bar-fill" style={{ width: `${width}%` }} />
                    </span>
                    <span className="radar-meta num">
                      {multiple(r.burst_multiple)} · {pct(r.change_10s)} · {px(r.last_price)}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          <p className="radar-note">
            watch-tier geometry: each symbol vs its own baseline; a cell lighting up is the earliest
            visible sign of a burst forming, below any alert threshold.
          </p>
        </section>
      )}
    </ResizablePanel>
  );
});
