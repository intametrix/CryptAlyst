/**
 * Display formatting helpers.
 *
 * Every helper is *total*: it accepts `unknown` (missing key, `null`, `NaN`,
 * or a stray string from a sloppy backend) and degrades to an em dash instead
 * of ever rendering "NaN", "undefined" or "$NaN".
 *
 * Fractions arrive as fractions, not percents (`0.0461` = +4.61%), so the
 * `×100` is done here.
 */

/** Rendered whenever a value is absent/unusable. */
export const DASH = '—';

/** Coerce anything to a finite number, or `null`. */
export function toNum(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string') {
    const t = v.trim();
    if (t === '') return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** `"82490.00"` -> `"82,490.00"`; sign and decimals are preserved. */
export function group(s: string): string {
  const neg = s.startsWith('-');
  const body = neg ? s.slice(1) : s;
  const dot = body.indexOf('.');
  const int = dot === -1 ? body : body.slice(0, dot);
  const frac = dot === -1 ? '' : body.slice(dot);
  return `${neg ? '-' : ''}${int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${frac}`;
}

/** `"+0.00"` / `"-0.00"` are noise — normalise them to `"0.00"`. */
function dropNegZero(s: string): string {
  return s.startsWith('-') && Number(s) === 0 ? s.slice(1) : s;
}

/** Signed percentage from a fraction: `pct(0.0461)` -> `"+4.61%"`. */
export function pct(v: unknown, digits = 2): string {
  const n = toNum(v);
  if (n === null) return DASH;
  const s = dropNegZero((n * 100).toFixed(digits));
  return n > 0 ? `+${s}%` : `${s}%`;
}

/** Unsigned percentage from a fraction: `share(0.54)` -> `"54.0%"`. */
export function share(v: unknown, digits = 1): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return `${dropNegZero((n * 100).toFixed(digits))}%`;
}

const USD_UNITS: ReadonlyArray<{ scale: number; suffix: string }> = [
  { scale: 1e12, suffix: 'T' },
  { scale: 1e9, suffix: 'B' },
  { scale: 1e6, suffix: 'M' },
  { scale: 1e3, suffix: 'K' },
];

/** Compact USD: `usd(76400)` -> `"$76.4K"`, `usd(1.8e6)` -> `"$1.8M"`. */
export function usd(v: unknown): string {
  const n = toNum(v);
  if (n === null) return DASH;
  const sign = n < 0 ? '-' : '';
  const a = Math.abs(n);
  if (a === 0) return '$0';
  for (let i = 0; i < USD_UNITS.length; i += 1) {
    const u = USD_UNITS[i];
    if (a >= u.scale) {
      let scaled = a / u.scale;
      let suffix = u.suffix;
      // 999_960 -> "1000.0K" would be silly; promote to "$1.0M".
      if (Number(scaled.toFixed(1)) >= 1000 && i > 0) {
        scaled = a / USD_UNITS[i - 1].scale;
        suffix = USD_UNITS[i - 1].suffix;
      }
      return `${sign}$${scaled.toFixed(1)}${suffix}`;
    }
  }
  if (a >= 1) return `${sign}$${Math.round(a)}`;
  return `${sign}$${a.toFixed(2)}`;
}

/**
 * Adaptive price decimals for small-cap tick sizes:
 *   px(82490)     -> "82,490.00"
 *   px(1.1482)    -> "1.1482"
 *   px(0.0477)    -> "0.047700"
 *   px(0.00001234)-> "0.00001234"
 */
export function px(v: unknown): string {
  const n = toNum(v);
  if (n === null) return DASH;
  const sign = n < 0 ? '-' : '';
  const a = Math.abs(n);
  if (a === 0) return '0.00';
  if (a >= 1000) return sign + group(a.toFixed(2));
  if (a >= 1) return sign + a.toFixed(4);
  if (a >= 0.0001) return sign + a.toFixed(6);
  return sign + a.toFixed(8);
}

/** Fixed-decimal number with thousands separators: `num(1234.5, 1)`. */
export function num(v: unknown, digits = 0): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return group(dropNegZero(n.toFixed(digits)));
}

/** Integer with thousands separators. */
export function int(v: unknown): string {
  return num(v, 0);
}

/** Ratio with a multiplication sign: `multiple(16.1)` -> `"16.1×"`. */
export function multiple(v: unknown, digits = 1): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return `${num(n, digits)}×`;
}

/** `"OSMOUSDT"` for a string, else em dash — for `reject_reason`, `group`, … */
export function text(v: unknown): string {
  if (typeof v === 'string') {
    const t = v.trim();
    return t === '' ? DASH : t;
  }
  if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  return DASH;
}

/** `true`/`false` as `"yes"`/`"—"` — used for boolean flags that may be null. */
export function flag(v: unknown): string {
  if (v === true) return 'yes';
  if (v === false) return 'no';
  return DASH;
}

/** A `string[]` joined for display; absent lists render as an em dash. */
export function joinList(v: unknown, sep = ', '): string {
  if (!Array.isArray(v) || v.length === 0) return DASH;
  const parts = v.map((x) => (typeof x === 'string' ? x : String(x))).filter((x) => x !== '');
  return parts.length === 0 ? DASH : parts.join(sep);
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** Local wall-clock `HH:MM:SS` for an epoch-ms timestamp. */
export function clock(ms: unknown): string {
  const n = toNum(ms);
  if (n === null) return DASH;
  const d = new Date(n);
  if (Number.isNaN(d.getTime())) return DASH;
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** UTC ISO timestamp — used for `title` tooltips. */
export function isoUtc(ms: unknown): string {
  const n = toNum(ms);
  if (n === null) return DASH;
  const d = new Date(n);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toISOString();
}

/** Human age of a timestamp relative to `nowMs`: `"4s"`, `"3m 07s"`. */
export function age(ms: unknown, nowMs: number): string {
  const n = toNum(ms);
  if (n === null) return DASH;
  return duration((nowMs - n) / 1000);
}

/** Compact duration from seconds: `"34s"`, `"20m 34s"`, `"5h 03m"`, `"2d 04h"`. */
export function duration(sec: unknown): string {
  const n = toNum(sec);
  if (n === null) return DASH;
  const s = Math.max(0, Math.floor(n));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${pad2(s % 60)}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${pad2(Math.floor((s % 3600) / 60))}m`;
  return `${Math.floor(s / 86400)}d ${pad2(Math.floor((s % 86400) / 3600))}h`;
}

/** CSS tone for a signed value: `"up"` (green) / `"down"` (red) / `""`. */
export function tone(v: unknown): 'up' | 'down' | '' {
  const n = toNum(v);
  if (n === null || n === 0) return '';
  return n > 0 ? 'up' : 'down';
}
