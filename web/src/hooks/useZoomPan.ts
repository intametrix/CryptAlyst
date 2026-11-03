import { useCallback, useRef, useState } from 'react';

/**
 * Pan and zoom over an index range.
 *
 * The SVG charts had no navigation at all: a three-month view could not be
 * inspected without squinting. This keeps a visible window in index space and
 * returns the mapping, so the chart redraws the same geometry against a narrower
 * range. Wheel zooms around the cursor, drag pans, double-click fits.
 */
export interface ZoomPan {
  /** Visible window in index space. */
  d0: number;
  d1: number;
  zoomed: boolean;
  onWheel: (e: React.WheelEvent<SVGSVGElement>) => void;
  onPointerDown: (e: React.PointerEvent<SVGSVGElement>) => void;
  onPointerMove: (e: React.PointerEvent<SVGSVGElement>) => void;
  onPointerUp: (e: React.PointerEvent<SVGSVGElement>) => void;
  reset: () => void;
  /** Show the newest N points (a zoom preset). */
  show: (bars: number) => void;
}

export function useZoomPan(count: number, width: number): ZoomPan {
  const [range, setRange] = useState<{ d0: number; d1: number } | null>(null);
  const drag = useRef<{ x: number; d0: number; d1: number } | null>(null);

  const span = Math.max(count - 1, 1);
  const d0 = range === null ? 0 : range.d0;
  const d1 = range === null ? span : range.d1;
  const zoomed = range !== null;

  const clamp = useCallback(
    (a: number, b: number) => {
      const minSpan = Math.min(6, span);
      let lo = a;
      let hi = b;
      if (hi - lo < minSpan) {
        const mid = (lo + hi) / 2;
        lo = mid - minSpan / 2;
        hi = mid + minSpan / 2;
      }
      if (lo < 0) {
        hi -= lo;
        lo = 0;
      }
      if (hi > span) {
        lo -= hi - span;
        hi = span;
      }
      return { d0: Math.max(0, lo), d1: Math.min(span, hi) };
    },
    [span],
  );

  const onWheel = useCallback(
    (e: React.WheelEvent<SVGSVGElement>) => {
      if (count < 8) return;
      e.preventDefault();
      const rect = e.currentTarget.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / Math.max(rect.width, 1)));
      const at = d0 + frac * (d1 - d0);
      const factor = e.deltaY > 0 ? 1.25 : 0.8;
      const half = ((d1 - d0) / 2) * factor;
      setRange(clamp(at - half, at + half));
    },
    [count, d0, d1, clamp],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      drag.current = { x: e.clientX, d0, d1 };
      e.currentTarget.setPointerCapture?.(e.pointerId);
    },
    [d0, d1],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      const d = drag.current;
      if (d === null) return;
      const perPx = (d.d1 - d.d0) / Math.max(width, 1);
      const shift = (e.clientX - d.x) * perPx;
      setRange(clamp(d.d0 - shift, d.d1 - shift));
    },
    [width, clamp],
  );

  const onPointerUp = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  }, []);

  const reset = useCallback(() => setRange(null), []);

  const show = useCallback(
    (bars: number) => {
      if (bars <= 0 || bars >= count) {
        setRange(null);
        return;
      }
      setRange({ d0: Math.max(0, span - bars), d1: span });
    },
    [count, span],
  );

  return { d0, d1, zoomed, onWheel, onPointerDown, onPointerMove, onPointerUp, reset, show };
}
