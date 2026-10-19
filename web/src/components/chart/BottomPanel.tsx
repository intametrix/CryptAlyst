import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { int } from '../../format';
import { readPanelHeight, readPanelOpen, savePanelHeight, savePanelOpen, PANEL_MIN_HEIGHT_PX } from './chartLayout';

export interface BottomPanelProps {
  /** How many events the list holds, for the collapsed badge. */
  eventCount: number;
  children: ReactNode;
}

/** The smallest the chart region may be, so the divider can never starve it. */
const CHART_MIN_PX = 120;

/**
 * The bottom panel: an 8px divider handle plus the two-column region below it
 * (events + data). Dragging the handle resizes (pointer capture, like
 * SignalsDock), double-clicking toggles open/collapsed, and when collapsed
 * only the handle remains, carrying the event count badge. Height and
 * open/closed persist via the layout helpers.
 */
export function BottomPanel({ eventCount, children }: BottomPanelProps) {
  const [open, setOpen] = useState<boolean>(() => readPanelOpen());
  const [height, setHeight] = useState<number>(() => readPanelHeight());
  const dragging = useRef<{ startY: number; startH: number } | null>(null);
  /** The region the chart and this panel share (the popup body). */
  const panelRef = useRef<HTMLElement | null>(null);

  const layoutBox = useCallback(
    (): HTMLElement | null => panelRef.current?.closest('.chart-popup') ?? null,
    [],
  );

  const clampHeight = useCallback(
    (value: number): number => {
      const box = layoutBox();
      const chart = box?.querySelector('.cp-chart-region') as HTMLElement | null;
      const shared =
        box !== null && box !== undefined
          ? box.getBoundingClientRect().height
          : chart !== null
            ? chart.getBoundingClientRect().height + height
            : 800;
      const ceiling = Math.max(PANEL_MIN_HEIGHT_PX, shared - CHART_MIN_PX);
      return Math.max(PANEL_MIN_HEIGHT_PX, Math.min(ceiling, Math.round(value)));
    },
    [layoutBox, height],
  );

  const toggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      savePanelOpen(next);
      return next;
    });
  }, []);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // Capture can be refused; the move/up handlers on the same element
        // still track the gesture while the pointer is over it.
      }
      dragging.current = { startY: event.clientY, startH: height };
      document.body.classList.add('no-select');
    },
    [height],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragging.current;
      if (drag === null) return;
      // Dragging the divider *down* makes the panel shorter.
      setHeight(clampHeight(drag.startH - (event.clientY - drag.startY)));
    },
    [clampHeight],
  );

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragging.current;
    dragging.current = null;
    document.body.classList.remove('no-select');
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // The capture was already released; nothing to do.
    }
    if (drag !== null) {
      setHeight((current) => {
        savePanelHeight(current);
        return current;
      });
    }
  }, []);

  // Re-clamp when the shared box shrinks (overlay resize), so the chart never
  // falls below its floor because of a stored height.
  const clampRef = useRef(clampHeight);
  clampRef.current = clampHeight;
  useEffect(() => {
    const box = layoutBox();
    if (box === null) return;
    const observer = new ResizeObserver(() => {
      setHeight((h) => clampRef.current(h));
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, [layoutBox]);

  return (
    <>
      <div
        className="cp-divider"
        role="separator"
        aria-orientation="horizontal"
        aria-label={open ? 'resize the bottom panel (double-click to collapse)' : 'expand the bottom panel'}
        aria-expanded={open}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={toggle}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 40 : 12;
          if (event.key === 'ArrowUp') {
            event.preventDefault();
            if (!open) toggle();
            else setHeight((h) => clampHeight(h + step));
          } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            setHeight((h) => clampHeight(h - step));
          } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggle();
          }
        }}
      >
        <span className="cp-divider-grip" aria-hidden="true" />
        <button
          type="button"
          className="cp-divider-badge"
          onClick={toggle}
          onPointerDown={(event) => event.stopPropagation()}
          onDoubleClick={(event) => event.stopPropagation()}
          title={open ? 'collapse the panel (double-click the divider works too)' : 'expand the panel'}
          aria-expanded={open}
        >
          {open ? '▾' : '▸'} {int(eventCount)} events
        </button>
      </div>
      {open && (
        <section
          className="cp-bottom open"
          style={{ height: `${height}px` }}
          ref={panelRef}
          aria-label="events and data"
        >
          {children}
        </section>
      )}
    </>
  );
}
