import { useMemo } from 'react';
import { useHeat } from '../heatContext';
import { heatStyle } from '../heatContext';
import { num } from '../format';
import type { ToolsMsg } from '../types';
import { Tooltip } from './Tooltip';

/** Human names for the documented heat component keys. */
const COMPONENT_LABELS: Readonly<Record<string, string>> = {
  flow: 'flow',
  move: 'move',
  trend: 'trend',
  level: 'level',
  range: 'range',
};

export interface HeatBorderProps {
  symbol: string | null | undefined;
  tools?: ToolsMsg | null;
  /** Extra classes on the wrapper. */
  className?: string;
  /** Element to render; defaults to a `span` so it can live inside a table cell. */
  as?: 'span' | 'div';
  /** Rendered inside the glow. */
  children: React.ReactNode;
}

/**
 * Wrap anything that represents a ticker so it glows when that ticker is hot.
 *
 * - `heat < 25` renders **no** border at all
 * - `heat >= 25` sets `--heat` (25 → 0, 100 → 1) and CSS interpolates the light
 *   green border and glow from it
 * - the glow uses `box-shadow` plus a border, so nothing reflows when it appears
 * - it subscribes to **this symbol only**, so a universe-wide heat tick does not
 *   re-render the surrounding list
 * - hovering or focusing it explains the glow: heat value, the reason strings,
 *   and the component breakdown, each falling back to `/api/tools` copy
 */
export function HeatBorder({ symbol, tools = null, className, as = 'span', children }: HeatBorderProps) {
  const entry = useHeat(symbol);
  const style = useMemo(() => heatStyle(entry?.heat), [entry?.heat]);
  const Tag = as;
  const name = typeof symbol === 'string' ? symbol : '';
  const hot = entry !== null;

  return (
    <Tooltip
      keys={['heat']}
      tools={tools}
      fallbackTitle={name === '' ? 'Heat' : `Heat — ${name}`}
      label="Composite of flow, price move, trend, level and range. Brighter border = more interesting right now. Below 25 nothing is drawn."
      value={hot ? `${num(entry.heat, 1)} / 100` : null}
      reasons={hot ? entry.reasons : null}
      breakdown={hot ? entry.components : null}
      breakdownLabels={COMPONENT_LABELS}
      tone="heat"
      className="heat-wrap"
    >
      <Tag className={`heat-border${className !== undefined ? ` ${className}` : ''}`} style={style}>
        {children}
      </Tag>
    </Tooltip>
  );
}

/**
 * A small always-visible explanation of what the glow means. Rendered once in
 * the header so the affordance exists without any heat data.
 */
export function HeatLegend({ activeCount, tools }: { activeCount: number; tools: ToolsMsg | null }) {
  return (
    <Tooltip
      keys={['heat']}
      tools={tools}
      fallbackTitle="Heat"
      label="Every ticker glows light green when something about it is interesting — a flow burst, a fast move, a trend, a level test or a range break. The brighter the border, the stronger the signal; below 25 nothing is drawn, and a border fades out rather than snapping off."
      value={`${num(activeCount, 0)} lit`}
      tone="heat"
      className="heat-legend-wrap"
    >
      <span className="heat-legend" tabIndex={0}>
        <span className="heat-legend-swatch" aria-hidden="true" />
        heat
      </span>
    </Tooltip>
  );
}
