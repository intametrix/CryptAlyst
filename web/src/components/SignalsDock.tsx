import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { int } from '../format';
import { saveBoxSize, readBoxSize } from '../layout';

export interface SignalsDockProps {
  /** How many signals are in the list, for the header count. */
  count: number;
  /** Collapsed or open. */
  open: boolean;
  onToggle: () => void;
  /** The list (and the selected signal's detail, rendered inline by the caller). */
  children: ReactNode;
  /** Shown even when collapsed, so the panel explains itself. */
  hint?: string;
}

const MIN_OPEN_PX = 120;
/**
 * The chart's own floor.
 *
 * The panel may never grow so large that the chart has less than this: the
 * requirement is a *shared* minimum, and letting the divider win meant the body
 * collapsed to 76px — a canvas with no candles in it (measured).
 */
const MIN_CHART_PX = 220;

/**
 * The signals container below the chart.
 *
 * One region, not two: the list and the selected signal's detail live in the same
 * collapsible box, because two stacked panels bound to the same selection would
 * leave the user unsure which one is authoritative.
 *
 * The panel is a flex sibling *above* nothing and *below* the chart body, and the
 * chart body is `flex: 1 1 auto`. So every pixel this panel gives up is taken by
 * the chart: collapsing it grows the price pane by construction, and the pane
 * allocator re-runs from the chart's own resize observer.
 */
export function SignalsDock({ count, open, onToggle, children, hint }: SignalsDockProps) {
  const [height, setHeight] = useState<number>(() => {
    const stored = readBoxSize('signals.dock');
    const value = stored !== null && stored.h > 0 ? stored.h : 260;
    return Math.max(MIN_OPEN_PX, value);
  });
  const dragging = useRef<{ startY: number; startH: number } | null>(null);
  /** The dock element itself, used to find the box it shares with the chart. */
  const panelRef = useRef<HTMLElement | null>(null);

  /**
   * The box the dock and the chart share.
   *
   * Not `parentElement`: the dock sits in a slim wrapper, so measuring that gave a
   * 34px parent and clamped the panel to its floor. The layout box is the chart
   * panel itself, which holds the chart body and this dock as flex siblings.
   */
  const layoutBox = useCallback((): HTMLElement | null => panelRef.current?.closest('.chart-panel') ?? null, []);

  /**
   * The dock may take at most this much.
   *
   * The chart's own floor is measured against the **chart body**, not the panel:
   * the panel also holds a toolbar, the chip bar, the outlook strip and a footer,
   * so reserving a flat 220px of panel height still left the body at 41px with a
   * 180px host — a canvas with no room for candles. What the dock gives up is
   * exactly what the body gains, so the body plus the current dock height is the
   * space the two share.
   */
  const ceilingFor = useCallback((): number => {
    const box = layoutBox();
    const body = box?.querySelector('.chart-body') as HTMLElement | null;
    if (body === null) return 600;
    const bodyHeight = body.getBoundingClientRect().height;
    // The body may not fall below its floor, and the dock may not fall below its
    // own; together those bound the dock's size.
    return Math.max(MIN_OPEN_PX, Math.round(bodyHeight + height - MIN_CHART_PX));
  }, [layoutBox, height]);
  const clampHeight = useCallback(
    (value: number): number => Math.max(MIN_OPEN_PX, Math.min(ceilingFor(), Math.round(value))),
    [ceilingFor],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      // Pointer capture, so a fast drag that leaves the 8px handle keeps resizing.
      // Capture first, and never let it abort the drag: a capture call can throw
      // (a synthetic event has no active pointer, and a browser can refuse), and
      // losing the capture is recoverable while a half-started drag is not.
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // Without capture the drag still works while the pointer stays over the
        // handle; the move handler is attached to the same element.
      }
      dragging.current = { startY: event.clientY, startH: height };
      document.body.classList.add('no-select');
    },
    [height],
  );

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragging.current;
    if (drag === null) return;
    // Dragging the divider *down* makes the panel shorter.
    setHeight(clampHeight(drag.startH - (event.clientY - drag.startY)));
  }, [clampHeight]);

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
      // Persisted like every other box, so a reopen keeps the user's sizing.
      saveBoxSize('signals.dock', { w: 0, h: height });
    }
  }, [height]);

  // The observer is subscribed once. `clampHeight` is reached through a ref, so
  // re-subscribing on every height change cannot clamp back the value the user is
  // in the middle of setting.
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
      {open && (
        <div
          className="signals-divider"
          role="separator"
          aria-orientation="horizontal"
          aria-label="resize the signals panel"
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={(event) => {
            const step = event.shiftKey ? 40 : 12;
            if (event.key === 'ArrowUp') {
              event.preventDefault();
              // The same ceiling as the drag, so neither path can starve the chart.
              setHeight((h) => clampHeight(h + step));
            } else if (event.key === 'ArrowDown') {
              event.preventDefault();
              setHeight(() => clampHeight(height - step));
            }
          }}
        />
      )}
      <section
        className={`signals-dock${open ? ' open' : ' collapsed'}`}
        style={open ? { height: `${height}px` } : undefined}
        ref={panelRef}
      >
        <header className="signals-head">
          <button
            type="button"
            className="signals-toggle"
            aria-expanded={open}
            aria-controls="signals-body"
            onClick={onToggle}
            title={open ? 'collapse the signals panel' : 'expand the signals panel'}
          >
            <span className="caret" aria-hidden="true">
              {open ? '\u25be' : '\u25b8'}
            </span>
            signals
            <span className="num signals-count">{int(count)}</span>
          </button>
          {hint !== undefined && <span className="signals-hint">{hint}</span>}
        </header>
        {/* The body is always mounted so the list keeps its scroll position and
            the selection is never lost to a collapse; it is simply hidden. */}
        <div className="signals-body" id="signals-body" hidden={!open}>
          {children}
        </div>
      </section>
    </>
  );
}
