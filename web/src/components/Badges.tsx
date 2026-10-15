import { eventIdentity, severityColor, type ColorCanon } from '../colors';
import { detectorLabel, kindStyle, prettifyEnum, severityStyle } from '../types';
import type { AlertKind, Severity } from '../types';

/**
 * There is deliberately no "UNKNOWN" anywhere in this file.
 *
 * An enum value the frontend has not seen before is prettified (`BREAKOUT_LONG`
 * → "Breakout Long", `flow-onset` → "Flow Onset") and coloured from a neutral
 * style bucket, so a new backend value degrades to a readable grey badge instead
 * of shouting "UNKNOWN" at whoever is watching the screen.
 */

const KIND_CLASS: Readonly<Record<string, string>> = {
  pump: 'kind-pump',
  dump: 'kind-dump',
  'reversal-pump': 'kind-rev-pump',
  'reversal-dump': 'kind-rev-dump',
  'breakout-long': 'kind-breakout-long',
  'breakout-short': 'kind-breakout-short',
  breakout: 'kind-breakout',
  other: 'kind-other',
};

/** Short forms for the badges, only where a long word would wrap. */
const KIND_SHORT: Readonly<Record<string, string>> = {
  PUMP: 'PUMP',
  DUMP: 'DUMP',
  REVERSAL_PUMP: 'REV PUMP',
  REVERSAL_DUMP: 'REV DUMP',
};

const SEV_CLASS: Readonly<Record<string, string>> = {
  watch: 'sev-watch',
  alert: 'sev-alert',
  critical: 'sev-critical',
  other: 'sev-other',
};

const SEV_SHORT: Readonly<Record<string, string>> = {
  WATCH: 'WATCH',
  ALERT: 'ALERT',
  CRITICAL: 'CRITICAL',
};

/**
 * `kind` badge — PUMP / DUMP / REVERSAL_* / BREAKOUT_*, and anything new the
 * backend grows. Unknown words are title-cased, never "UNKNOWN".
 */
export function KindBadge({
  kind,
  /** The server's resolved colour for this event, when it sent one. */
  color,
  canon = null,
}: {
  kind?: AlertKind | string | null;
  color?: string | null;
  canon?: ColorCanon | null;
}) {
  const style = kindStyle(kind);
  const word = typeof kind === 'string' ? kind.trim().toUpperCase() : '';
  const label = KIND_SHORT[word] ?? prettifyEnum(kind, 'Unclassified');
  // The identity supplies the colour: the payload's own value when present, the
  // canonical palette otherwise. Direction is not severity, so this badge and the
  // severity badge next to it stay on separate axes.
  const identity = eventIdentity({ kind, color, canon });
  const serverColor = typeof color === 'string' && color.trim() !== '';
  return (
    <span
      className={`badge ${KIND_CLASS[style] ?? KIND_CLASS.other}${serverColor ? ' badge-canon' : ''}`}
      data-color={identity.color}
      data-letter={identity.letter}
      data-direction={identity.direction}
      style={serverColor ? { ['--eglyph-color' as string]: identity.color } : undefined}
    >
      {label}
    </span>
  );
}

/** `severity` badge — WATCH / ALERT / CRITICAL; anything else reads neutrally. */
export function SeverityBadge({
  severity,
  canon = null,
}: {
  severity?: Severity | string | null;
  canon?: ColorCanon | null;
}) {
  const style = severityStyle(severity);
  const word = typeof severity === 'string' ? severity.trim().toUpperCase() : '';
  const label = SEV_SHORT[word] ?? prettifyEnum(severity, 'Unrated');
  // Severity is its own axis: a WATCH pump is green while this badge is grey.
  return (
    <span
      className={`badge ${SEV_CLASS[style] ?? SEV_CLASS.other}`}
      data-color={severityColor(severity, canon)}
      style={{ ['--eglyph-color' as string]: severityColor(severity, canon) }}
    >
      {label}
    </span>
  );
}

/** Which model produced an alert: `flow-onset`, `pump-screen`, `turtle-breakout`. */
export function DetectorBadge({ detector }: { detector?: string | null }) {
  if (typeof detector !== 'string' || detector.trim() === '') return null;
  return (
    <span className="badge detector-badge" title={`produced by ${detectorLabel(detector)}`}>
      {detectorLabel(detector)}
    </span>
  );
}

/** Neutral marker for a batch (snapshot-based) signal rather than a live one. */
export function BatchBadge({ snapshotMs, nowMs }: { snapshotMs: number | null; nowMs: number }) {
  const seconds = snapshotMs === null ? null : Math.max(0, Math.round((nowMs - snapshotMs) / 1000));
  return (
    <span className="badge batch-badge" title="batch detector — the underlying snapshot is not live tick data">
      {seconds === null ? 'batch' : `snapshot ${seconds}s ago`}
    </span>
  );
}
