/**
 * Persisted layout state.
 *
 * Every resizable box writes its own size here, keyed by a stable id, and
 * restores it on load. A single `resetLayout()` clears the lot, because a
 * persisted layout the user has dragged into a bad state must be recoverable
 * without devtools.
 *
 * `localStorage` is treated as untrusted: it may be unavailable (private mode,
 * disabled storage) and its contents may be corrupt or from an older build, so
 * every read is validated and every write is wrapped.
 */

const STORAGE_KEY = 'pumpdump.layout.v1';

/** Size of one persisted box, in CSS pixels. */
export interface BoxSize {
  w: number;
  h: number;
}

/** Persisted chart indicator toggles. */
export type ChipState = Record<string, boolean>;

/** Everything persisted across reloads. */
export interface LayoutState {
  boxes: Record<string, BoxSize>;
  chips: ChipState;
}

const EMPTY: LayoutState = { boxes: {}, chips: {} };

function storage(): Storage | null {
  try {
    const s = globalThis.localStorage;
    if (s === undefined || s === null) return null;
    // Touch it: Safari throws on access in some privacy modes.
    const probe = '__pumpdump_probe__';
    s.setItem(probe, '1');
    s.removeItem(probe);
    return s;
  } catch {
    return null;
  }
}

function validSize(v: unknown): BoxSize | null {
  if (typeof v !== 'object' || v === null) return null;
  const { w, h } = v as { w?: unknown; h?: unknown };
  const width = typeof w === 'number' && Number.isFinite(w) ? w : null;
  const height = typeof h === 'number' && Number.isFinite(h) ? h : null;
  if (width === null && height === null) return null;
  return { w: width ?? 0, h: height ?? 0 };
}

export function loadLayout(): LayoutState {
  const s = storage();
  if (s === null) return EMPTY;
  try {
    const raw = s.getItem(STORAGE_KEY);
    if (raw === null || raw === '') return EMPTY;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return EMPTY;
    const { boxes, chips } = parsed as { boxes?: unknown; chips?: unknown };
    const out: LayoutState = { boxes: {}, chips: {} };
    if (typeof boxes === 'object' && boxes !== null) {
      for (const [key, value] of Object.entries(boxes as Record<string, unknown>)) {
        const size = validSize(value);
        if (size !== null) out.boxes[key] = size;
      }
    }
    if (typeof chips === 'object' && chips !== null) {
      for (const [key, value] of Object.entries(chips as Record<string, unknown>)) {
        if (typeof value === 'boolean') out.chips[key] = value;
      }
    }
    return out;
  } catch {
    return EMPTY;
  }
}

let cache: LayoutState | null = null;

/** The in-memory layout, read from storage on first use. */
export function getLayout(): LayoutState {
  if (cache === null) cache = loadLayout();
  return cache;
}

export function saveLayout(next: LayoutState): void {
  cache = next;
  const s = storage();
  if (s === null) return;
  try {
    s.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Quota or privacy failure: the in-memory copy still drives this session.
  }
}

/** Persist one box's size. A `null` on an axis means "not constrained". */
export function saveBoxSize(id: string, size: BoxSize): void {
  const current = getLayout();
  saveLayout({ ...current, boxes: { ...current.boxes, [id]: size } });
}

export function saveChipState(chips: ChipState): void {
  const current = getLayout();
  saveLayout({ ...current, chips });
}

/** Persisted size for a box, or `null` when nothing (valid) was stored. */
export function readBoxSize(id: string): BoxSize | null {
  return getLayout().boxes[id] ?? null;
}

/**
 * Drop every persisted size and toggle. Returns the now-empty state so the
 * caller can reset its React state from the same source of truth.
 */
export function resetLayout(): LayoutState {
  cache = EMPTY;
  const s = storage();
  if (s !== null) {
    try {
      s.removeItem(STORAGE_KEY);
    } catch {
      // Nothing else to do; the in-memory reset already took effect.
    }
  }
  return EMPTY;
}

/** Bumped on every reset so `usePersistentSize` re-reads cleared storage. */
let generation = 0;
const listeners = new Set<() => void>();

export function onLayoutReset(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getLayoutGeneration(): number {
  return generation;
}

export function bumpLayoutGeneration(): void {
  generation += 1;
  for (const fn of listeners) fn();
}
