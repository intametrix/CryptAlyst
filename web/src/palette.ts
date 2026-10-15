/**
 * The chart palette, read from the CSS custom properties in `styles.css`.
 *
 * The rule is one definition per colour: the dashboard chrome uses the custom
 * properties directly in CSS, and the chart library (which needs concrete
 * strings) reads those same properties here. Neither side re-authors a colour,
 * so the chart and the feed cannot drift apart.
 *
 * `resolvePalette()` is called once when a chart is created. If the stylesheet
 * has not loaded (or the tokens are missing), it falls back to the documented
 * values rather than producing `undefined` colours, which the library would
 * render as black.
 */

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
}

/** The documented defaults; also what a missing token degrades to. */
const FALLBACK: ChartPalette = {
  up: '#2ee06a',
  down: '#ff4d5e',
  flat: '#8492a9',
  accent: '#4d8dfd',
  reversal: '#ffb020',
  breakout: '#4d8dfd',
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
  severity: { watch: '#8492a9', alert: '#ffb020', critical: '#ff2d55' },
};

const TOKEN: Record<Exclude<keyof ChartPalette, 'severity'>, string> = {
  up: '--chart-up',
  down: '--chart-down',
  flat: '--chart-flat',
  accent: '--chart-accent',
  reversal: '--chart-reversal',
  breakout: '--chart-breakout',
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
  next.severity = {
    watch: read('--sev-watch', FALLBACK.severity.watch),
    alert: read('--sev-alert', FALLBACK.severity.alert),
    critical: read('--sev-critical', FALLBACK.severity.critical),
  };
  cached = next;
  return next;
}

/** Invalidate the cache (used when the theme changes). */
export function resetPaletteCache(): void {
  cached = null;
}

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
