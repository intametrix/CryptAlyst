/**
 * The alert-feed filter, as a set of small removable chips.
 *
 * Every badge in the dashboard is a navigation target: clicking a severity, a
 * detector, or a kind narrows the feed to it. The filter is deliberately a value
 * object rather than scattered booleans, so it can be serialised into the URL
 * and rendered as a single chip bar.
 */

export interface FeedFilter {
  severity: string | null;
  detector: string | null;
  kind: string | null;
}

export const EMPTY_FILTER: FeedFilter = { severity: null, detector: null, kind: null };

/** The filter dimensions, in the order their chips are rendered. */
export const FILTER_KEYS = ['severity', 'detector', 'kind'] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

const LABELS: Record<FilterKey, string> = {
  severity: 'severity',
  detector: 'detector',
  kind: 'kind',
};

export interface ActiveFilter {
  key: FilterKey;
  label: string;
  value: string;
}

/** The chips to show, in a stable order. */
export function activeFilters(filter: FeedFilter | null | undefined): ActiveFilter[] {
  const out: ActiveFilter[] = [];
  if (filter === null || filter === undefined) return out;
  for (const key of FILTER_KEYS) {
    const value = filter[key];
    if (typeof value === 'string' && value !== '') out.push({ key, label: LABELS[key], value });
  }
  return out;
}

export function hasFilters(filter: FeedFilter | null | undefined): boolean {
  if (filter === null || filter === undefined) return false;
  return FILTER_KEYS.some((key) => filter[key] !== null);
}

/**
 * Toggle one dimension.
 *
 * Clicking the *same* value again clears it (the chip is a toggle), and clicking
 * a different value replaces it, so a badge click never traps the user in a
 * filter they cannot undo.
 */
export function toggleFilter(filter: FeedFilter | null | undefined, key: FilterKey, value: string): FeedFilter {
  const base = filter ?? EMPTY_FILTER;
  const clean = value.trim();
  if (clean === '') return base;
  return { ...base, [key]: base[key] === clean ? null : clean };
}

export function clearFilter(filter: FeedFilter | null | undefined, key: FilterKey): FeedFilter {
  return { ...(filter ?? EMPTY_FILTER), [key]: null };
}

/** Does one alert survive the filter? Case-insensitive on every dimension. */
export function matchesFilter(
  alert: { severity?: unknown; detector?: unknown; kind?: unknown } | null | undefined,
  filter: FeedFilter | null | undefined,
): boolean {
  // A missing filter means "show everything"; a missing alert is dropped.
  if (alert === null || alert === undefined) return false;
  if (filter === null || filter === undefined) return true;
  const same = (a: unknown, b: string | null): boolean =>
    b === null || (typeof a === 'string' && a.toUpperCase() === b.toUpperCase());
  return same(alert.severity, filter.severity) && same(alert.detector, filter.detector) && same(alert.kind, filter.kind);
}
