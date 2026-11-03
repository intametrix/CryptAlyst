import { memo } from 'react';
import { useNow } from '../hooks/useNow';

/**
 * How old a panel's data is.
 *
 * A number with no age is a claim without a timestamp: a leaderboard frozen by a
 * dropped stream looks exactly like a live one. This renders the age and grades
 * it, so a stale panel is visibly stale.
 */
export const Freshness = memo(function Freshness({
  atMs,
  staleAfterS = 10,
  warnAfterS = 4,
  label,
}: {
  /** Timestamp of the data being shown, in ms. */
  atMs: number | null | undefined;
  staleAfterS?: number;
  warnAfterS?: number;
  /** Optional prefix, e.g. "flow". */
  label?: string;
}) {
  const now = useNow();
  if (atMs === null || atMs === undefined || !Number.isFinite(atMs)) return null;
  const ageS = Math.max(0, Math.round((now - atMs) / 1000));
  const tone = ageS >= staleAfterS ? 'is-stale' : ageS >= warnAfterS ? 'is-warn' : 'is-fresh';
  const text = ageS < 1 ? 'now' : ageS < 60 ? `${ageS}s ago` : ageS < 3600 ? `${Math.round(ageS / 60)}m ago` : `${Math.round(ageS / 3600)}h ago`;
  return (
    <span className={`freshness ${tone}`} title={`${label !== undefined ? `${label} ` : ''}updated ${text}`}>
      <span className="freshness-dot" aria-hidden="true" />
      {text}
    </span>
  );
});
