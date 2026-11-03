import { useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * A container every chart lives in.
 *
 * Charts used to be loose blocks that either fitted the page or did not. Each one
 * now has the same three states everywhere — minimised, normal, full screen — and
 * the same controls, so a reader does not have to learn a different interaction
 * per panel.
 */
export function ChartPanel({
  title,
  meta,
  actions,
  children,
  defaultOpen = true,
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [full, setFull] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!full) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFull(false);
    };
    window.addEventListener('keydown', onKey);
    // The page must not scroll behind an overlay.
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [full]);

  return (
    <div className={`cp${full ? ' is-full' : ''}${open ? '' : ' is-min'}`} ref={wrapRef}>
      <div className="cp-head">
        <button
          type="button"
          className="cp-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          title={open ? 'minimise this chart' : 'expand this chart'}
        >
          <span className={`cp-caret${open ? ' is-open' : ''}`} aria-hidden="true">▸</span>
          <span className="cp-title">{title}</span>
        </button>
        {meta !== undefined && meta !== null && <span className="cp-meta">{meta}</span>}
        <span className="cp-controls">
          {actions}
          <button
            type="button"
            className="cp-btn"
            onClick={() => {
              setOpen(true);
              setFull((v) => !v);
            }}
            title={full ? 'back to the panel (Esc)' : 'fill the window with this chart'}
          >
            {full ? 'exit full' : 'full'}
          </button>
          <button
            type="button"
            className="cp-btn"
            onClick={() => {
              setFull(false);
              setOpen(false);
            }}
            title="minimise this chart"
          >
            min
          </button>
        </span>
      </div>
      {open && <div className="cp-body">{children}</div>}
    </div>
  );
}
