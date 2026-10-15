import { useEffect, useRef, useState } from 'react';

/** Measured box of an element, in CSS pixels. */
export interface ElementSize {
  width: number;
  height: number;
}

const ZERO: ElementSize = { width: 0, height: 0 };

export interface UseElementSizeOptions {
  /**
   * Which box to observe. `content-box` (the default) measures the space a child
   * may actually occupy, excluding padding and border — which is what a canvas
   * wants, and what makes a mid-drag measurement honest.
   */
  box?: ResizeObserverBoxOptions;
  /**
   * Coalesce notifications to at most one per animation frame. A panel drag fires
   * dozens of observer callbacks per second; without this, every one would
   * re-render and re-resize the chart, which is exactly the jank to avoid.
   */
  throttleToFrame?: boolean;
}

/**
 * Measure an element with a `ResizeObserver`, not a window `resize` listener.
 *
 * This is what makes children react to their *container*: a chart inside a
 * dragged box gets a new `width`/`height` here and repaints even though the
 * viewport never changed. Media queries are blind to it.
 *
 * The measurement is taken from `getBoundingClientRect()` (the observer's own
 * `contentRect` is a *layout* box, i.e. unscaled, so it disagrees with the canvas
 * under a CSS transform) and rounded to whole pixels, so a fractional layout
 * cannot feed an endless re-render loop.
 *
 * Returns `{0,0}` when there is no `ResizeObserver` rather than throwing.
 */
export function useElementSize<T extends HTMLElement = HTMLDivElement>(
  options: UseElementSizeOptions = {},
): {
  ref: React.RefObject<T | null>;
  size: ElementSize;
} {
  const { box = 'content-box', throttleToFrame = true } = options;
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState<ElementSize>(ZERO);

  useEffect(() => {
    const node = ref.current;
    if (node === null || typeof ResizeObserver === 'undefined') return;

    let frame: number | null = null;
    let last = { width: -1, height: -1 };

    const apply = (): void => {
      frame = null;
      const rect = node.getBoundingClientRect();
      const width = Math.max(0, Math.round(rect.width));
      const height = Math.max(0, Math.round(rect.height));
      if (width === last.width && height === last.height) return;
      last = { width, height };
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    };

    const measure = (): void => {
      if (!throttleToFrame) {
        apply();
        return;
      }
      if (frame !== null) return;
      frame = window.requestAnimationFrame(apply);
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node, { box });
    return () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [box, throttleToFrame]);

  return { ref, size };
}
