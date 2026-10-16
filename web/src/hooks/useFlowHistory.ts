import { useEffect, useState } from 'react';
import { toNum } from '../format';

/** One 10-second flow bin. */
export interface FlowBin {
  /** The bin's start time, epoch ms. */
  t: number;
  flowQuote: number;
  baselineQuote: number;
  /** `flow_quote / baseline_quote` — the abnormality the detector thresholds on. */
  burstMultiple: number;
  change10s: number;
}

/**
 * Why the fetch failed, in the two shapes that must look different.
 *
 * A **quiet** symbol is tracked but has no flow: the endpoint answers `200` with
 * zeroed bins, and that is drawn as a flat line. An **untracked** symbol answers
 * `404`, which means "not tracked", not "broken" — showing an error there would
 * be a lie, and showing flat would hide a real gap in coverage.
 */
export type FlowHistoryState =
  | { status: 'loading'; bins: []; quiet: false }
  | { status: 'ready'; bins: FlowBin[]; quiet: boolean }
  /** Tracked, but every bin is zero: draw it flat, never as an error. */
  | { status: 'quiet'; bins: FlowBin[]; quiet: true }
  /** Not tracked: a coverage gap, which is a different thing from quiet. */
  | { status: 'untracked'; bins: []; quiet: false }
  | { status: 'error'; bins: []; quiet: false };

function parseBins(raw: unknown): FlowBin[] {
  if (!Array.isArray(raw)) return [];
  const out: FlowBin[] = [];
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue;
    const row = item as Record<string, unknown>;
    const t = toNum(row.t);
    if (t === null) continue;
    out.push({
      t: Math.trunc(t),
      flowQuote: toNum(row.flow_quote) ?? 0,
      baselineQuote: toNum(row.baseline_quote) ?? 0,
      burstMultiple: toNum(row.burst_multiple) ?? 0,
      change10s: toNum(row.change_10s) ?? 0,
    });
  }
  // Oldest first, deduplicated by bin time: the chart requires strictly
  // increasing times, and a duplicate would be rejected by the library.
  out.sort((a, b) => a.t - b.t);
  const deduped: FlowBin[] = [];
  for (const bin of out) {
    if (deduped.length > 0 && deduped[deduped.length - 1]?.t === bin.t) deduped[deduped.length - 1] = bin;
    else deduped.push(bin);
  }
  return deduped;
}

/**
 * Fetch the 10-second flow bins for one symbol.
 *
 * The response carries `{ bins: [...] }`, each bin already containing the ratio
 * the detector thresholds on, so nothing is recomputed here. The two empty states
 * are distinguished on purpose: a `404` is *untracked* (a coverage gap, reported
 * as such) while a `200` full of zeros is *quiet* (drawn flat).
 *
 * This is the pane that makes the detector's claim legible: a 1s candle shows
 * price, not the abnormal quote-volume burst that triggered the alert.
 */
export function useFlowHistory(
  symbol: string | null,
  limit = 360,
  options: { pollMs?: number } = {},
): FlowHistoryState {
  const pollMs = options.pollMs ?? 30_000;
  const [state, setState] = useState<FlowHistoryState>({ status: 'loading', bins: [], quiet: false });
  /** Bumped to re-issue the request without changing the subject. */
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (symbol === null) {
      setState({ status: 'ready', bins: [], quiet: false });
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setState({ status: 'loading', bins: [], quiet: false });

    void (async () => {
      try {
        const res = await fetch(`/api/flow/history?symbol=${encodeURIComponent(symbol)}&limit=${limit}`, {
          headers: { accept: 'application/json' },
          signal: controller.signal,
        });
        if (cancelled) return;
        // 404 is a *state*, not a failure: the symbol is not tracked.
        if (res.status === 404) {
          setState({ status: 'untracked', bins: [], quiet: false });
          return;
        }
        if (!res.ok) {
          setState({ status: 'error', bins: [], quiet: false });
          return;
        }
        const contentType = res.headers.get('content-type') ?? '';
        if (!contentType.toLowerCase().includes('json')) {
          setState({ status: 'error', bins: [], quiet: false });
          return;
        }
        const body: unknown = await res.json();
        if (cancelled) return;
        const bins = parseBins((body as { bins?: unknown }).bins);
        if (bins.length === 0) {
          setState({ status: 'untracked', bins: [], quiet: false });
          return;
        }
        // Every bin zero means tracked-but-quiet, which is drawn flat rather than
        // reported as a problem.
        const quiet = bins.every((b) => b.flowQuote === 0 || b.burstMultiple === 0);
        setState(quiet ? { status: 'quiet', bins, quiet: true } : { status: 'ready', bins, quiet: false });
      } catch {
        if (!cancelled) setState({ status: 'error', bins: [], quiet: false });
      }
    })();

    // A quiet or untracked symbol must not stay that way forever: the endpoint
    // returns bins only as flow is observed, so a fresh symbol starts empty and
    // fills in. This re-issues the request on a slow timer, and not at all while
    // the tab is hidden.
    const timer = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      setNonce((n) => n + 1);
    }, pollMs);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [symbol, limit, pollMs, nonce]);

  return state;
}
