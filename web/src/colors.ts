/**
 * The canonical colour + glyph language.
 *
 * The backend is the single source of truth: `/api/colors` publishes the palette
 * *and* the letter/glyph for every kind, and every alert, mark and flow row
 * carries its own resolved `color`. Nothing here maps an enum to a constant of
 * its own — that is exactly how two components drift apart and end up drawing the
 * same event two different ways.
 *
 * The fetched palette is written onto `document.documentElement` as CSS custom
 * properties, so the DOM and the chart library read the same values. The
 * documented constants are only a fallback for the window before the fetch lands
 * (or if the endpoint is unavailable).
 */

/**
 * Listeners notified when the canon changes.
 *
 * The chart palette caches a resolved copy of the tokens, so it needs to know.
 * A registry rather than a direct import because `palette.ts` imports the canon's
 * fallbacks from here — a direct two-way import would be a cycle.
 */
const canonListeners = new Set<() => void>();

/** Register a callback for canon changes; returns an unsubscribe function. */
export function onCanonChange(listener: () => void): () => void {
  canonListeners.add(listener);
  return () => canonListeners.delete(listener);
}

/** `/api/colors` — the palette plus the letter/glyph per kind. */
export interface ColorCanon {
  direction: { up: string; down: string; reversal: string; neutral: string };
  severity: { WATCH: string; ALERT: string; CRITICAL: string };
  /**
   * The text colour to use *on* each direction fill.
   *
   * Published by the backend because the fills are all bright: black on every
   * one, since white on `#00e676` would be unreadable. Read from the palette
   * rather than hardcoded, so the badge and the toolbar cannot disagree.
   */
  text: { up: string; down: string; reversal: string; neutral: string };
  /** kind -> single letter (P/D/R/B). */
  letters: Record<string, string>;
  /** kind -> marker glyph. */
  glyphs: Record<string, 'arrowUp' | 'arrowDown' | 'circle' | 'square'>;
}

/**
 * The documented palette. Used only until the fetch resolves, and if it never
 * does — the server's values always win.
 */
export const CANON_FALLBACK: ColorCanon = {
  direction: { up: '#00e676', down: '#ff1744', reversal: '#ffab00', neutral: '#40c4ff' },
  severity: { WATCH: '#78909c', ALERT: '#ffab00', CRITICAL: '#ff1744' },
  text: { up: '#000000', down: '#000000', reversal: '#000000', neutral: '#000000' },
  letters: {
    PUMP: 'P',
    DUMP: 'D',
    REVERSAL_PUMP: 'R',
    REVERSAL_DUMP: 'R',
    BREAKOUT_LONG: 'B',
    BREAKOUT_SHORT: 'B',
    BREAKOUT: 'B',
  },
  glyphs: {
    PUMP: 'arrowUp',
    DUMP: 'arrowDown',
    BREAKOUT_LONG: 'arrowUp',
    BREAKOUT_SHORT: 'arrowDown',
    REVERSAL_PUMP: 'arrowUp',
    REVERSAL_DUMP: 'arrowDown',
    BREAKOUT: 'arrowUp',
  },
};

/** Every CSS custom property the canon writes, and where its value comes from. */
const TOKENS: ReadonlyArray<{ name: string; pick: (canon: ColorCanon) => string }> = [
  { name: '--c-up', pick: (c) => c.direction.up },
  { name: '--c-down', pick: (c) => c.direction.down },
  { name: '--c-reversal', pick: (c) => c.direction.reversal },
  { name: '--c-neutral', pick: (c) => c.direction.neutral },
  { name: '--c-sev-watch', pick: (c) => c.severity.WATCH },
  { name: '--c-sev-alert', pick: (c) => c.severity.ALERT },
  { name: '--c-sev-critical', pick: (c) => c.severity.CRITICAL },
  { name: '--c-text-up', pick: (c) => c.text.up },
  { name: '--c-text-down', pick: (c) => c.text.down },
  { name: '--c-text-reversal', pick: (c) => c.text.reversal },
  { name: '--c-text-neutral', pick: (c) => c.text.neutral },
];

/**
 * Publish the canon as CSS custom properties.
 *
 * Called once when the palette arrives; the stylesheet references these tokens
 * (with the same fallbacks) so no component needs a hex value inline.
 */
export function applyCanonToCss(canon: ColorCanon): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  for (const token of TOKENS) {
    const value = token.pick(canon);
    if (typeof value === 'string' && value.trim() !== '') root.style.setProperty(token.name, value.trim());
  }
  // Anything caching a resolved copy of the tokens just became stale.
  for (const listener of canonListeners) listener();
}

/** Normalise an unknown payload into a canon, filling gaps from the fallback. */
export function parseCanon(raw: unknown): ColorCanon | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const data = raw as Partial<ColorCanon>;
  const canon: ColorCanon = {
    direction: { ...CANON_FALLBACK.direction, ...(data.direction ?? {}) },
    severity: { ...CANON_FALLBACK.severity, ...(data.severity ?? {}) },
    text: { ...CANON_FALLBACK.text, ...(data.text ?? {}) },
    // Letters and glyphs merge *per key*, so a backend that only publishes some
    // kinds still gets a usable map for all of them.
    letters: { ...CANON_FALLBACK.letters, ...(data.letters ?? {}) },
    glyphs: { ...CANON_FALLBACK.glyphs, ...(data.glyphs ?? {}) },
  };
  return canon;
}

/* ------------------------------------------------------------------ identity */

/** The visual identity of one event — the same everywhere it is rendered. */
export interface EventIdentity {
  /** Single letter: P, D, R or B. */
  letter: string;
  /** The colour to draw with. */
  color: string;
  /** Which family it belongs to, for CSS classes and styling. */
  family: 'pump' | 'dump' | 'reversal' | 'breakout' | 'other';
  /** Arrow direction: +1 up, -1 down. */
  direction: 1 | -1;
  /** The marker glyph, from the server's own map where possible. */
  glyph: 'arrowUp' | 'arrowDown' | 'circle' | 'square';
  /** The text colour for this fill — black on every bright direction colour. */
  textColor: string;
}

/** Normalise a kind for lookups: upper case, underscores kept. */
export function canonKind(kind: unknown): string {
  return typeof kind === 'string' ? kind.trim().toUpperCase() : '';
}

/** Which family a kind belongs to. */
export function familyOf(kind: unknown): EventIdentity['family'] {
  const k = canonKind(kind);
  if (k.includes('REVERSAL')) return 'reversal';
  if (k.includes('BREAKOUT')) return 'breakout';
  if (k.includes('DUMP')) return 'dump';
  if (k.includes('PUMP')) return 'pump';
  return 'other';
}

/**
 * Resolve one event's identity.
 *
 * Resolution order is deliberate:
 *
 * 1. the **server's own `color`** when the payload carries one (the canon says
 *    the UI must use it rather than mapping enums itself);
 * 2. the **server's letter/glyph map** for this kind;
 * 3. the documented fallbacks, then the kind-derived family, so a kind nobody
 *    has seen before still renders as *something* legible.
 *
 * Direction and severity stay separate axes: this returns the *direction* colour,
 * and a caller that wants to show severity asks for it separately. A CRITICAL
 * dump is red twice over; a WATCH pump stays green with a grey severity badge.
 */
export function eventIdentity(opts: {
  kind?: unknown;
  severity?: unknown;
  /** The payload's own resolved colour, when it has one. */
  color?: unknown;
  /** The payload's own letter, when it has one. */
  letter?: unknown;
  /** The payload's own direction (+1/-1), when it has one. */
  direction?: unknown;
  canon?: ColorCanon | null;
}): EventIdentity {
  const canon = opts.canon ?? null;
  const kind = canonKind(opts.kind);
  const family = familyOf(kind);

  const serverLetter = typeof opts.letter === 'string' ? opts.letter.trim().toUpperCase() : '';
  const letter =
    serverLetter !== ''
      ? serverLetter.slice(0, 1)
      : (canon?.letters[kind] ?? CANON_FALLBACK.letters[kind] ?? (family === 'other' ? '\u2022' : ''));

  const serverColor = typeof opts.color === 'string' ? opts.color.trim() : '';
  const severity = canonKind(opts.severity);
  const fallbackColor =
    severity === 'CRITICAL'
      ? (canon?.severity.CRITICAL ?? CANON_FALLBACK.severity.CRITICAL)
      : family === 'dump'
        ? (canon?.direction.down ?? CANON_FALLBACK.direction.down)
        : family === 'reversal'
          ? (canon?.direction.reversal ?? CANON_FALLBACK.direction.reversal)
          : family === 'breakout'
            ? (canon?.direction.neutral ?? CANON_FALLBACK.direction.neutral)
            : family === 'pump'
              ? (canon?.direction.up ?? CANON_FALLBACK.direction.up)
              : (canon?.severity.WATCH ?? CANON_FALLBACK.severity.WATCH);
  const color = serverColor !== '' ? serverColor : fallbackColor;

  const wireDirection = typeof opts.direction === 'number' && Number.isFinite(opts.direction) ? opts.direction : null;
  const glyph =
    canon?.glyphs[kind] ?? CANON_FALLBACK.glyphs[kind] ?? (family === 'dump' || family === 'reversal' ? 'arrowDown' : 'arrowUp');
  const direction: 1 | -1 =
    wireDirection !== null ? (wireDirection < 0 ? -1 : 1) : glyph === 'arrowDown' ? -1 : 1;

  return {
    letter: letter === '' ? '\u2022' : letter,
    color,
    family,
    direction,
    glyph,
    textColor: textFor(family, canon),
  };
}

/** The text colour to use on a direction fill. */
export function textFor(family: EventIdentity['family'], canon?: ColorCanon | null): string {
  const table = canon?.text ?? CANON_FALLBACK.text;
  if (family === 'dump') return table.down;
  if (family === 'reversal') return table.reversal;
  if (family === 'breakout') return table.neutral;
  return table.up;
}

/** The severity colour, on its own axis. */
export function severityColor(severity: unknown, canon?: ColorCanon | null): string {
  const key = canonKind(severity);
  const table = canon?.severity ?? CANON_FALLBACK.severity;
  if (key === 'CRITICAL') return table.CRITICAL;
  if (key === 'ALERT') return table.ALERT;
  return table.WATCH;
}
