import { useEffect, useState } from 'react';

/**
 * The 3-second rule, applied to the whole UI.
 *
 * Tooltip already waits, but most controls carry a plain `title` attribute, which
 * the browser shows instantly and which no component can slow down. This takes
 * those titles away on hover, waits, and draws its own bubble — so every control
 * behaves the same way without editing a hundred of them.
 */
const DELAY_MS = 3000;

interface Shown {
  text: string;
  x: number;
  y: number;
}

export function DelayedTitles() {
  const [shown, setShown] = useState<Shown | null>(null);

  useEffect(() => {
    let timer: number | null = null;
    let host: HTMLElement | null = null;
    let saved: string | null = null;

    const clear = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      if (host !== null && saved !== null) {
        // Put the title back for the next hover — and for screen readers and the
        // native fallback if this layer ever stops running.
        host.setAttribute('title', saved);
      }
      host = null;
      saved = null;
      setShown(null);
    };

    const onOver = (e: PointerEvent) => {
      const el = (e.target as HTMLElement | null)?.closest('[title]') as HTMLElement | null;
      if (el === null) return;
      // Tooltip has its own, richer bubble; do not stack two.
      if (el.closest('.tip-trigger') !== null) return;
      if (el === host) return;
      clear();
      const text = el.getAttribute('title');
      if (text === null || text.trim() === '') return;
      host = el;
      saved = text;
      el.removeAttribute('title');
      timer = window.setTimeout(() => {
        const r = el.getBoundingClientRect();
        setShown({ text, x: r.left + r.width / 2, y: r.bottom + 6 });
      }, DELAY_MS);
    };

    document.addEventListener('pointerover', onOver);
    document.addEventListener('pointerout', clear);
    window.addEventListener('scroll', clear, true);
    document.addEventListener('keydown', clear);
    return () => {
      document.removeEventListener('pointerover', onOver);
      document.removeEventListener('pointerout', clear);
      window.removeEventListener('scroll', clear, true);
      document.removeEventListener('keydown', clear);
      clear();
    };
  }, []);

  if (shown === null) return null;
  return (
    <div
      className="dtip"
      role="tooltip"
      style={{ left: `${Math.round(shown.x)}px`, top: `${Math.round(shown.y)}px` }}
    >
      {shown.text}
    </div>
  );
}
