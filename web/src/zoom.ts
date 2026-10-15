/**
 * The zoom presets, as published by `GET /api/zoom-presets`.
 *
 * The buttons are **rendered from this plan** rather than from a client-side
 * table, so the labels and the data can never disagree: a preset the backend
 * cannot draw is simply not offered.
 *
 * The plan is also honest about *how* each preset is satisfied — `mode: 'fit'`
 * means "everything loaded", and `mode: 'range'` carries the interval and bar
 * count it needs. That interval is the part that matters: a 5-second window
 * cannot be drawn from one-minute candles, so the fine presets ask for `1s`
 * candles, and selecting one is an **interval switch, which is a real data
 * fetch**. Only a preset whose interval is already loaded is a pure zoom.
 */

export interface ZoomPreset {
  /** Stable id / label, e.g. `5s`, `1h`, `all`. */
  preset: string;
  /** `range` needs `bars` of `interval`; `fit` means the whole loaded history. */
  mode: 'range' | 'fit';
  /** The interval this preset must be drawn on. `fit` presets omit it. */
  interval?: string;
  /** How many bars the window should show. */
  bars?: number;
  /** Optional longer description from the server. */
  label?: string;
}

export interface ZoomPlan {
  presets: ZoomPreset[];
  allowedIntervals: string[];
  /** The server's own explanation, surfaced verbatim in the UI. */
  note: string | null;
  /** True when the plan came from the endpoint rather than the fallback. */
  fromServer: boolean;
}

/**
 * The documented plan, used until the fetch lands (and if it never does).
 *
 * Deliberately identical to the live response so a cold start behaves the same;
 * the fetched plan always wins.
 */
export const ZOOM_PLAN_FALLBACK: ZoomPlan = {
  presets: [
    { preset: '5s', mode: 'range', interval: '1s', bars: 5 },
    { preset: '10s', mode: 'range', interval: '1s', bars: 10 },
    { preset: '30s', mode: 'range', interval: '1s', bars: 30 },
    { preset: '1m', mode: 'range', interval: '1s', bars: 60 },
    { preset: '2m', mode: 'range', interval: '1s', bars: 120 },
    { preset: '5m', mode: 'range', interval: '1s', bars: 300 },
    { preset: '1h', mode: 'range', interval: '1m', bars: 60 },
    { preset: 'all', mode: 'fit', label: 'All loaded history' },
  ],
  allowedIntervals: ['1s', '1m', '5m', '15m', '1h', '4h', '1d'],
  note: 'Presets finer than the currently loaded interval require an interval switch, which IS a data fetch. Only presets within the loaded interval are pure zoom.',
  fromServer: false,
};

/** Parse a plan defensively; anything unusable keeps the fallback. */
export function parseZoomPlan(raw: unknown): ZoomPlan | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const data = raw as { presets?: unknown; allowed_intervals?: unknown; note?: unknown };
  if (!Array.isArray(data.presets)) return null;

  const presets: ZoomPreset[] = [];
  for (const item of data.presets) {
    if (typeof item !== 'object' || item === null) continue;
    const row = item as Record<string, unknown>;
    const preset = typeof row.preset === 'string' ? row.preset.trim() : '';
    if (preset === '') continue;
    const mode = row.mode === 'fit' ? 'fit' : 'range';
    const interval = typeof row.interval === 'string' ? row.interval.trim() : undefined;
    const bars = typeof row.bars === 'number' && Number.isFinite(row.bars) && row.bars > 0 ? Math.floor(row.bars) : undefined;
    // A range preset without a usable interval or bar count cannot be drawn, so it
    // is dropped rather than rendered as a button that does nothing.
    if (mode === 'range' && (interval === undefined || bars === undefined)) continue;
    presets.push({
      preset,
      mode,
      ...(interval === undefined ? {} : { interval }),
      ...(bars === undefined ? {} : { bars }),
      ...(typeof row.label === 'string' ? { label: row.label } : {}),
    });
  }
  if (presets.length === 0) return null;

  return {
    presets,
    allowedIntervals: Array.isArray(data.allowed_intervals)
      ? data.allowed_intervals.filter((v): v is string => typeof v === 'string')
      : ZOOM_PLAN_FALLBACK.allowedIntervals,
    note: typeof data.note === 'string' && data.note.trim() !== '' ? data.note : null,
    fromServer: true,
  };
}

/**
 * How a preset must be satisfied, given what is already loaded.
 *
 * This is the behaviour the acceptance criteria turn on:
 *
 * - the preset's interval is already loaded → **pure zoom**, no request;
 * - it differs → an **interval switch**, which is a fetch, and the UI shows its
 *   loading state while it happens.
 *
 * Never silently draw a 5-second window out of one-minute candles: that is the
 * bug this mapping exists to remove.
 */
export function presetAction(preset: ZoomPreset, loaded: string): 'zoom' | 'switch' | 'fit' {
  if (preset.mode === 'fit') return 'fit';
  return preset.interval === loaded ? 'zoom' : 'switch';
}
