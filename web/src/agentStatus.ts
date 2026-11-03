/**
 * Agent liveness rule.
 *
 * Kept free of React and the DOM so `scripts/verify-logic.mjs` can exercise it
 * directly, and so the dashboard and any other consumer agree on one answer.
 */

/**
 * An agent is active while it keeps beaconing. The agent's beacon interval is
 * 30s, so three missed beacons (90s) means it is gone and must not be listed.
 */
export const AGENT_ACTIVE_MS = 90_000;

/**
 * isAgentActive reports whether an agent has checked in recently enough to be
 * worth displaying. A never-seen agent (0/absent timestamp) is not active: it
 * has no evidence of being alive.
 */
export function isAgentActive(lastSeenMs: unknown, now: number): boolean {
  const seen = Number(lastSeenMs) || 0;
  if (!Number.isFinite(seen) || seen <= 0) return false;
  return now - seen < AGENT_ACTIVE_MS;
}
