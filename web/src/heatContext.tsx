import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import {
  HEAT_TICK_MS,
  createHeatStore,
  heatKey,
  type HeatEntry,
  type HeatIngest,
  type HeatStore,
} from './heatStore';

export {
  HEAT_MIN,
  HEAT_TICK_MS,
  createHeatStore,
  heatKey,
  heatStrength,
  heatStyle,
  type HeatEntry,
  type HeatIngest,
  type HeatStore,
} from './heatStore';

interface HeatContextValue {
  store: HeatStore;
  ingest: HeatIngest;
}

const HeatContext = createContext<HeatContextValue | null>(null);

/**
 * Owns the heat store, seeds it from `GET /api/heat`, and runs the easing /
 * decay ticker.
 *
 * The only React state here is a coarse tick counter, bumped at most once per
 * `TICK_MS` and only while some symbol still holds heat. Rows subscribe to their
 * own symbol through `useHeat`, so a payload covering 400 symbols re-renders
 * only the few whose value moved — never the whole feed.
 */
export function HeatProvider({
  children,
  seedUrl = '/api/heat',
  seedPayload,
  onReady,
}: {
  children: ReactNode;
  /** REST seed path; `null` skips it. A 404/HTML answer is ignored. */
  seedUrl?: string | null;
  /**
   * A payload to ingest immediately instead of fetching. Used by tests to
   * exercise the glow without a network, and harmless in the app (unused).
   */
  seedPayload?: unknown;
  /** Receives the ingest function so an outer stream hook can feed heat in. */
  onReady?: (ingest: HeatIngest) => void;
}) {
  const internal = useMemo(createHeatStore, []);
  const [, bump] = useState(0);

  const ingest = useCallback<HeatIngest>(
    (payload, receivedMs) => {
      internal.ingest(payload, receivedMs);
      bump((n) => n + 1);
    },
    [internal],
  );

  useEffect(() => {
    onReady?.(ingest);
  }, [ingest, onReady]);

  // An in-memory seed (tests) short-circuits the fetch below.
  useEffect(() => {
    if (seedPayload !== undefined) ingest(seedPayload);
  }, [seedPayload, ingest]);

  // Seed from REST once on mount.
  useEffect(() => {
    if (seedUrl === null || seedPayload !== undefined) return;
    let cancelled = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const res = await fetch(seedUrl, { headers: { accept: 'application/json' }, signal: controller.signal });
        if (!res.ok) return;
        const contentType = res.headers.get('content-type') ?? '';
        // A backend that has not implemented this yet serves index.html.
        if (!contentType.toLowerCase().includes('json')) return;
        const body: unknown = await res.json();
        if (cancelled) return;
        ingest(body);
      } catch {
        // Heat is additive: a missing endpoint must not surface as an error.
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [seedUrl, ingest, seedPayload]);

  // Easing / decay ticker. Idles completely when nothing is lit.
  useEffect(() => {
    let timer: number | null = null;
    const stop = (): void => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const start = (): void => {
      if (timer !== null) return;
      timer = window.setInterval(() => {
        internal.tick();
        bump((n) => n + 1);
        if (internal.store.liveCount() === 0) stop();
      }, HEAT_TICK_MS);
    };
    start();
    return stop;
  }, [internal]);

  const value = useMemo<HeatContextValue>(() => ({ store: internal.store, ingest }), [internal, ingest]);

  return <HeatContext.Provider value={value}>{children}</HeatContext.Provider>;
}

/** The raw store, or `null` outside a `HeatProvider`. */
export function useHeatContext(): HeatContextValue | null {
  return useContext(HeatContext);
}

/**
 * Subscribe to one symbol's heat.
 *
 * Returns `null` for "not interesting" — no data at all, or a value that has
 * decayed below the drawable threshold. Re-renders only when this symbol's heat,
 * reasons, or components actually change.
 */
export function useHeat(symbol: string | null | undefined): HeatEntry | null {
  const ctx = useContext(HeatContext);
  const store = ctx?.store ?? null;
  const key = heatKey(symbol);
  const cached = useMemo(() => ({ current: null as HeatEntry | null }), []);

  const subscribe = useCallback(
    (listener: () => void) => (store === null ? () => undefined : store.subscribe(key, listener)),
    [store, key],
  );

  const getSnapshot = useCallback((): HeatEntry | null => {
    if (store === null) return null;
    const entry = store.get(key);
    // `useSyncExternalStore` compares by identity, so hand back the same object
    // unless something the row renders actually changed.
    const previous = cached.current;
    if (entry === null) {
      cached.current = null;
      return null;
    }
    if (
      previous !== null &&
      previous.heat === entry.heat &&
      previous.updatedMs === entry.updatedMs &&
      previous.reasons === entry.reasons &&
      previous.components === entry.components
    ) {
      return previous;
    }
    cached.current = entry;
    return entry;
  }, [store, key, cached]);

  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Convenience: the display value (0-100), or 0 when uninteresting. */
export function useHeatValue(symbol: string | null | undefined): number {
  return useHeat(symbol)?.heat ?? 0;
}
