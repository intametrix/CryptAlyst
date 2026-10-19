import { useCallback, useEffect, useRef, useState } from 'react';
import {
  bumpLayoutGeneration,
  getLayoutGeneration,
  onLayoutReset,
  readBoxSize,
  saveBoxSize,
  type BoxSize,
} from '../layout';

/** Numeric guard used everywhere a size is clamped. */
export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  if (max < min) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

export type ResizeAxis = 'both' | 'x' | 'y';

export interface PersistentSizeOptions {
  /** Stable id — the `localStorage` key for this box. */
  id: string;
  axis: ResizeAxis;
  minWidth?: number;
  maxWidth?: number;
  minHeight?: number;
  maxHeight?: number;
  /**
   * When true the box keeps its resolved height even though `axis` is `'x'`:
   * used by the horizontal split, where one side owns the width and the
   * enclosing row already fixes the height.
   */
  fillHeight?: boolean;
  /** Size to open at before the user has ever dragged this box. */
  defaultWidth?: number;
  defaultHeight?: number;
}

export interface PersistentSize {
  ref: React.RefObject<HTMLDivElement | null>;
  /** Explicit pixel size, or an axis left at `null` to let CSS decide. */
  size: BoxSize;
  startDrag: (event: React.PointerEvent<HTMLElement>, handleAxis?: ResizeAxis) => void;
  startDragX: (event: React.PointerEvent<HTMLElement>) => void;
  startDragY: (event: React.PointerEvent<HTMLElement>) => void;
  /** Keyboard resizing, so the grip is usable without a pointer. */
  nudge: (dx: number, dy: number) => void;
  /** Drop the stored size and fall back to the defaults. */
  reset: () => void;
  dragging: boolean;
}

const DEFAULT_MAX = 4000;

function maxFor(explicit: number | undefined, axis: 'width' | 'height'): number {
  if (explicit !== undefined) return explicit;
  const host = typeof document !== 'undefined' ? document.documentElement : null;
  if (host !== null && axis === 'height') return Math.max(320, host.clientHeight);
  return DEFAULT_MAX;
}

/**
 * A pointer-draggable size for one box, restored from and persisted to
 * `localStorage`.
 *
 * The drag uses pointer capture, so the pointer can leave the grip (or the
 * window) without losing the gesture, and it suppresses text selection and
 * native scrolling for the duration — dragging a grip must never select the
 * page or scroll it.
 */
export function usePersistentSize(options: PersistentSizeOptions): PersistentSize {
  const {
    id,
    axis,
    minWidth = 220,
    maxWidth,
    minHeight = 140,
    maxHeight,
    fillHeight = false,
    defaultWidth,
    defaultHeight,
  } = options;

  const resolve = useCallback((): BoxSize => {
    const stored = readBoxSize(id);
    const wantsW = axis === 'both' || axis === 'x';
    const wantsH = fillHeight || axis === 'both' || axis === 'y';
    const storedW = stored !== null && stored.w > 0 ? stored.w : (defaultWidth ?? 0);
    const storedH = stored !== null && stored.h > 0 ? stored.h : (defaultHeight ?? 0);
    return {
      w: wantsW && storedW > 0 ? clamp(storedW, minWidth, maxFor(maxWidth, 'width')) : 0,
      h: wantsH && storedH > 0 ? clamp(storedH, minHeight, maxFor(maxHeight, 'height')) : 0,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, axis, minWidth, maxWidth, minHeight, maxHeight, fillHeight, defaultWidth, defaultHeight]);

  const [size, setSize] = useState<BoxSize>(resolve);
  const [dragging, setDragging] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const sizeRef = useRef<BoxSize>(size);
  sizeRef.current = size;

  // A "reset layout" action elsewhere in the app must clear these too.
  useEffect(() => {
    setSize(resolve());
  }, [resolve]);

  useEffect(() => {
    return onLayoutReset(() => setSize(resolve()));
  }, [resolve]);

  // Restore once more if the persisted layout generation was bumped between
  // the initial state and the first effect (e.g. a reset during mount).
  const generationRef = useRef(getLayoutGeneration());
  useEffect(() => {
    if (generationRef.current !== getLayoutGeneration()) {
      generationRef.current = getLayoutGeneration();
      setSize(resolve());
    }
  });

  const persist = useCallback(
    (next: BoxSize) => {
      saveBoxSize(id, next);
    },
    [id],
  );

  const nudge = useCallback(
    (dx: number, dy: number) => {
      setSize((prev) => {
        const next: BoxSize = {
          w: axis === 'both' || axis === 'x' ? clamp(prev.w + dx, minWidth, maxFor(maxWidth, 'width')) : prev.w,
          h:
            fillHeight || axis === 'both' || axis === 'y'
              ? clamp(prev.h + dy, minHeight, maxFor(maxHeight, 'height'))
              : prev.h,
        };
        persist(next);
        return next;
      });
    },
    [axis, fillHeight, minWidth, maxWidth, minHeight, maxHeight, persist],
  );

  const reset = useCallback(() => {
    const cleared: BoxSize = { w: 0, h: 0 };
    setSize(cleared);
    saveBoxSize(id, cleared);
  }, [id]);

  const startDrag = useCallback(
    (event: React.PointerEvent<HTMLElement>, handleAxis?: ResizeAxis) => {
      if (event.button !== 0 && event.pointerType === 'mouse') return;
      const mode = handleAxis ?? axis;
      const target = event.currentTarget;
      const startX = event.clientX;
      const startY = event.clientY;

      // Measure the parent so a drag can never push the box outside its own row.
      const parent = ref.current?.parentElement ?? null;
      const parentRect = parent?.getBoundingClientRect() ?? null;
      const parentW = parentRect !== null ? Math.floor(parentRect.width) : DEFAULT_MAX;
      const parentH = parentRect !== null ? Math.floor(parentRect.height) : maxFor(maxHeight, 'height');

      const rect = ref.current?.getBoundingClientRect() ?? null;
      // A box that is currently sized by CSS (flex/grid) starts from its
      // measured size, so the first drag continues from where it looks.
      const startW = rect !== null && rect.width > 0 ? Math.round(rect.width) : sizeRef.current.w;
      const startH = rect !== null && rect.height > 0 ? Math.round(rect.height) : sizeRef.current.h;

      const limitW = Math.min(maxFor(maxWidth, 'width'), Math.max(minWidth, parentW > 0 ? parentW : DEFAULT_MAX));
      const limitH = Math.min(maxFor(maxHeight, 'height'), Math.max(minHeight, parentH > 0 ? parentH : DEFAULT_MAX));

      const prevCursor = document.body.style.cursor;
      const prevSelect = document.body.style.userSelect;
      document.body.style.cursor = mode === 'y' ? 'ns-resize' : mode === 'x' ? 'ew-resize' : 'nwse-resize';
      document.body.style.userSelect = 'none';
      setDragging(true);

      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // A capture failure still leaves the window listeners below working.
      }

      const onMove = (move: PointerEvent): void => {
        const dx = move.clientX - startX;
        const dy = move.clientY - startY;
        setSize((prev) => {
          const next: BoxSize = {
            w: mode === 'both' || mode === 'x' ? clamp(startW + dx, minWidth, limitW) : prev.w,
            h:
              fillHeight || mode === 'both' || mode === 'y' ? clamp(startH + dy, minHeight, limitH) : prev.h,
          };
          return next;
        });
      };

      const finish = (): void => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', finish);
        document.body.style.cursor = prevCursor;
        document.body.style.userSelect = prevSelect;
        setDragging(false);
        persist(sizeRef.current);
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', finish);
      window.addEventListener('pointercancel', finish);
      event.preventDefault();
      event.stopPropagation();
    },
    [axis, fillHeight, minHeight, minWidth, maxHeight, maxWidth, persist],
  );

  const startDragX = useCallback((event: React.PointerEvent<HTMLElement>) => startDrag(event, 'x'), [startDrag]);
  const startDragY = useCallback((event: React.PointerEvent<HTMLElement>) => startDrag(event, 'y'), [startDrag]);

  return { ref, size, startDrag, startDragX, startDragY, nudge, reset, dragging };
}

/**
 * Split a container into two independently resizable panes separated by a
 * draggable divider. `vertical` stacks the children; the default lays them out
 * side by side.
 */
export function useSplit(opts: {
  id: string;
  vertical?: boolean;
  /** Which side is fixed-size; the other grows. */
  side?: 'first' | 'second';
  min: number;
  max?: number;
  defaultSize: number;
}): {
  size: number;
  ref: React.RefObject<HTMLDivElement | null>;
  dragging: boolean;
  /** True once the user has actually dragged the divider and we restored it. */
  stored: boolean;
  startDrag: (event: React.PointerEvent<HTMLElement>) => void;
  nudge: (delta: number) => void;
} {
  const { id, vertical = false, side = 'first', min, max, defaultSize } = opts;
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<number>(() => {
    const stored = readBoxSize(id);
    const value = vertical ? stored?.h ?? 0 : stored?.w ?? 0;
    return value > 0 ? clamp(value, min, max ?? DEFAULT_MAX) : defaultSize;
  });
  const [dragging, setDragging] = useState(false);
  const [stored, setStored] = useState<boolean>(() => {
    const box = readBoxSize(id);
    return vertical ? (box?.h ?? 0) > 0 : (box?.w ?? 0) > 0;
  });
  const sizeRef = useRef(size);
  sizeRef.current = size;

  const restore = useCallback(() => {
    const box = readBoxSize(id);
    const value = vertical ? box?.h ?? 0 : box?.w ?? 0;
    setStored(value > 0);
    setSize(value > 0 ? clamp(value, min, max ?? DEFAULT_MAX) : defaultSize);
  }, [id, vertical, min, max, defaultSize]);

  useEffect(() => {
    restore();
    const off = onLayoutReset(restore);
    return off;
  }, [restore]);

  const persist = useCallback(
    (value: number) => {
      saveBoxSize(id, vertical ? { w: 0, h: value } : { w: value, h: 0 });
    },
    [id, vertical],
  );

  const nudge = useCallback(
    (delta: number) => {
      setSize((prev) => {
        const limit = max ?? (vertical ? maxFor(undefined, 'height') : maxFor(undefined, 'width'));
        const next = clamp(prev + delta, min, limit);
        persist(next);
        return next;
      });
    },
    [max, min, persist, vertical],
  );

  const startDrag = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (event.button !== 0 && event.pointerType === 'mouse') return;
      const target = event.currentTarget;
      const container = ref.current;
      if (container === null) return;

      const startPos = vertical ? event.clientY : event.clientX;
      const startSize = sizeRef.current;
      const containerRect = container.getBoundingClientRect();
      const total = vertical ? containerRect.height : containerRect.width;
      // Keep a usable remainder on the other side of the divider.
      const hardMax = Math.max(min, Math.floor(total * 0.85));
      const limit = Math.min(max ?? DEFAULT_MAX, hardMax);

      const prevCursor = document.body.style.cursor;
      const prevSelect = document.body.style.userSelect;
      document.body.style.cursor = vertical ? 'ns-resize' : 'ew-resize';
      document.body.style.userSelect = 'none';
      setDragging(true);

      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // Fall through to the window listeners.
      }

      const onMove = (move: PointerEvent): void => {
        const current = vertical ? move.clientY : move.clientX;
        const delta = (current - startPos) * (side === 'first' ? 1 : -1);
        setSize(clamp(startSize + delta, min, limit));
      };

      const finish = (): void => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', finish);
        document.body.style.cursor = prevCursor;
        document.body.style.userSelect = prevSelect;
        setDragging(false);
        persist(sizeRef.current);
      };

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', finish);
      window.addEventListener('pointercancel', finish);
      event.preventDefault();
    },
    [max, min, persist, side, vertical],
  );

  return { size, ref, dragging, stored, startDrag, nudge };
}

export { bumpLayoutGeneration };
