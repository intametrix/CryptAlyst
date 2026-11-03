import { useEffect, useState } from 'react';

/**
 * P(continuation) per symbol.
 *
 * Keyed by symbol rather than by alert id on purpose: the feed shows alerts that
 * arrived seconds ago, while the scorer's snapshot is taken on a timer, so an
 * exact (symbol, timestamp) match almost never hits — which is why the badge was
 * invisible. The probability describes the symbol's state, so the latest score
 * for that symbol is the right value for a fresh alert on it too.
 */
interface ScoredAlert {
  symbol: string;
  ts_ms: number;
  continuation: number;
}

const scores = new Map<string, number>();
const listeners = new Set<() => void>();
let started = false;

function publish(next: Map<string, number>): void {
  scores.clear();
  for (const [k, v] of next) scores.set(k, v);
  for (const l of listeners) l();
}

async function loadScores(): Promise<void> {
  try {
    const res = await fetch('/api/alerts/scored?limit=120', { headers: { accept: 'application/json' } });
    if (!res.ok) return;
    const body = (await res.json()) as { alerts?: ScoredAlert[] };
    const next = new Map<string, number>();
    for (const a of body.alerts ?? []) {
      if (typeof a.continuation === 'number' && a.continuation >= 0) {
        // Newest wins: the endpoint returns newest first.
        if (!next.has(a.symbol)) next.set(a.symbol, a.continuation);
      }
    }
    publish(next);
  } catch {
    /* the feed keeps working without the score */
  }
}

/** Start polling once for the lifetime of the page. */
export function useContinuationPoller(enabled = true): void {
  useEffect(() => {
    if (!enabled || started) return;
    started = true;
    void loadScores();
    window.setInterval(() => void loadScores(), 15_000);
  }, [enabled]);
}

/** The probability for one symbol, or undefined when it has not been scored. */
export function useContinuationValue(symbol: string | null | undefined): number | undefined {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const l = () => setTick((v) => v + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  void tick;
  if (!symbol) return undefined;
  return scores.get(symbol);
}
