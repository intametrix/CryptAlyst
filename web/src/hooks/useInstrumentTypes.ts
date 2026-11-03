import { useEffect, useState } from 'react';

/**
 * What kind of instrument a symbol is: a USDT-margined perpetual or spot.
 *
 * The venue treats them differently (funding, liquidation, borrow), and a list
 * that mixes them without saying so is misleading. The reference data is fetched
 * once and shared, because it changes rarely and every list wants the same map.
 */
export interface InstrumentType {
  cls: string;
  name: string;
}

const cache = new Map<string, InstrumentType>();
const listeners = new Set<() => void>();
let started = false;
let loaded = false;

function publish(next: Map<string, InstrumentType>): void {
  cache.clear();
  for (const [k, v] of next) cache.set(k, v);
  loaded = true;
  for (const l of listeners) l();
}

async function loadTypes(): Promise<void> {
  try {
    const res = await fetch('/api/instruments?limit=4000', { headers: { accept: 'application/json' } });
    if (!res.ok) return;
    const body = (await res.json()) as { instruments?: { symbol?: string; class?: string; name?: string }[] };
    const next = new Map<string, InstrumentType>();
    for (const row of body.instruments ?? []) {
      if (typeof row.symbol === 'string' && row.symbol !== '') {
        next.set(row.symbol, { cls: row.class ?? '', name: row.name ?? '' });
      }
    }
    publish(next);
  } catch {
    /* the lists still render without the type */
  }
}

/** Short label: PERP, SPOT, or empty when unknown. */
export function typeLabel(cls: string | undefined): string {
  if (cls === 'crypto_perp') return 'PERP';
  if (cls === 'crypto_spot') return 'SPOT';
  return '';
}

/** The type for one symbol, subscribed to the shared map. */
export function useInstrumentType(symbol: string | null | undefined): InstrumentType | undefined {
  const [, force] = useState(0);
  useEffect(() => {
    if (!started) {
      started = true;
      void loadTypes();
    }
    const l = () => force((v) => v + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  if (!symbol) return undefined;
  return cache.get(symbol);
}

/** True once the reference data has arrived, so a list can avoid flicker. */
export function useInstrumentTypesReady(): boolean {
  const [, force] = useState(0);
  useEffect(() => {
    if (!started) {
      started = true;
      void loadTypes();
    }
    const l = () => force((v) => v + 1);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return loaded;
}
