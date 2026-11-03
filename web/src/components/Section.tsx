import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ErrorBoundary } from '../ErrorBoundary';

const STORE_PREFIX = 'cryptalas.section.';

/**
 * A collapsible section with a remembered open/closed state.
 *
 * Every section on the forecast workspace is one of these: the page is dense,
 * and a reader should be able to fold away the parts they are not using without
 * losing the parts they are. The state persists per `id`, so a section that was
 * folded away stays folded across reloads.
 */
export function Section({
  id,
  title,
  meta,
  actions,
  defaultOpen = true,
  forceOpen,
  children,
}: {
  id: string;
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  defaultOpen?: boolean;
  /** Bump this to force the section open (e.g. when a result arrives). */
  forceOpen?: number;
  children: ReactNode;
}) {
  const [open, setOpen] = useState<boolean>(() => {
    if (typeof window === 'undefined') return defaultOpen;
    try {
      const raw = window.localStorage.getItem(STORE_PREFIX + id);
      return raw === null ? defaultOpen : raw === '1';
    } catch {
      return defaultOpen;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(STORE_PREFIX + id, open ? '1' : '0');
    } catch {
      /* a disabled localStorage must not break the panel */
    }
  }, [id, open]);

  /** Height in pixels, null while the section sizes itself to its content. */
  const [height, setHeight] = useState<number | null>(() => {
    if (typeof window === 'undefined') return null;
    try {
      const raw = window.localStorage.getItem(STORE_PREFIX + id + '.h');
      const n = raw === null ? 0 : Number(raw);
      return Number.isFinite(n) && n > 0 ? n : null;
    } catch {
      return null;
    }
  });
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  // A result arriving must reveal the section that holds it, even if the user
  // had folded it away earlier.
  const forceRef = useRef(forceOpen);
  useEffect(() => {
    if (forceOpen === undefined || forceOpen === forceRef.current) return;
    forceRef.current = forceOpen;
    setOpen(true);
  }, [forceOpen]);

  const toggle = useCallback(() => setOpen((v) => !v), []);

  const onGripDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const body = (e.currentTarget.parentElement as HTMLElement | null);
      dragRef.current = { startY: e.clientY, startH: body?.offsetHeight ?? 240 };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [],
  );

  const onGripMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const d = dragRef.current;
      if (!d) return;
      const next = Math.max(120, Math.min(1600, d.startH + (e.clientY - d.startY)));
      setHeight(next);
    },
    [],
  );

  const onGripUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragRef.current) return;
      dragRef.current = null;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* the pointer may already be gone */
      }
      setHeight((h) => {
        try {
          if (h !== null) window.localStorage.setItem(STORE_PREFIX + id + '.h', String(h));
          else window.localStorage.removeItem(STORE_PREFIX + id + '.h');
        } catch {
          /* ignore */
        }
        return h;
      });
    },
    [id],
  );

  return (
    <section className={`sec${open ? ' is-open' : ''}`}>
      <header className="sec-head">
        <button
          type="button"
          className="sec-toggle"
          aria-expanded={open}
          aria-controls={`sec-body-${id}`}
          onClick={toggle}
        >
          <span className={`sec-caret${open ? ' is-open' : ''}`} aria-hidden="true">
            ▸
          </span>
          <span className="sec-title">{title}</span>
        </button>
        {meta !== undefined && meta !== null && <div className="sec-meta">{meta}</div>}
        {actions !== undefined && actions !== null && <div className="sec-actions">{actions}</div>}
      </header>
      {open && (
        <>
          <div
            className="sec-body"
            id={`sec-body-${id}`}
            style={height !== null ? { height: `${height}px`, overflowY: 'auto' } : undefined}
          >
            {/* One panel failing must not blank the workspace, and the message
                has to name the panel so the failure is actionable. */}
            <ErrorBoundary where={id}>
              {children}
            </ErrorBoundary>
          </div>
          <div
            className="sec-grip"
            role="separator"
            aria-orientation="horizontal"
            aria-label={`resize ${title}`}
            title="drag to resize this section"
            onPointerDown={onGripDown}
            onPointerMove={onGripMove}
            onPointerUp={onGripUp}
          />
        </>
      )}
    </section>
  );
}
