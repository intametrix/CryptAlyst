import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getJSON } from '../data/client';

interface UniverseRow {
  symbol: string;
  last_price: number;
  change_24h_pct: number;
  quote_volume_24h: number;
}

interface InstrumentRow {
  symbol: string;
  name: string;
  class: string;
  status: string;
}

export interface Ticker {
  symbol: string;
  base: string;
  quote: string;
  /** Reference-data name, when the registry knows this instrument. */
  name: string;
  /** Reference-data class: crypto_spot, crypto_perp, … */
  klass: string;
  price: number | null;
  change: number | null;
}

const QUOTES = ['USDT', 'USDC', 'BUSD', 'BTC', 'ETH', 'BNB', 'FDUSD', 'TUSD'];
const RECENT_KEY = 'cryptalas.recentTickers';
const MAX_ROWS = 16;

/** Split BTCUSDT into base BTC and quote USDT. */
export function splitSymbol(symbol: string): { base: string; quote: string } {
  for (const q of QUOTES) {
    if (symbol.length > q.length && symbol.endsWith(q)) {
      return { base: symbol.slice(0, -q.length), quote: q };
    }
  }
  return { base: symbol, quote: '' };
}

function readRecents(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    const arr = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string').slice(0, 8) : [];
  } catch {
    return [];
  }
}

function writeRecent(symbol: string): void {
  try {
    const next = [symbol, ...readRecents().filter((s) => s !== symbol)].slice(0, 8);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* a full or disabled localStorage must not break the picker */
  }
}

/**
 * Ticker picker.
 *
 * Ported from the chart lab's symbol control (`scalp5/chart_real.html`): type to
 * filter, arrow keys to move, Enter to take the highlighted row, Escape to
 * close. Rows show the base/quote split, the last price and the 24h change so a
 * ticker can be chosen by looking rather than by remembering exact pairs.
 *
 * Recent picks are remembered, so the second visit needs no typing.
 */
export function TickerPicker({
  value,
  onPick,
  ariaLabel = 'ticker',
}: {
  value: string;
  onPick: (symbol: string) => void;
  ariaLabel?: string;
}) {
  const [universe, setUniverse] = useState<Ticker[]>([]);
  const [draft, setDraft] = useState(value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  /**
   * True while the box has just been opened and nothing has been typed yet.
   * Without this the pre-filled symbol became the search query, so clicking the
   * box listed only symbols matching itself instead of everything available.
   */
  const [browsing, setBrowsing] = useState(false);
  /**
   * Timestamp of the last pick. A blur that follows a pick must never commit the
   * typed text — that is what turned a chosen WIFUSDT into a typed "WIF".
   */
  const pickedAtRef = useRef(0);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => setDraft(value), [value]);

  useEffect(() => {
    let dead = false;
    const load = async () => {
      // Identity comes from the security reference data service; prices come
      // from the market feed. Joining them here keeps each service honest about
      // what it owns instead of duplicating a symbol list.
      const [refRes, mktRes] = await Promise.allSettled([
        getJSON<{ instruments?: InstrumentRow[] }>('/api/instruments?limit=4000'),
        getJSON<{ symbols?: UniverseRow[] }>('/api/universe'),
      ]);

      // Identity and price are separate services; either may be down, and the picker
      // still has to open with what it got.
      const instruments: InstrumentRow[] =
        refRes.status === 'fulfilled' ? (refRes.value.instruments ?? []) : [];
      const prices = new Map<string, UniverseRow>(
        mktRes.status === 'fulfilled' ? (mktRes.value.symbols ?? []).map((r: UniverseRow) => [r.symbol, r] as const) : [],
      );
      if (dead) return;

      let rows: Ticker[];
      if (instruments.length > 0) {
        rows = instruments.map((r) => {
          const { base, quote } = splitSymbol(r.symbol);
          const m = prices.get(r.symbol);
          return {
            symbol: r.symbol,
            base,
            quote,
            name: r.name ?? '',
            klass: r.class ?? '',
            price: m?.last_price ?? null,
            change: m?.change_24h_pct ?? null,
          };
        });
      } else {
        // Reference data unreachable: fall back to the market universe so the
        // picker still works rather than showing an empty list.
        rows = (Array.from(prices.values()) as UniverseRow[]).map((r) => {
          const { base, quote } = splitSymbol(r.symbol);
          return {
            symbol: r.symbol,
            base,
            quote,
            name: '',
            klass: '',
            price: r.last_price ?? null,
            change: r.change_24h_pct ?? null,
          };
        });
      }
      // Liquidity order: the most traded instruments first.
      rows.sort((a, b) => (prices.get(b.symbol)?.quote_volume_24h ?? 0) - (prices.get(a.symbol)?.quote_volume_24h ?? 0));
      setUniverse(rows);
    };
    void load();
    return () => {
      dead = true;
    };
  }, []);

  // Close on an outside press, like any dropdown.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const hits = useMemo(() => {
    const q = browsing ? '' : draft.trim().toUpperCase();
    const seen = new Set<string>();
    const out: Ticker[] = [];
    const push = (t: Ticker) => {
      if (seen.has(t.symbol)) return;
      seen.add(t.symbol);
      out.push(t);
    };
    if (q === '') {
      for (const s of readRecents()) {
        const hit = universe.find((t) => t.symbol === s);
        if (hit) push(hit);
      }
    }
    for (const t of universe) {
      if (out.length >= MAX_ROWS) break;
      if (q !== '' && !t.symbol.includes(q) && !t.base.includes(q)) continue;
      push(t);
    }
    return out.slice(0, MAX_ROWS);
  }, [draft, universe, browsing]);

  const choose = useCallback(
    (t: Ticker) => {
      pickedAtRef.current = Date.now();
      writeRecent(t.symbol);
      setDraft(t.symbol);
      setOpen(false);
      setActive(-1);
      onPick(t.symbol);
    },
    [onPick],
  );

  const commit = useCallback(
    (raw: string) => {
      const next = raw.trim().toUpperCase();
      if (next === '') return;
      writeRecent(next);
      onPick(next);
    },
    [onPick],
  );

  return (
    <div className="tp-wrap" ref={wrapRef}>
      <input
        className="tp-input"
        value={draft}
        aria-label={ariaLabel}
        autoComplete="off"
        spellCheck={false}
        placeholder="BTCUSDT"
        onChange={(e) => {
          setDraft(e.target.value.toUpperCase());
          setBrowsing(false);
          setOpen(true);
          setActive(-1);
        }}
        onClick={(e) => {
          // Clicking an input that already has focus fires no focus event, so a
          // second click after a pick did nothing and the list never reopened.
          e.currentTarget.select();
          setBrowsing(true);
          setOpen(true);
        }}
        onFocus={(e) => {
          // The current symbol is fully selected on click, so typing replaces it
          // instead of appending to it, and the list opens showing everything.
          e.currentTarget.select();
          setBrowsing(true);
          setOpen(true);
        }}
        onBlur={() => {
          // Commit free text, but let a click on a row win first and never
          // commit the same choice twice.
          window.setTimeout(() => {
            // A pick in the last 700ms owns the outcome; the typed text loses.
            if (Date.now() - pickedAtRef.current < 700) return;
            commit(draft);
          }, 150);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!open) {
              setOpen(true);
              return;
            }
            if (hits.length === 0) return;
            const dir = e.key === 'ArrowDown' ? 1 : -1;
            setActive((i) => {
              const next = i + dir;
              if (next < 0) return hits.length - 1;
              if (next >= hits.length) return 0;
              return next;
            });
            return;
          }
          if (e.key === 'Enter') {
            e.preventDefault();
            // A valid typed symbol commits immediately: the UI reflects the
            // choice on Enter, whether or not a row is highlighted.
            const typed = draft.trim().toUpperCase();
            if (open && active >= 0 && hits[active]) {
              choose(hits[active]);
            } else if (/^[A-Z0-9]{2,20}$/.test(typed)) {
              pickedAtRef.current = Date.now();
              setOpen(false);
              setBrowsing(false);
              setActive(-1);
              setDraft(typed);
              writeRecent(typed);
              onPick(typed);
            } else {
              setOpen(false);
              commit(draft);
            }
            return;
          }
          if (e.key === 'Escape') {
            setOpen(false);
            setBrowsing(false);
            setActive(-1);
          }
        }}
      />

      {open && (
        <div className="tp-list" role="listbox">
          {hits.length === 0 ? (
            <div className="tp-empty">
              {draft.trim() === '' ? 'loading tickers…' : `no match for “${draft}”`}
            </div>
          ) : (
            <>
              {(browsing || draft.trim() === '') && (
                <div className="tp-hint">
                  recent + top {hits.length} of {universe.length} by liquidity — type to search
                </div>
              )}
              {hits.map((t, i) => (
                <div
                  key={t.symbol}
                  role="option"
                  aria-selected={i === active}
                  className={`tp-row${i === active ? ' is-active' : ''}`}
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    choose(t);
                  }}
                  onClick={(e) => {
                    e.preventDefault();
                    choose(t);
                  }}
                >
                  <span className="tp-sym">
                    <b>{t.base}</b>
                    {t.quote && <span className="tp-quote">/{t.quote}</span>}
                    {t.name && <span className="tp-name">{t.name}</span>}
                  </span>
                  <span className="tp-price">{t.price != null ? t.price.toPrecision(7) : ''}</span>
                  <span className={`tp-chg ${(t.change ?? 0) >= 0 ? 'up' : 'down'}`}>
                    {(t.change ?? 0) >= 0 ? '+' : ''}
                    {(t.change ?? 0).toFixed(1)}%
                  </span>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
