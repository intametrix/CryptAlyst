import { useState } from 'react';
import { TickerPicker } from './TickerPicker';
import { useResource } from '../hooks/useResource';

interface Dataset {
  symbol: string; interval: string; bars: number;
  from_ms: number; to_ms: number; bytes: number; file: string;
}

const INTERVALS = ['5s', '30s', '1m', '15m', '1h', '4h', '1d'];

function when(ms: number): string {
  if (!ms) return '—';
  const d = new Date(ms);
  return `${String(d.getDate()).padStart(2, '0')} ${d.toLocaleString('en-GB', { month: 'short' })} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function size(bytes: number): string {
  if (bytes > 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  if (bytes > 1 << 10) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

/**
 * What data exists locally, and a way to add more.
 *
 * Every forecast used to re-download its history. This is the inventory of what
 * has already been fetched — symbol, interval, how far back it reaches — so a
 * gap is visible and only the gap gets loaded.
 */
export function DataPanel({ symbol }: { symbol: string }) {
  // The inventory is a resource: one hook owns load, poll, abort and error.
  const inventory = useResource<{ datasets?: Dataset[]; root?: string }>('/api/data/inventory', { pollMs: 15000 });
  const rows = inventory.data?.datasets ?? [];
  const root = inventory.data?.root ?? '';
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState(symbol);
  const [interval, setInterval] = useState('1h');
  const [note, setNote] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);

  const ingest = async () => {
    const sym = draft.trim().toUpperCase();
    if (sym === '') return;
    setBusy(sym + interval);
    setNote(null);
    try {
      const q = new URLSearchParams({ symbol: sym, interval, bars: '1000' });
      const r = await fetch(`/api/data/load?${q.toString()}`, { method: 'POST', headers: { accept: 'application/json' } });
      const body = (await r.json()) as { ok: boolean; stored?: number; error?: string };
      setNote(body.ok ? `${sym} ${interval}: ${body.stored ?? 0} bars in the store` : (body.error ?? 'load failed'));
      inventory.reload();
    } catch (e) {
      setNote((e as Error).message);
    }
    setBusy(null);
  };

  const total = rows.reduce((n: number, r: Dataset) => n + r.bytes, 0);

  return (
    <div className="dp">
      <div className="dp-bar">
        <span className="dp-label">DATA ON DISK</span>
        <span className="dp-meta">{rows.length} datasets · {size(total)}</span>
        <span className="dp-meta">{root}</span>
      </div>

      <div className="dp-load">
        <TickerPicker value={draft} ariaLabel="symbol to load" onPick={(sym) => setDraft(sym)} />
        <select value={interval} onChange={(e) => setInterval(e.target.value)} aria-label="interval to load">
          {INTERVALS.map((i) => <option key={i} value={i}>{i}</option>)}
        </select>
        <button type="button" onClick={() => void ingest()} disabled={busy !== null}>
          {busy !== null ? 'loading…' : 'fetch 1000 bars'}
        </button>
        {note !== null && <span className="dp-note">{note}</span>}
      </div>

      {rows.length === 0 ? (
        <p className="fl-hint">nothing stored yet — fetch a symbol and interval above</p>
      ) : (
        <div className="dp-table">
          <div className="dp-row dp-head">
            <span>symbol</span><span>interval</span><span>bars</span><span>from</span><span>to</span><span>size</span>
          </div>
          {rows.map((r) => (
            <div className="dp-row" key={`${r.symbol}-${r.interval}`}>
              <span className="dp-sym">{r.symbol}</span>
              <span>{r.interval}</span>
              <span className="dp-num">{r.bars}</span>
              <span>{when(r.from_ms)}</span>
              <span>{when(r.to_ms)}</span>
              <span className="dp-num">{size(r.bytes)}</span>
              <button
                type="button"
                className={`dp-del${armed === `${r.symbol}-${r.interval}` ? ' is-armed' : ''}`}
                title={`delete ${r.symbol} ${r.interval} from the store`}
                onClick={() => {
                  const id = `${r.symbol}-${r.interval}`;
                  if (armed !== id) {
                    setArmed(id);
                    window.setTimeout(() => setArmed(null), 4000);
                    return;
                  }
                  setArmed(null);
                  void fetch(`/api/data/prune?symbol=${encodeURIComponent(r.symbol)}&interval=${encodeURIComponent(r.interval)}`, {
                    method: 'POST',
                  }).then(() => inventory.reload());
                }}
              >
                {armed === `${r.symbol}-${r.interval}` ? 'delete?' : '×'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
