import { eventIdentity, type ColorCanon, type EventIdentity } from '../colors';

/** The arrow that goes with a direction, in the same language as the markers. */
const ARROW = { up: '\u2191', down: '\u2193' } as const;

export interface EventGlyphProps {
  kind?: unknown;
  severity?: unknown;
  /** The payload's own resolved colour — authoritative when present. */
  color?: unknown;
  /** The payload's own letter, when it has one. */
  letter?: unknown;
  /** The payload's own direction (+1/−1), when it has one. */
  direction?: unknown;
  canon?: ColorCanon | null;
  /** Show the arrow glyph alongside the letter. */
  arrow?: boolean;
  /** Larger, for a detail panel. */
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  title?: string;
}

/**
 * One event's visual identity: the letter, its colour, and the arrow that goes
 * with it — resolved through the canonical palette so the chart marker, the
 * chart legend, the event row, the detail panel and the alert-feed badge all
 * render the *same* event the *same* way.
 *
 * The colour is an inline custom property rather than a class: the value comes
 * from the server (`/api/colors`, or the payload's own `color`), and a class per
 * colour would be a second, client-side copy of the palette — the exact drift the
 * canon exists to prevent.
 */
export function EventGlyph({
  kind,
  severity,
  color,
  letter,
  direction,
  canon = null,
  arrow = true,
  size = 'sm',
  className,
  title,
}: EventGlyphProps) {
  const identity: EventIdentity = eventIdentity({ kind, severity, color, letter, direction, canon });
  const arrowGlyph = identity.direction === 1 ? ARROW.up : ARROW.down;
  return (
    <span
      className={`eglyph eglyph-${identity.family} eglyph-${size}${className !== undefined ? ` ${className}` : ''}`}
      style={{ ['--eglyph-color' as string]: identity.color }}
      title={title ?? `${identity.letter} · ${identity.family}${arrow ? ` ${arrowGlyph}` : ''}`}
      data-letter={identity.letter}
      data-color={identity.color}
      data-direction={identity.direction}
      data-family={identity.family}
    >
      <b className="eglyph-letter" aria-hidden="true">
        {identity.letter}
      </b>
      {arrow && (
        <i className="eglyph-arrow" aria-hidden="true">
          {arrowGlyph}
        </i>
      )}
      <span className="sr-only">
        {identity.family} {identity.direction === 1 ? 'up' : 'down'}
      </span>
    </span>
  );
}
