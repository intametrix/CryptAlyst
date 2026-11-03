import { useEffect, useState } from 'react';

/**
 * One clock for the whole app.
 *
 * Every "3s ago" label needs the current time, and a timer per label would mean
 * dozens of intervals all waking on the same second. This keeps a single
 * interval, started when the first subscriber arrives and stopped when the last
 * one leaves, and re-renders each subscriber only when the second actually
 * changes.
 */
type Listener = (now: number) => void;

const listeners = new Set<Listener>();
let timer: number | null = null;
let current = Date.now();

function start(): void {
  if (timer !== null) return;
  timer = window.setInterval(() => {
    current = Date.now();
    for (const l of listeners) l(current);
  }, 1000);
}

function stop(): void {
  if (timer === null) return;
  window.clearInterval(timer);
  timer = null;
}

/** Subscribe to a once-per-second clock. */
export function useNow(): number {
  const [now, setNow] = useState(current);
  useEffect(() => {
    const l: Listener = (t) => setNow((prev) => (t > prev ? t : prev));
    listeners.add(l);
    start();
    return () => {
      listeners.delete(l);
      if (listeners.size === 0) stop();
    };
  }, []);
  return now;
}
