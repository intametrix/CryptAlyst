import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { INTERVALS, isInterval, type Interval } from './types';

/* --------------------------------------------------------------- url state */

/**
 * The drillable state that is reflected in the URL, so a link can be shared and
 * a reload lands on the same view.
 *
 * Shape: `#symbol=ENAUSDT&interval=15m&view=trade&sev=CRITICAL&det=turtle-breakout&kind=PUMP`
 * Everything is optional; an empty hash means "no drill-down".
 */
export interface UrlState {
  symbol: string | null;
  interval: Interval | null;
  /** Which step of the drill-down the user is on. */
  view: string | null;
  severity: string | null;
  detector: string | null;
  kind: string | null;
}

export function readUrlState(): UrlState {
  const empty: UrlState = { symbol: null, interval: null, view: null, severity: null, detector: null, kind: null };
  if (typeof window === 'undefined') return empty;
  try {
    const raw = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
    if (raw === '') return empty;
    const params = new URLSearchParams(raw);
    const interval = params.get('interval');
    return {
      symbol: normSymbol(params.get('symbol')) || null,
      interval: isInterval(interval) ? interval : null,
      view: params.get('view'),
      severity: params.get('sev'),
      detector: params.get('det'),
      kind: params.get('kind'),
    };
  } catch {
    return empty;
  }
}

/** Write the drillable state back to the hash, dropping empty values. */
export function writeUrlState(state: Partial<UrlState>): void {
  if (typeof window === 'undefined') return;
  try {
    const params = new URLSearchParams();
    const set = (key: string, value: string | null | undefined): void => {
      if (typeof value === 'string' && value.trim() !== '') params.set(key, value.trim());
    };
    set('symbol', state.symbol);
    set('interval', state.interval);
    set('view', state.view);
    set('sev', state.severity);
    set('det', state.detector);
    set('kind', state.kind);
    const next = params.toString();
    const target = next === '' ? window.location.pathname + window.location.search : `#${next}`;
    // `replaceState` keeps the back button meaningful rather than filling it with
    // one entry per click.
    window.history.replaceState(null, '', target);
  } catch {
    // A blocked history call must never break the UI.
  }
}

/** The dashboard's single shared selection. */
export interface Selection {
  /** Symbol whose chart is showing, or `null` before anything is picked. */
  symbol: string | null;
  interval: Interval;
  /** Select a symbol. Re-selecting the current one is a no-op, never a toggle. */
  selectSymbol: (symbol: string) => void;
  selectInterval: (interval: Interval) => void;
  /** Case/whitespace-insensitive comparison against the current selection. */
  isSelected: (symbol: string | null | undefined) => boolean;
  /** Drop the selection entirely. */
  clearSymbol: () => void;
}

const FALLBACK: Selection = {
  symbol: null,
  interval: '1h',
  selectSymbol: () => undefined,
  selectInterval: () => undefined,
  isSelected: () => false,
  clearSymbol: () => undefined,
};

const SelectionContext = createContext<Selection>(FALLBACK);

const INTERVAL_KEY = 'pumpdump.interval.v1';

function readInterval(initial: Interval): Interval {
  try {
    const raw = globalThis.localStorage?.getItem(INTERVAL_KEY) ?? null;
    return isInterval(raw) ? raw : initial;
  } catch {
    return initial;
  }
}

/** Normalise a symbol for comparison: trimmed, upper-cased. */
export function normSymbol(symbol: string | null | undefined): string {
  return typeof symbol === 'string' ? symbol.trim().toUpperCase() : '';
}

/**
 * Owns `{ symbol, interval }` for the whole app.
 *
 * Every symbol in the dashboard (alert feed, flow leaderboard, screen table,
 * survey) selects through here, so clicking a name anywhere loads that chart.
 * The selection is deliberately *sticky*: clicking the already-selected symbol
 * does not clear it.
 */
export function SelectionProvider({
  children,
  seedSymbol = null,
  initialInterval = '1h',
}: {
  children: ReactNode;
  /**
   * Adopted once, and only if the user has not picked anything yet — so the
   * chart is never blank on a cold start, and a click is never overridden by
   * late-arriving screen data.
   */
  seedSymbol?: string | null;
  initialInterval?: Interval;
}) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [interval, setIntervalState] = useState<Interval>(() => readInterval(initialInterval));

  // Adopt the seed exactly once, and never override a user's choice.
  const adopted = useRef(false);
  useEffect(() => {
    if (adopted.current) return;
    const clean = normSymbol(seedSymbol);
    if (clean === '') return;
    adopted.current = true;
    setSymbol((prev) => (prev === null ? clean : prev));
  }, [seedSymbol]);

  const selectSymbol = useCallback((next: string) => {
    const clean = normSymbol(next);
    if (clean === '') return;
    setSymbol(clean);
  }, []);

  const clearSymbol = useCallback(() => setSymbol(null), []);

  const selectInterval = useCallback((next: Interval) => {
    if (!INTERVALS.includes(next)) return;
    setIntervalState(next);
    try {
      globalThis.localStorage?.setItem(INTERVAL_KEY, next);
    } catch {
      // Persisting the interval is a nicety; a failure must not break the pick.
    }
  }, []);

  const isSelected = useCallback((candidate: string | null | undefined) => {
    const clean = normSymbol(candidate);
    return clean !== '' && clean === normSymbol(symbol);
  }, [symbol]);

  const value = useMemo<Selection>(
    () => ({ symbol, interval, selectSymbol, selectInterval, isSelected, clearSymbol }),
    [symbol, interval, selectSymbol, selectInterval, isSelected, clearSymbol],
  );

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

/** Read the shared selection. Safe outside a provider (returns inert defaults). */
export function useSelection(): Selection {
  return useContext(SelectionContext);
}
