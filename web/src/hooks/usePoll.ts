import { useEffect, useRef } from 'react';

/**
 * One polling loop, declared once.
 *
 * The callback is kept in a ref so the interval is created once and still calls
 * current state — several hand-rolled timers captured a stale closure and kept
 * polling old data while looking healthy.
 */
export function usePoll(
  fn: () => void | Promise<void>,
  ms: number,
  options: { enabled?: boolean; immediate?: boolean } = {},
): void {
  const { enabled = true, immediate = false } = options;
  const sink = useRef(fn);
  sink.current = fn;

  useEffect(() => {
    if (!enabled || ms <= 0) return undefined;
    if (immediate) void sink.current();
    const id = window.setInterval(() => {
      void sink.current();
    }, ms);
    return () => window.clearInterval(id);
  }, [enabled, ms, immediate]);
}
