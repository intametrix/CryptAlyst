import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { TooltipSpec } from '../glossary';
import { onLayoutReset } from '../layout';
import { computePlacement, type TipPosition } from './tooltipPlacement';
import { TooltipBody } from './Tooltip';

/** What the viewer is currently showing. */
export interface TooltipRequest {
  spec: TooltipSpec;
  /** Optional current value rendered as a "now" row. */
  value?: string | null;
  /** Optional "why" list, e.g. the heat reasons. */
  reasons?: readonly string[] | null;
  /** Optional 0-1 component breakdown. */
  breakdown?: Readonly<Record<string, number>> | null;
  /** Optional display names for the breakdown keys. */
  breakdownLabels?: Record<string, string> | null;
  tone?: string;
  preferred?: 'top' | 'bottom';
  /** The trigger, used to anchor the tooltip. */
  anchor: Element | null;
  /** React `aria-describedby` id for the trigger. */
  id: string;
}

export interface TooltipHandle {
  /** Open (hover/pointer enter). */
  show: (request: TooltipRequest) => void;
  /** Close, but only if `id` is the tooltip currently showing. */
  hide: (id: string) => void;
  /** Recompute the position (used by the trigger's focus/scroll handlers). */
  refresh: (id: string) => void;
  /** The id this provider expects a trigger to expose. */
  nextId: () => string;
  activeId: string | null;
}

const TooltipViewerContext = createContext<TooltipHandle | null>(null);

/** True when a `<TooltipViewer>` is mounted above this component. */
export function useTooltipViewer(): TooltipHandle | null {
  return useContext(TooltipViewerContext);
}

/**
 * A single, app-wide tooltip surface.
 *
 * Mounting one tooltip element per trigger is wasteful in a dashboard with
 * hundreds of rows, so triggers publish their content here instead and one
 * viewer renders it in a portal. That keeps:
 *
 * - **rich content** — title, formula, meaning, reading, units, current value
 * - **hover and keyboard focus** — both call `show`; `aria-describedby` points
 *   at the viewer's `role="tooltip"` element
 * - **never overflowing the viewport** — positions come from the pure
 *   `computePlacement` helper, which clamps on both axes and flips sides
 * - **repositioning on container resize** — a `ResizeObserver` on the document
 *   element plus scroll/resize listeners re-anchor it while a panel is dragged
 */
export function TooltipViewer({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<TooltipRequest | null>(null);
  const [position, setPosition] = useState<TipPosition | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const requestRef = useRef<TooltipRequest | null>(null);
  requestRef.current = request;
  const counter = useRef(0);

  const nextId = useCallback(() => {
    counter.current += 1;
    return `tip-${counter.current}`;
  }, []);

  const show = useCallback((next: TooltipRequest) => {
    setRequest(next);
    setPosition(null);
  }, []);

  const hide = useCallback((id: string) => {
    setRequest((prev) => (prev !== null && prev.id === id ? null : prev));
  }, []);

  const place = useCallback(() => {
    const current = requestRef.current;
    const tip = tipRef.current;
    if (current === null || tip === null) return;
    const anchor = current.anchor;
    if (anchor === null || !anchor.isConnected) {
      setRequest(null);
      return;
    }
    const t = anchor.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    setPosition(
      computePlacement({
        trigger: { top: t.top, left: t.left, width: t.width, height: t.height },
        tip: { width: tipRect.width, height: tipRect.height },
        viewport: {
          width: window.innerWidth,
          height: window.innerHeight,
        },
        preferred: current.preferred ?? 'bottom',
      }),
    );
  }, []);

  const refresh = useCallback(
    (id: string) => {
      if (requestRef.current?.id !== id) return;
      place();
    },
    [place],
  );

  // Position after the tooltip has content (it is rendered hidden for one pass).
  useEffect(() => {
    if (request === null) {
      setPosition(null);
      return;
    }
    place();
  }, [request, place]);

  useEffect(() => {
    if (request === null) return;
    const onScrollOrResize = (): void => place();
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);

    // Container resize: the document element changes size whenever a resizable
    // box is dragged, so this catches panel drags that a window resize misses.
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => place());
      observer.observe(document.documentElement);
      if (request.anchor !== null) observer.observe(request.anchor);
    }
    const offReset = onLayoutReset(place);

    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setRequest(null);
    };
    window.addEventListener('keydown', onKey);

    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
      window.removeEventListener('keydown', onKey);
      observer?.disconnect();
      offReset();
    };
  }, [request, place]);

  const value = useMemo<TooltipHandle>(
    () => ({ show, hide, refresh, nextId, activeId: request?.id ?? null }),
    [show, hide, refresh, nextId, request],
  );

  const arrowStyle = position === null ? undefined : { left: `${position.arrowLeft}px` };

  return (
    <TooltipViewerContext.Provider value={value}>
      {children}
      {request !== null && typeof document !== 'undefined'
        ? createPortal(
            <div
              ref={tipRef}
              id={request.id}
              role="tooltip"
              className={`tip tip-${request.tone ?? 'default'} tip-${position?.placement ?? request.preferred ?? 'bottom'}`}
              style={
                position === null
                  ? { top: 0, left: 0, visibility: 'hidden' }
                  : { top: `${position.top}px`, left: `${position.left}px`, visibility: 'visible' }
              }
            >
              <TooltipBody
                spec={request.spec}
                value={request.value ?? null}
                reasons={request.reasons ?? null}
                breakdown={request.breakdown ?? null}
                labels={request.breakdownLabels ?? undefined}
              />
              <span className="tip-arrow" style={arrowStyle} />
            </div>,
            document.body,
          )
        : null}
    </TooltipViewerContext.Provider>
  );
}
