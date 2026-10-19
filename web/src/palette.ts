/**
 * The chart palette, read from the CSS custom properties.
 *
 * The directional and severity values are the **canon's**: `/api/colors` is
 * fetched once and written onto `:root` as `--c-*` (see `src/colors.ts`), and
 * `styles.css` seeds those same tokens with the documented fallback. This module
 * is only the accessor the chart library needs, because it wants concrete colour
 * strings rather than `var()` references — it is not a second palette.
 *
 * `resolvePalette()` is called once when a chart is created. If the stylesheet
 * has not loaded (or the tokens are missing), it falls back to the documented
 * values rather than producing `undefined` colours, which the library would
 * render as black.
 */

import { CANON_FALLBACK, onCanonChange } from './colors';

export interface ChartPalette {
  up: string;
  down: string;
  flat: string;
  accent: string;
  reversal: string;
  breakout: string;
  donchian: string;
  donchianSlow: string;
  emaFast: string;
  emaMed: string;
  smaSlow: string;
  atr: string;
  adx: string;
  rsi: string;
  grid: string;
  border: string;
  text: string;
  volumeUp: string;
  volumeDown: string;
  volumeFlat: string;
  severity: { watch: string; alert: string; critical: string };
  /** The pane background, so a band can mask what is beneath it. */
  background: string;
}

/** The documented defaults; also what a missing token degrades to. */
const FALLBACK: ChartPalette = {
  // Directional and severity hues come from the canonical palette in one place.
  up: CANON_FALLBACK.direction.up,
  down: CANON_FALLBACK.direction.down,
  flat: CANON_FALLBACK.severity.WATCH,
  accent: '#4d8dfd',
  reversal: CANON_FALLBACK.direction.reversal,
  breakout: CANON_FALLBACK.direction.neutral,
  donchian: '#4d8dfd',
  donchianSlow: '#8b5cf6',
  emaFast: '#22d3ee',
  emaMed: '#f59e0b',
  smaSlow: '#e879f9',
  atr: '#fb923c',
  adx: '#38bdf8',
  rsi: '#c084fc',
  grid: 'rgba(30, 38, 54, 0.6)',
  border: '#1e2636',
  text: '#8492a9',
  volumeUp: 'rgba(46, 224, 106, 0.45)',
  volumeDown: 'rgba(255, 77, 94, 0.45)',
  volumeFlat: 'rgba(132, 146, 169, 0.35)',
  background: '#111725',
  severity: {
    watch: CANON_FALLBACK.severity.WATCH,
    alert: CANON_FALLBACK.severity.ALERT,
    critical: CANON_FALLBACK.severity.CRITICAL,
  },
};

const TOKEN: Record<Exclude<keyof ChartPalette, 'severity'>, string> = {
  // Directional hues are the canon's tokens; the rest are chart-specific.
  up: '--c-up',
  down: '--c-down',
  flat: '--c-sev-watch',
  accent: '--chart-accent',
  reversal: '--c-reversal',
  breakout: '--c-neutral',
  donchian: '--chart-donchian',
  donchianSlow: '--chart-donchian-slow',
  emaFast: '--chart-ema-fast',
  emaMed: '--chart-ema-med',
  smaSlow: '--chart-sma-slow',
  atr: '--chart-atr',
  adx: '--chart-adx',
  rsi: '--chart-rsi',
  grid: '--chart-grid',
  border: '--chart-border',
  text: '--chart-text',
  volumeUp: '--chart-volume-up',
  volumeDown: '--chart-volume-down',
  volumeFlat: '--chart-volume-flat',
  background: '--panel',
};

let cached: ChartPalette | null = null;

/** Read the palette out of the computed style, with defaults for anything absent. */
export function resolvePalette(force = false): ChartPalette {
  if (cached !== null && !force) return cached;
  if (typeof window === 'undefined' || typeof document === 'undefined') return FALLBACK;
  const style = getComputedStyle(document.documentElement);
  const read = (token: string, fallback: string): string => {
    const value = style.getPropertyValue(token).trim();
    return value === '' ? fallback : value;
  };
  const next: ChartPalette = { ...FALLBACK, severity: { ...FALLBACK.severity } };
  for (const [key, token] of Object.entries(TOKEN) as Array<[keyof typeof TOKEN, string]>) {
    next[key] = read(token, FALLBACK[key]);
  }
  next.background = read('--panel', FALLBACK.background);
  next.severity = {
    watch: read('--c-sev-watch', FALLBACK.severity.watch),
    alert: read('--c-sev-alert', FALLBACK.severity.alert),
    critical: read('--c-sev-critical', FALLBACK.severity.critical),
  };
  cached = next;
  return next;
}

/** Invalidate the cache (used when the canon or the theme changes). */
export function resetPaletteCache(): void {
  cached = null;
}

// The canon is fetched after the first chart may already have resolved a palette,
// so a resolved cache is dropped whenever it changes.
onCanonChange(resetPaletteCache);

/**
 * Colour for a marker, preferring the server's own value.
 *
 * The backend already sends an explicit `color` per mark, and the server staying
 * authoritative matters: it knows things the client does not (a detector with its
 * own palette, for instance). The CSS tokens are the fallback.
 */
export function markColor(opts: {
  serverColor?: string | null;
  severity?: string | null;
  direction?: 1 | -1 | null;
  kind?: string | null;
}): string {
  const server = typeof opts.serverColor === 'string' ? opts.serverColor.trim() : '';
  if (server !== '') return server;
  const palette = resolvePalette();
  const severity = (opts.severity ?? '').toUpperCase();
  if (severity === 'CRITICAL') return palette.severity.critical;
  if (severity === 'WATCH') return palette.flat;
  const kind = (opts.kind ?? '').toUpperCase();
  if (kind.includes('REVERSAL')) return palette.reversal;
  if (kind.includes('BREAKOUT')) return palette.breakout;
  return (opts.direction ?? 1) < 0 ? palette.down : palette.up;
}
