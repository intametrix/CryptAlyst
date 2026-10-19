/**
 * Column definitions for the tables, kept free of React.
 *
 * Every column carries the container width below which it is dropped, so a
 * narrowed panel progressively reveals columns instead of clipping them. Both
 * the tables and the runtime verification script read these, which is why they
 * live outside the components.
 */

/** Columns required by API.md, all sortable. */
export type SortKey =
  | 'symbol'
  | 'kind'
  | 'score'
  | 'price'
  | 'ret_5m'
  | 'ret_15m'
  | 'ret_60m'
  | 'vol_ratio'
  | 'taker_buy_ratio'
  | 'close_position'
  | 'recent_quote_volume'
  | 'quote_volume_24h';

export interface Column {
  key: SortKey;
  label: string;
  numeric: boolean;
  /** Glossary keys for the header tooltip. */
  toolKeys: string[];
  /**
   * Container width below which this column is dropped. `0` means it is always
   * shown. Higher-priority columns have lower thresholds.
   */
  minWidth: number;
}

export const SCREEN_COLUMNS: readonly Column[] = [
  { key: 'symbol', label: 'symbol', numeric: false, toolKeys: ['symbol'], minWidth: 0 },
  { key: 'score', label: 'score', numeric: true, toolKeys: ['score'], minWidth: 0 },
  { key: 'kind', label: 'kind', numeric: false, toolKeys: ['kind'], minWidth: 330 },
  { key: 'ret_5m', label: '5m', numeric: true, toolKeys: ['ret_5m'], minWidth: 400 },
  { key: 'price', label: 'price', numeric: true, toolKeys: ['price'], minWidth: 460 },
  { key: 'ret_15m', label: '15m', numeric: true, toolKeys: ['ret_15m'], minWidth: 540 },
  { key: 'vol_ratio', label: 'vol ratio', numeric: true, toolKeys: ['vol_ratio'], minWidth: 610 },
  { key: 'ret_60m', label: '1h', numeric: true, toolKeys: ['ret_60m'], minWidth: 700 },
  { key: 'taker_buy_ratio', label: 'taker %', numeric: true, toolKeys: ['taker_buy_ratio'], minWidth: 780 },
  { key: 'close_position', label: 'close pos', numeric: true, toolKeys: ['close_position'], minWidth: 860 },
  {
    key: 'recent_quote_volume',
    label: '5m vol',
    numeric: true,
    toolKeys: ['recent_quote_volume'],
    minWidth: 960,
  },
  { key: 'quote_volume_24h', label: '24h vol', numeric: true, toolKeys: ['quote_volume_24h'], minWidth: 1060 },
];

/** Defensive cap so a huge screen cannot lock up the browser. */
export const SCREEN_MAX_ROWS = 250;

/** One flow-leaderboard column. */
export interface FlowColumn {
  key: string;
  label: string;
  /** Glossary keys for the header tooltip. */
  toolKeys: string[];
  /** Below this container width the column is dropped. */
  minWidth: number;
  /** Relative share of the row width. */
  fr: number;
}

/**
 * Flow columns. `symbol` and `burst` (the bar) are never dropped; the rest are
 * revealed as the box widens.
 */
export const FLOW_COLUMNS: readonly FlowColumn[] = [
  { key: 'symbol', label: 'symbol', toolKeys: ['symbol'], minWidth: 0, fr: 1.1 },
  { key: 'burst', label: 'burst', toolKeys: ['burst_multiple'], minWidth: 0, fr: 1.2 },
  { key: 'flow', label: 'flow', toolKeys: ['flow_quote'], minWidth: 300, fr: 0.9 },
  { key: 'change', label: '10s', toolKeys: ['change_10s'], minWidth: 380, fr: 0.8 },
  { key: 'price', label: 'price', toolKeys: ['price'], minWidth: 460, fr: 0.9 },
  { key: 'z', label: 'z', toolKeys: ['burst_z'], minWidth: 540, fr: 0.6 },
  { key: 'taker', label: 'taker', toolKeys: ['taker_buy_ratio'], minWidth: 620, fr: 0.7 },
  { key: 'vol24', label: '24h vol', toolKeys: ['quote_volume_24h'], minWidth: 700, fr: 0.9 },
];

/** Screen columns that fit in `width` pixels. `0` means "not measured yet". */
export function visibleColumns(width: number): Column[] {
  if (width <= 0) return SCREEN_COLUMNS.filter((c) => c.minWidth === 0);
  return SCREEN_COLUMNS.filter((c) => width >= c.minWidth);
}

/** Flow columns that fit in `width` pixels. */
export function visibleFlowColumns(width: number): FlowColumn[] {
  if (width <= 0) return FLOW_COLUMNS.filter((c) => c.minWidth === 0);
  return FLOW_COLUMNS.filter((c) => width >= c.minWidth);
}
