import { memo } from 'react';
import { int, usd } from '../format';
import { useElementSize } from '../hooks/useElementSize';
import type { StatusMsg, ToolsMsg } from '../types';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

interface Props {
  stats: {
    total: number;
    pumps: number;
    dumps: number;
    reversals: number;
    critical: number;
  };
  status: StatusMsg | null;
  tools: ToolsMsg | null;
}

interface Cell {
  key: string;
  label: string;
  value: string;
  tone: 'up' | 'down' | 'rev' | 'crit' | 'plain';
  /** Glossary keys for the tooltip. */
  toolKeys: string[];
  /** Extra context shown in the tooltip body. */
  note: string;
}

function Stat({
  cell,
  tools,
}: {
  cell: Cell;
  tools: ToolsMsg | null;
}) {
  const { ref, size } = useElementSize<HTMLDivElement>();
  const stacked = size.width > 0 && size.width < 130;
  return (
    <Tooltip
      keys={cell.toolKeys}
      tools={tools}
      fallbackTitle={cell.label}
      label={cell.note}
      value={cell.value}
      tone="stat"
    >
      <div
        ref={ref}
        className={`stat stat-${cell.tone}${stacked ? ' stacked' : ''}`}
        tabIndex={0}
        role="group"
        aria-label={`${cell.label}: ${cell.value}`}
      >
        <span className="stat-label">{cell.label}</span>
        <span className="stat-value num">{cell.value}</span>
      </div>
    </Tooltip>
  );
}

/**
 * The stat strip.
 *
 * Each card is its own resizable box (so a user can grow the one they care
 * about), measures itself, and reflows from a row to a stacked label/value pair
 * when its own box gets narrow — not when the viewport does.
 */
export const StatStrip = memo(function StatStrip({ stats, status, tools }: Props) {
  const cells: Cell[] = [
    {
      key: 'pumps',
      label: 'pumps',
      value: int(stats.pumps),
      tone: 'up',
      toolKeys: ['pumps', 'kind'],
      note: `PUMP alerts in the ${int(stats.total)} alerts held in memory`,
    },
    {
      key: 'dumps',
      label: 'dumps',
      value: int(stats.dumps),
      tone: 'down',
      toolKeys: ['dumps', 'kind'],
      note: `DUMP alerts in the ${int(stats.total)} alerts held in memory`,
    },
    {
      key: 'reversals',
      label: 'reversals',
      value: int(stats.reversals),
      tone: 'rev',
      toolKeys: ['reversals'],
      note: 'REVERSAL_PUMP + REVERSAL_DUMP in the in-memory buffer',
    },
    {
      key: 'critical',
      label: 'critical',
      value: int(stats.critical),
      tone: 'crit',
      toolKeys: ['critical', 'severity'],
      note: 'alerts with severity CRITICAL in the in-memory buffer',
    },
    {
      key: 'volume',
      label: 'universe 24h vol',
      value: usd(status?.universe_volume_24h),
      tone: 'plain',
      toolKeys: ['universe_volume_24h'],
      note: 'Summed 24h quote volume across the tracked universe',
    },
    {
      key: 'scans',
      label: 'scan passes',
      value: int(status?.scans),
      tone: 'plain',
      toolKeys: ['scans'],
      note: `status.scans · candidates ${int(status?.candidates)} · messages ${int(status?.messages)}`,
    },
  ];

  return (
    <section className="stat-strip" aria-label="alert tallies">
      {cells.map((cell) => (
        <ResizablePanel
          key={cell.key}
          id={`stat.${cell.key}`}
          axis="x"
          minWidth={96}
          maxWidth={520}
          minHeight={0}
          className="stat-box"
        >
          {() => <Stat cell={cell} tools={tools} />}
        </ResizablePanel>
      ))}
    </section>
  );
});
