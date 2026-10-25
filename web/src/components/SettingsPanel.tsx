import { memo, useCallback, useEffect, useMemo, useState } from 'react';

/** GET /api/settings — live detection/universe thresholds. */
export interface Settings {
  screen_min_move: number;
  screen_min_vol_ratio: number;
  screen_min_vol_z: number;
  screen_min_recent_usd: number;
  screen_min_quote_volume: number;
  screen_top_n: number;
  min_liq: number;
  max_symbols: number;
}

interface Field {
  key: keyof Settings;
  label: string;
  hint: string;
  step: string;
}

const GROUPS: { title: string; fields: Field[] }[] = [
  {
    title: 'Pump / Dump screen',
    fields: [
      { key: 'screen_min_move', label: 'min 5m move %', hint: 'lower = more / faster signals', step: 'any' },
      { key: 'screen_min_vol_ratio', label: 'min volume ratio', hint: '', step: 'any' },
      { key: 'screen_min_vol_z', label: 'min volume z-score', hint: '', step: 'any' },
      { key: 'screen_min_recent_usd', label: 'min recent USD flow', hint: '', step: 'any' },
      { key: 'screen_min_quote_volume', label: 'min 24h quote volume', hint: 'lower = include smaller pairs', step: 'any' },
      { key: 'screen_top_n', label: 'symbols to screen', hint: '', step: '1' },
    ],
  },
  {
    title: 'Universe',
    fields: [
      { key: 'min_liq', label: 'min 24h volume to track', hint: '', step: 'any' },
      { key: 'max_symbols', label: 'max tracked symbols', hint: '', step: '1' },
    ],
  },
];

/**
 * Settings — the live-tunable pump/dump + universe thresholds. Every field is
 * applied immediately via POST /api/settings/apply; no restart.
 */
export const SettingsPanel = memo(function SettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/settings', { headers: { accept: 'application/json' } });
      if (!res.ok) throw new Error(`GET /api/settings ${res.status}`);
      const body = (await res.json()) as Settings;
      setSettings(body);
      setDraft(Object.fromEntries(Object.entries(body).map(([k, v]) => [k, String(v)])));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => {
    if (!settings) return false;
    return Object.entries(settings).some(([k, v]) => draft[k] != null && Number(draft[k]) !== v);
  }, [settings, draft]);

  const apply = useCallback(async () => {
    if (!settings) return;
    const body: Record<string, number> = {};
    for (const [k, v] of Object.entries(draft)) {
      const n = Number(v);
      if (v !== '' && Number.isFinite(n)) body[k] = n;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch('/api/settings/apply', {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`POST /api/settings/apply ${res.status}`);
      const next = (await res.json()) as Settings;
      setSettings(next);
      setDraft(Object.fromEntries(Object.entries(next).map(([k, v]) => [k, String(v)])));
      setApplied(true);
      window.setTimeout(() => setApplied(false), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [draft, settings]);

  return (
    <section className="panel settings">
      <div className="panel-head">
        <h2>settings</h2>
        <span className="sub">
          detection + universe thresholds · apply immediately, no restart
        </span>
      </div>

      {error != null && <p className="empty">settings api: {error}</p>}
      {settings === null && error == null && <p className="empty">loading settings…</p>}

      {settings !== null && (
        <div className="settings-groups">
          {GROUPS.map((group) => (
            <div key={group.title} className="settings-group">
              <div className="settings-group-title">{group.title}</div>
              {group.fields.map((f) => (
                <label key={f.key} className="settings-field">
                  <span className="settings-label">{f.label}</span>
                  <input
                    type="number"
                    className="settings-input"
                    step={f.step}
                    value={draft[f.key] ?? ''}
                    onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                  />
                  {f.hint && <span className="settings-hint">{f.hint}</span>}
                </label>
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="settings-actions">
        <button type="button" className="btn" onClick={load} disabled={busy}>
          reload
        </button>
        <button type="button" className="btn primary" onClick={apply} disabled={busy || !dirty}>
          {busy ? 'applying…' : applied ? 'applied ✓' : 'Apply'}
        </button>
      </div>
    </section>
  );
});
