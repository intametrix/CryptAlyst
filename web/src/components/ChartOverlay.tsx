import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { clamp } from '../hooks/usePersistentSize';
import { onLayoutReset, readBoxSize, saveBoxSize } from '../layout';

/** Which interaction a pointer gesture is performing. */
type Gesture = 'drag' | 'resize' | null;

export interface ChartOverlayProps {
  /** Stable id; the storage key for both size and position. */
  id: string;
  title: string;
  defaultWidth?: number;
  defaultHeight?: number;
  minWidth?: number;
  maxWidth?: number;
  minHeight?: number;
  maxHeight?: number;
  /** Rendered in the header, after the title (the close button lives here). */
  header?: ReactNode;
  children: ReactNode;
  /** Called on the close control, Escape, and backdrop presses. */
  onClose: () => void;
  /** Element focused when the panel opens; defaults to the panel itself. */
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  /** Extra class on the panel. */
  className?: string;
}

/**
 * A floating, resizable, draggable, dismissible panel.
 *
 * Deliberately generic: the chart is its only user today, but it owns no chart
 * knowledge, so a real OS-window variant (or another floating tool) could reuse
 * it unchanged.
 *
 * - **resizable** with the corner grip, or the arrow keys when the grip is
 *   focused. Size persists per `id`.
 * - **draggable** by the header; position persists per `id`, and is re-clamped
 *   whenever the window shrinks so the panel can never be stranded off-screen.
 * - **dismissible** through `onClose` from the close control, Escape, or a
 *   backdrop press. A press inside the panel never closes it.
 * - `role="dialog"` + `aria-modal="true"`, a focus trap while open, focus
 *   returned on close, and the body scroll locked and restored.
 *
 * Size and position live in exactly one place (`box`) — the panel is sized and
 * positioned from it — so dragging, resizing and "reset layout" always agree.
 */
/**
 * How tall a chart opens.
 *
 * 88% of the viewport, so a deliberately-opened chart gets the screen — the
 * clamp to `innerHeight - 16` stays as the ceiling, but the default now sits near
 * it instead of at a fixed, much shorter height. A small window still wins, and a
 * caller-supplied `defaultHeight` is used only when it is already taller than the
 * viewport-relative figure (a genuinely tall ask is not reduced).
 */
export function defaultHeightFor(viewportHeight: number, explicit?: number): number {
  const target = Math.round(viewportHeight * 0.88);
  return explicit !== undefined && explicit > target ? explicit : target;
}

export function ChartOverlay({
  id,
  title,
  defaultWidth = 1000,
  defaultHeight = 640,
  minWidth = 320,
  maxWidth = 2400,
  minHeight = 240,
  maxHeight = 1600,
  header,
  children,
  onClose,
  initialFocusRef,
  className,
}: ChartOverlayProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [gesture, setGesture] = useState<Gesture>(null);

  /** Viewport size, or a large-screen fallback when there is no `window`. */
  const viewport = useCallback(() => {
    const win = typeof window === 'undefined' ? null : window;
    return {
      width: win === null ? 1600 : win.innerWidth,
      height: win === null ? 900 : win.innerHeight,
    };
  }, []);

  /** Keep the panel inside the viewport, with its header always reachable. */
  const clampToViewport = useCallback(
    (next: { x: number; y: number; w: number; h: number }) => {
      const { width, height } = viewport();
      const w = clamp(next.w, minWidth, Math.min(maxWidth, Math.max(minWidth, width - 16)));
      const h = clamp(next.h, minHeight, Math.min(maxHeight, Math.max(minHeight, height - 16)));
      const minVisible = 120; // px of the panel that must stay on screen
      const x = clamp(next.x, minVisible - w, Math.max(minVisible, width - minVisible));
      const y = clamp(next.y, 0, Math.max(0, height - 44));
      return { x, y, w, h };
    },
    [maxHeight, maxWidth, minHeight, minWidth, viewport],
  );

  // Seeded from storage so a reopened panel lands where it was left, and clamped
  // immediately (a window may have shrunk since). `window` is guarded because
  // this component must also survive a server render.
  const [box, setBox] = useState(() => {
    const { width, height } = viewport();
    const storedSize = readBoxSize(`${id}.size`);
    const storedPos = readBoxSize(`${id}.pos`);
    const w = storedSize !== null && storedSize.w > 0 ? storedSize.w : defaultWidth;
    // Opening the chart deliberately should give the user the screen: the default
    // height tracks the viewport rather than a fixed number of pixels.
    const h = storedSize !== null && storedSize.h > 0 ? storedSize.h : defaultHeightFor(height, defaultHeight);
    return clampToViewport({
      x: storedPos !== null && storedPos.w !== 0 ? storedPos.w : Math.round((width - w) / 2),
      y: storedPos !== null && storedPos.h !== 0 ? storedPos.h : Math.round((height - h) / 3),
      w,
      h,
    });
  });

  /** Apply a change and persist the axes the caller owns. */
  const commit = useCallback(
    (next: { x: number; y: number; w: number; h: number }, persistSize: boolean, persistPos: boolean) => {
      const clamped = clampToViewport(next);
      setBox(clamped);
      if (persistSize) saveBoxSize(`${id}.size`, { w: clamped.w, h: clamped.h });
      if (persistPos) saveBoxSize(`${id}.pos`, { w: clamped.x, h: clamped.y });
    },
    [clampToViewport, id],
  );

  // Re-clamp on window resize, so the panel always comes back on screen.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onResize = (): void => setBox((prev) => clampToViewport(prev));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [clampToViewport]);

  // "Reset layout" restores the opening geometry.
  useEffect(
    () =>
      onLayoutReset(() => {
        const { width, height } = viewport();
        const w = defaultWidth;
        const h = defaultHeightFor(height, defaultHeight);
        commit({ x: Math.round((width - w) / 2), y: Math.round((height - h) / 3), w, h }, true, true);
      }),
    [commit, defaultHeight, defaultWidth, viewport],
  );

  /* -- keyboard: Escape closes, Tab stays inside ----------------------------- */

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        // Never steal Escape from a field the user is typing into.
        const active = document.activeElement;
        if (
          active instanceof HTMLElement &&
          (active.tagName === 'INPUT' ||
            active.tagName === 'TEXTAREA' ||
            active.tagName === 'SELECT' ||
            active.isContentEditable)
        ) {
          return;
        }
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const panel = panelRef.current;
      if (panel === null) return;
      const focusables = [
        ...panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ].filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusables.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    if (typeof window === 'undefined') return;
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [onClose]);

  /* -- focus in on open, back on close -------------------------------------- */

  const openerRef = useRef<HTMLElement | null>(
    typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );

  useEffect(() => {
    const opener = openerRef.current;
    const target = initialFocusRef?.current ?? panelRef.current;
    target?.focus();
    return () => {
      if (opener !== null && opener.isConnected) opener.focus();
    };
  }, [initialFocusRef]);

  /* -- scroll lock ---------------------------------------------------------- */

  useEffect(() => {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const { overflow, position, top, left, width } = document.body.style;
    document.body.style.overflow = 'hidden';
    document.body.style.position = 'fixed';
    document.body.style.top = `-${scrollY}px`;
    document.body.style.left = `-${scrollX}px`;
    document.body.style.width = '100%';
    return () => {
      document.body.style.overflow = overflow;
      document.body.style.position = position;
      document.body.style.top = top;
      document.body.style.left = left;
      document.body.style.width = width;
      window.scrollTo(scrollX, scrollY);
    };
  }, []);

  /* -- pointer gestures ----------------------------------------------------- */

  const beginGesture = useCallback(
    (event: React.PointerEvent<HTMLElement>, kind: 'drag' | 'resize') => {
      if (event.button !== 0 && event.pointerType === 'mouse') return;

      const startX = event.clientX;
      const startY = event.clientY;
      const origin = box;
      const handle = event.currentTarget;
      const prevSelect = document.body.style.userSelect;
      const prevCursor = document.body.style.cursor;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = kind === 'drag' ? 'grabbing' : 'nwse-resize';
      setGesture(kind);
      try {
        // Pointer capture keeps the gesture alive outside the handle or window.
        handle.setPointerCapture(event.pointerId);
      } catch {
        // The window listeners below still work without capture.
      }

      const onMove = (move: PointerEvent): void => {
        const dx = move.clientX - startX;
        const dy = move.clientY - startY;
        setBox(
          clampToViewport(
            kind === 'drag'
              ? { ...origin, x: origin.x + dx, y: origin.y + dy }
              : { ...origin, w: origin.w + dx, h: origin.h + dy },
          ),
        );
      };
      const finish = (): void => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', finish);
        document.body.style.userSelect = prevSelect;
        document.body.style.cursor = prevCursor;
        setGesture(null);
        setBox((current) => {
          if (kind === 'resize') saveBoxSize(`${id}.size`, { w: current.w, h: current.h });
          else saveBoxSize(`${id}.pos`, { w: current.x, h: current.y });
          return current;
        });
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', finish);
      window.addEventListener('pointercancel', finish);
      event.preventDefault();
    },
    [box, clampToViewport, id],
  );

  const onHeaderPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      // A press on a control in the header must not start a drag.
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('button, a, input, select, textarea') !== null) return;
      beginGesture(event, 'drag');
    },
    [beginGesture],
  );

  const onGripKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      const step = event.shiftKey ? 60 : 16;
      const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
      const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
      if (dx === 0 && dy === 0) return;
      event.preventDefault();
      commit({ ...box, w: box.w + dx, h: box.h + dy }, true, false);
    },
    [box, commit],
  );

  return (
    <>
      <div className="overlay-backdrop" onPointerDown={onClose} aria-hidden="true" />
      <div
        className={`overlay chart-overlay${gesture !== null ? ' dragging' : ''}`}
        style={{ left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` }}
        data-overlay-id={id}
      >
        <div
          ref={panelRef}
          className={`overlay-panel${className !== undefined ? ` ${className}` : ''}`}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          tabIndex={-1}
        >
          <div className="overlay-head" onPointerDown={onHeaderPointerDown}>
            <span className="overlay-grip" aria-hidden="true" />
            <h2 className="overlay-title">{title}</h2>
            <div className="overlay-head-actions">{header}</div>
          </div>
          <div className="overlay-body">{children}</div>
        </div>

        {/* Outside the clip, so a narrow panel can never cut the grip off. */}
        <span
          role="separator"
          tabIndex={0}
          aria-label="resize chart panel (arrow keys, shift for bigger steps)"
          className="grip grip-both overlay-grip-corner"
          onPointerDown={(event) => beginGesture(event, 'resize')}
          onKeyDown={onGripKeyDown}
        >
          <span className="grip-dots" aria-hidden="true" />
        </span>
      </div>
    </>
  );
}
