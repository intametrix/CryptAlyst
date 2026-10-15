/**
 * The heat store and its pure helpers.
 *
 * Split out of `heat.tsx` so the store can be exercised without a DOM or React:
 * everything here is plain data. `heat.tsx` re-exports it for the components.
 */

/** Per-symbol heat, as pushed by the `heat` SSE message / `GET /api/heat`. */
export interface HeatEntry {
  symbol: string;
  /** 0-100. Below `HEAT_MIN` nothing is drawn at all. */
  heat: number;
  /** Each component is 0-1. */
  components: Record<string, number>;
  /** Short human strings, e.g. "16.1x flow burst". */
  reasons: string[];
  /** Local ms this value was last refreshed. */
  updatedMs: number;
}

/** Below this, no border is drawn at all (the "uninteresting" case). */
export const HEAT_MIN = 25;

/**
 * How often values are eased toward their target, and decay when stale.
 * Exported because the React provider drives the interval with it.
 */
export const HEAT_TICK_MS = 700;

/**
 * Easing applied per tick. A new payload is authoritative, but it is approached
 * gradually (half the remaining gap per tick) so a jump from 30 to 90 fades up
 * instead of snapping. A symbol absent from the newest payload decays at 6% per
 * tick, walking 100 → below the 25 threshold in roughly 11 seconds.
 */
const EASE = 0.5;
const DECAY = 0.94;

/**
 * Map a heat value (0-100) onto the 0-1 range the stylesheet interpolates:
 * `25 → 0`, `100 → 1`, anything below the threshold → 0 (nothing is drawn).
 */
export function heatStrength(heat: number | null | undefined): number {
  if (typeof heat !== 'number' || !Number.isFinite(heat) || heat < HEAT_MIN) return 0;
  return Math.max(0, Math.min(1, (heat - HEAT_MIN) / (100 - HEAT_MIN)));
}

/**
 * Inline style carrying the `--heat` custom property, or `undefined` when the
 * ticker is not interesting enough to draw anything at all.
 *
 * One property drives both the border and the glow in `styles.css`, so the
 * mapping from "heat" to "brightness" is tuned in exactly one place.
 */
export function heatStyle(heat: number | null | undefined): { [key: string]: string } | undefined {
  const strength = heatStrength(heat);
  if (strength <= 0) return undefined;
  return { '--heat': strength.toFixed(3) };
}

/** Normalise a symbol for map keys. */
export function heatKey(symbol: string | null | undefined): string {
  return typeof symbol === 'string' ? symbol.trim().toUpperCase() : '';
}

/** A read-only view of the heat table. */
export interface HeatStore {
  get: (symbol: string | null | undefined) => HeatEntry | null;
  subscribe: (symbol: string, listener: () => void) => () => void;
  /** Latest payload receive time, or `null` when nothing ever arrived. */
  lastUpdateMs: () => number | null;
  /** Symbols currently at or above the drawable threshold. */
  activeCount: () => number;
  /** Symbols still holding any value at all (drives the decay ticker). */
  liveCount: () => number;
}

/** Ingest function exposed to the stream hook. */
export type HeatIngest = (payload: unknown, receivedMs?: number) => void;

export interface HeatStoreInternal {
  store: HeatStore;
  ingest: HeatIngest;
  /** Ease/decay every symbol one step toward its target. */
  tick: () => void;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * Parse a `heat` payload (SSE message or `GET /api/heat` body).
 *
 * Tolerates everything the contract allows to be absent: a missing `rows`, rows
 * with no symbol, a non-numeric `heat`, non-object `components`, and a
 * `reasons` list containing non-strings. A payload with nothing usable yields an
 * empty array, which the store treats as "no news" rather than as "everything
 * went cold" — so one malformed frame cannot blank the whole board.
 */
export function parseHeatRows(payload: unknown): HeatEntry[] {
  if (typeof payload !== 'object' || payload === null) return [];
  const rows = (payload as { rows?: unknown }).rows;
  if (!Array.isArray(rows)) return [];
  const now = Date.now();
  const out: HeatEntry[] = [];
  for (const raw of rows) {
    if (typeof raw !== 'object' || raw === null) continue;
    const row = raw as { symbol?: unknown; heat?: unknown; components?: unknown; reasons?: unknown };
    const symbol = heatKey(typeof row.symbol === 'string' ? row.symbol : null);
    if (symbol === '') continue;
    const heat = Math.max(0, Math.min(100, num(row.heat) ?? 0));
    const components: Record<string, number> = {};
    if (typeof row.components === 'object' && row.components !== null) {
      for (const [k, v] of Object.entries(row.components as Record<string, unknown>)) {
        const value = num(v);
        if (value !== null) components[k] = value;
      }
    }
    const reasons = Array.isArray(row.reasons)
      ? row.reasons.filter((r): r is string => typeof r === 'string')
      : [];
    out.push({ symbol, heat, components, reasons, updatedMs: now });
  }
  return out;
}

export function createHeatStore(): HeatStoreInternal {
  /** What is currently on screen. */
  const shown = new Map<string, HeatEntry>();
  /** What the backend most recently said. */
  const target = new Map<string, number>();
  const listeners = new Map<string, Set<() => void>>();
  let lastUpdateMs: number | null = null;

  const notify = (symbol: string): void => {
    const set = listeners.get(symbol);
    if (set === undefined) return;
    for (const fn of [...set]) fn();
  };

  const ingest = (payload: unknown, receivedMs: number = Date.now()): void => {
    const entries = parseHeatRows(payload);
    if (entries.length === 0) return;
    lastUpdateMs = receivedMs;

    const seen = new Set<string>();
    for (const entry of entries) {
      seen.add(entry.symbol);
      const previous = shown.get(entry.symbol);
      target.set(entry.symbol, entry.heat);
      shown.set(entry.symbol, entry);
      // Only wake subscribers when something they render actually changed.
      if (
        previous === undefined ||
        previous.heat !== entry.heat ||
        previous.reasons.join('\u0000') !== entry.reasons.join('\u0000') ||
        JSON.stringify(previous.components) !== JSON.stringify(entry.components)
      ) {
        notify(entry.symbol);
      }
    }

    // A symbol missing from this payload is no longer interesting: ease it to
    // zero. The border fades out instead of vanishing.
    for (const symbol of [...target.keys()]) {
      if (!seen.has(symbol)) target.set(symbol, 0);
    }
  };

  const tick = (): void => {
    for (const [symbol, entry] of [...shown.entries()]) {
      const goal = target.get(symbol) ?? 0;
      let next: number;
      if (goal <= 0) {
        next = entry.heat * DECAY;
        if (next < 1) {
          shown.delete(symbol);
          target.delete(symbol);
          notify(symbol);
          continue;
        }
      } else {
        const gap = goal - entry.heat;
        next = Math.abs(gap) < 0.5 ? goal : entry.heat + gap * EASE;
      }
      const rounded = Math.round(next * 100) / 100;
      if (rounded !== entry.heat) {
        shown.set(symbol, { ...entry, heat: rounded });
        notify(symbol);
      }
    }
  };

  return {
    store: {
      get: (symbol) => {
        const k = heatKey(symbol);
        if (k === '') return null;
        return shown.get(k) ?? null;
      },
      subscribe: (symbol, listener) => {
        const k = heatKey(symbol);
        if (k === '') return () => undefined;
        let set = listeners.get(k);
        if (set === undefined) {
          set = new Set();
          listeners.set(k, set);
        }
        set.add(listener);
        return () => {
          const current = listeners.get(k);
          if (current === undefined) return;
          current.delete(listener);
          if (current.size === 0) listeners.delete(k);
        };
      },
      lastUpdateMs: () => lastUpdateMs,
      activeCount: () => {
        let n = 0;
        for (const entry of shown.values()) if (entry.heat >= HEAT_MIN) n += 1;
        return n;
      },
      liveCount: () => shown.size,
    },
    ingest,
    tick,
  };
}

