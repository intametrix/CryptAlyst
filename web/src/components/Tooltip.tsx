import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { hasDetail, resolveTooltip, type TooltipSource, type TooltipSpec } from '../glossary';
import { onLayoutReset } from '../layout';
import type { ToolsMsg } from '../types';
import { computePlacement, type TipPosition } from './tooltipPlacement';
import { useTooltipViewer } from './TooltipViewer';

/** Which visual flavour the tooltip takes. */
export type TooltipTone = 'default' | 'chip' | 'header' | 'stat' | 'badge' | 'heat';

export interface TooltipProps {
  /** `/api/tools` lookups, tried in order. */
  keys: readonly string[];
  /** The cached glossary (may be `null` while offline). */
  tools: ToolsMsg | null;
  /** Nicer last-resort title than the raw key. */
  fallbackTitle?: string;
  /** Optional secondary line, e.g. a chip's one-liner. */
  label?: string | null;
  /** Optional current value, shown as a "now" row. */
  value?: string | null;
  /** Optional "why" list — used by the heat glow to explain itself. */
  reasons?: readonly string[] | null;
  /** Optional 0-1 component breakdown, rendered as small bars. */
  breakdown?: Readonly<Record<string, number>> | null;
  /** Optional display names for the breakdown keys. */
  breakdownLabels?: BreakdownLabels | null;
  tone?: TooltipTone;
  /** Extra class on the wrapper. */
  className?: string;
  /** Tooltip placement preference; it flips automatically when it does not fit. */
  preferred?: 'top' | 'bottom';
  children: React.ReactNode;
}

const SOURCE_NOTE: Record<TooltipSource, string> = {
  api: 'from /api/tools',
  local: 'local copy (backend glossary unavailable)',
  key: 'no description available',
};

export interface BreakdownLabels {
  [key: string]: string;
}

/** The rich content of a tooltip: title, meaning, reading, units, value, why. */
export function TooltipBody({
  spec,
  value,
  reasons = null,
  breakdown = null,
  labels,
}: {
  spec: TooltipSpec;
  value: string | null;
  reasons?: readonly string[] | null;
  breakdown?: Readonly<Record<string, number>> | null;
  /** Optional display names for breakdown keys. */
  labels?: BreakdownLabels;
}) {
  return (
    <>
      <div className="tip-title">{spec.title}</div>
      {spec.label !== null && <div className="tip-label">{spec.label}</div>}
      {value !== null && value !== '' && (
        <div className="tip-section tip-now">
          <span className="tip-key">now</span>
          <span className="tip-text num">{value}</span>
        </div>
      )}
      {spec.formula !== null && (
        <div className="tip-section">
          <span className="tip-key">formula</span>
          <span className="tip-text">{spec.formula}</span>
        </div>
      )}
      {spec.meaning !== null && (
        <div className="tip-section">
          <span className="tip-key">meaning</span>
          <span className="tip-text">{spec.meaning}</span>
        </div>
      )}
      {spec.reading !== null && (
        <div className="tip-section">
          <span className="tip-key">reading</span>
          <span className="tip-text">{spec.reading}</span>
        </div>
      )}
      {spec.units !== null && (
        <div className="tip-section">
          <span className="tip-key">units</span>
          <span className="tip-text num">{spec.units}</span>
        </div>
      )}
      {reasons !== null && reasons.length > 0 && (
        <div className="tip-section">
          <span className="tip-key">why</span>
          <ul className="tip-reasons">
            {reasons.map((reason, i) => (
              <li key={`${i}-${reason}`}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      {breakdown !== null && Object.keys(breakdown).length > 0 && (
        <div className="tip-section">
          <span className="tip-key">mix</span>
          <span className="tip-heat">
            {Object.entries(breakdown).map(([key, raw]) => {
              const pct = Math.max(0, Math.min(1, raw)) * 100;
              return (
                <span className="tip-heat-row" key={key}>
                  <span className="tip-heat-key">{labels?.[key] ?? key}</span>
                  <span className="tip-heat-bar" aria-hidden="true">
                    <span className="tip-heat-fill" style={{ width: `${pct}%` }} />
                  </span>
                  <span className="tip-heat-value num">{pct.toFixed(0)}%</span>
                </span>
              );
            })}
          </span>
        </div>
      )}
      {!hasDetail(spec) && (
        <div className="tip-section">
          <span className="tip-text tip-muted">No glossary entry for this key — hover context only.</span>
        </div>
      )}
      <div className="tip-source">{SOURCE_NOTE[spec.source]}</div>
    </>
  );
}

/**
 * The dashboard's tooltip trigger.
 *
 * Inside a `<TooltipViewer>` (always true in the app) it publishes content to
 * that single shared surface, so hundreds of triggers cost nothing until they
 * are hovered. If no viewer is mounted — an isolated render, or a future
 * embed — it falls back to rendering its own portal, so the component is still
 * self-contained.
 *
 * Either way the behaviour is the same:
 * - opens on **mouse hover and keyboard focus** (the trigger is focusable)
 * - rich content from `/api/tools`, then local copy, then the raw key
 * - never overflows the viewport, and re-anchors when a container is resized
 * - `role="tooltip"` wired with `aria-describedby`
 */
export function Tooltip({
  keys,
  tools,
  fallbackTitle,
  label = null,
  value = null,
  reasons = null,
  breakdown = null,
  breakdownLabels = null,
  tone = 'default',
  className,
  preferred = 'bottom',
  children,
}: TooltipProps) {
  const viewer = useTooltipViewer();
  const generatedId = useId();
  const id = useMemo(() => `tip-${generatedId.replace(/:/g, '')}`, [generatedId]);

  const spec = useMemo(
    () => resolveTooltip(keys, tools, fallbackTitle, label),
    [keys, tools, fallbackTitle, label],
  );

  if (viewer !== null) {
    return (
      <span
        className={`tip-trigger${className !== undefined ? ` ${className}` : ''}`}
        aria-describedby={viewer.activeId === id ? id : undefined}
        onPointerEnter={(event) => {
          viewer.show({
            spec,
            value,
            reasons,
            breakdown,
            breakdownLabels,
            tone,
            preferred,
            anchor: event.currentTarget,
            id,
          });
        }}
        onPointerLeave={() => viewer.hide(id)}
        onFocus={(event) => {
          viewer.show({
            spec,
            value,
            reasons,
            breakdown,
            breakdownLabels,
            tone,
            preferred,
            anchor: event.currentTarget,
            id,
          });
        }}
        onBlur={() => viewer.hide(id)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') viewer.hide(id);
        }}
      >
        {children}
      </span>
    );
  }

  return (
    <StandaloneTooltip
      id={id}
      spec={spec}
      value={value}
      reasons={reasons}
      breakdown={breakdown}
      breakdownLabels={breakdownLabels}
      tone={tone}
      preferred={preferred}
      className={className}
    >
      {children}
    </StandaloneTooltip>
  );
}

/** The no-viewer fallback: owns its own portal, position, and listeners. */
function StandaloneTooltip({
  id,
  spec,
  value,
  reasons,
  breakdown,
  breakdownLabels,
  tone,
  preferred,
  className,
  children,
}: {
  id: string;
  spec: TooltipSpec;
  value: string | null;
  reasons: readonly string[] | null;
  breakdown: Readonly<Record<string, number>> | null;
  breakdownLabels: BreakdownLabels | null;
  tone: TooltipTone;
  preferred: 'top' | 'bottom';
  className?: string;
  children: React.ReactNode;
}) {
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<TipPosition | null>(null);

  const place = useCallback(() => {
    const trigger = triggerRef.current;
    const tip = tipRef.current;
    if (trigger === null || tip === null) return;
    const t = trigger.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    setPosition(
      computePlacement({
        trigger: { top: t.top, left: t.left, width: t.width, height: t.height },
        tip: { width: tipRect.width, height: tipRect.height },
        viewport: { width: window.innerWidth, height: window.innerHeight },
        preferred,
      }),
    );
  }, [preferred]);

  const close = useCallback(() => {
    setOpen(false);
    setPosition(null);
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    place();
  }, [open, place, spec, value]);

  useEffect(() => {
    if (!open) return;
    const onScrollOrResize = (): void => place();
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => place());
      observer.observe(document.documentElement);
      if (triggerRef.current !== null) observer.observe(triggerRef.current);
    }
    const offReset = onLayoutReset(place);

    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
      window.removeEventListener('keydown', onKey);
      observer?.disconnect();
      offReset();
    };
  }, [open, place, close]);

  const arrowStyle = position === null ? undefined : { left: `${position.arrowLeft}px` };

  return (
    <span
      ref={triggerRef}
      className={`tip-trigger${className !== undefined ? ` ${className}` : ''}`}
      aria-describedby={open ? id : undefined}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={close}
      onFocus={() => setOpen(true)}
      onBlur={close}
    >
      {children}
      {open && typeof document !== 'undefined'
        ? createPortal(
            <div
              ref={tipRef}
              id={id}
              role="tooltip"
              className={`tip tip-${tone} tip-${position?.placement ?? preferred}`}
              style={
                position === null
                  ? { top: 0, left: 0, visibility: 'hidden' }
                  : { top: `${position.top}px`, left: `${position.left}px`, visibility: 'visible' }
              }
            >
              <TooltipBody
                spec={spec}
                value={value}
                reasons={reasons}
                breakdown={breakdown}
                labels={breakdownLabels ?? undefined}
              />
              <span className="tip-arrow" style={arrowStyle} />
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
