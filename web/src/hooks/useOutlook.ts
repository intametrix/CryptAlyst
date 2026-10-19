import { useEffect, useState } from 'react';
import { toNum } from '../format';

/** One support or resistance level. */
export interface OutlookLevel {
  price: number;
  kind: 'support' | 'resistance';
  /** 0–1; stronger levels are drawn more opaque. */
  strength: number;
  touches: number;
  distancePct: number | null;
}

/** One forecast point: a projection with an uncertainty band. */
export interface ForecastPoint {
  t: number;
  value: number;
  lower: number | null;
  upper: number | null;
}

export interface OutlookEvent {
  inProgress: boolean;
  state: string;
  direction: 1 | -1;
  confidence: number;
  confidenceBasis: string;
  /** Seconds until the model expects a reversal, with its range. */
  etaReversalSec: number | null;
  etaLowSec: number | null;
  etaHighSec: number | null;
  /** Where that estimate comes from — shown verbatim, never paraphrased. */
  etaBasis: string;
}

export interface ShortHorizon {
  horizonSec: number | null;
  pUp: number | null;
  pDown: number | null;
  /**
   * The server's own words. It currently says "no measurable edge at this
   * horizon", and that sentence is the answer — it must be displayed verbatim
   * rather than replaced with a number the model cannot support.
   */
  edge: string;
  /** The caveat about what the model is, shown verbatim. */
  basis: string;
}

/** The calibration model's own description of itself — shown verbatim. */
export interface OutlookModel {
  calibrated: boolean;
  sampleSize: number | null;
  baseRate: number | null;
  brier: number | null;
  logLoss: number | null;
  /** The model's own words ("calibrated on N resolved predictions, brier X"). */
  basis: string;
  /** The labelling rule, including the in-sample caveat — shown verbatim. */
  labelRule: string;
  source: string;
}

export interface Outlook {
  symbol: string;
  model: OutlookModel | null;
  event: OutlookEvent | null;
  shortHorizon: ShortHorizon | null;
  supports: OutlookLevel[];
  resistances: OutlookLevel[];
  forecast: { kind: string | null; points: ForecastPoint[] };
  atMs: number;
}

function toLevel(raw: unknown): OutlookLevel | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const row = raw as Record<string, unknown>;
  const price = toNum(row.price);
  if (price === null) return null;
  const kind = row.kind === 'support' ? 'support' : 'resistance';
  return {
    price,
    kind,
    strength: Math.max(0, Math.min(1, toNum(row.strength) ?? 0)),
    touches: Math.max(0, Math.trunc(toNum(row.touches) ?? 0)),
    distancePct: toNum(row.distance_pct),
  };
}

function toForecastPoint(raw: unknown): ForecastPoint | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const row = raw as Record<string, unknown>;
  const t = toNum(row.t);
  const value = toNum(row.value);
  if (t === null || value === null) return null;
  return { t: Math.trunc(t), value, lower: toNum(row.lower), upper: toNum(row.upper) };
}

/** Parse the endpoint defensively; every section is optional. */
export function parseOutlook(raw: unknown): Outlook | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const data = raw as Record<string, unknown>;
  const outlook = (typeof data.outlook === 'object' && data.outlook !== null ? data.outlook : {}) as Record<string, unknown>;
  const forecastRaw = (typeof data.forecast === 'object' && data.forecast !== null ? data.forecast : {}) as Record<string, unknown>;

  const modelRaw = (typeof data.model === 'object' && data.model !== null ? data.model : null) as Record<string, unknown> | null;

  const eventRaw = (typeof outlook.event === 'object' && outlook.event !== null ? outlook.event : null) as Record<string, unknown> | null;
  const horizonRaw = (typeof outlook.short_horizon === 'object' && outlook.short_horizon !== null ? outlook.short_horizon : null) as Record<string, unknown> | null;

  const levels = (key: string): OutlookLevel[] =>
    (Array.isArray(outlook[key]) ? (outlook[key] as unknown[]) : [])
      .map(toLevel)
      .filter((level): level is OutlookLevel => level !== null)
      // Strongest first, so the ones worth drawing first are the ones that survive.
      .sort((a, b) => b.strength - a.strength);

  return {
    symbol: typeof data.symbol === 'string' ? data.symbol : '',
    atMs: Date.now(),
    model:
      modelRaw === null
        ? null
        : {
            calibrated: modelRaw.calibrated === true,
            sampleSize: toNum(modelRaw.sample_size),
            baseRate: toNum(modelRaw.base_rate),
            brier: toNum(modelRaw.brier),
            logLoss: toNum(modelRaw.log_loss),
            basis: typeof modelRaw.basis === 'string' ? modelRaw.basis : '',
            labelRule: typeof modelRaw.label_rule === 'string' ? modelRaw.label_rule : '',
            source: typeof modelRaw.source === 'string' ? modelRaw.source : '',
          },
    event:
      eventRaw === null
        ? null
        : {
            inProgress: eventRaw.in_progress === true,
            state: typeof eventRaw.state === 'string' ? eventRaw.state : 'unknown',
            direction: (toNum(eventRaw.direction) ?? 1) < 0 ? -1 : 1,
            confidence: toNum(eventRaw.confidence) ?? 0,
            confidenceBasis: typeof eventRaw.confidence_basis === 'string' ? eventRaw.confidence_basis : '',
            etaReversalSec: toNum(eventRaw.eta_reversal_sec),
            etaLowSec: toNum(eventRaw.eta_low_sec),
            etaHighSec: toNum(eventRaw.eta_high_sec),
            etaBasis: typeof eventRaw.eta_basis === 'string' ? eventRaw.eta_basis : '',
          },
    shortHorizon:
      horizonRaw === null
        ? null
        : {
            horizonSec: toNum(horizonRaw.horizon_sec),
            pUp: toNum(horizonRaw.p_up),
            pDown: toNum(horizonRaw.p_down),
            edge: typeof horizonRaw.edge === 'string' ? horizonRaw.edge : '',
            basis: typeof horizonRaw.basis === 'string' ? horizonRaw.basis : '',
          },
    supports: levels('supports'),
    resistances: levels('resistances'),
    forecast: {
      kind: typeof forecastRaw.kind === 'string' ? forecastRaw.kind : null,
      points: (Array.isArray(forecastRaw.points) ? (forecastRaw.points as unknown[]) : [])
        .map(toForecastPoint)
        .filter((point): point is ForecastPoint => point !== null)
        .sort((a, b) => a.t - b.t),
    },
  };
}

/**
 * Fetch `/api/outlook` for the charted symbol on the same cadence as the chart.
 *
 * This is the payload the user asked for by name: where the supports and
 * resistances actually are, how long the current excursion is expected to run,
 * and what the short-horizon model will — and will not — claim.
 */
export function useOutlook(symbol: string | null, pollMs = 15_000): Outlook | null {
  const [outlook, setOutlook] = useState<Outlook | null>(null);

  useEffect(() => {
    if (symbol === null) {
      setOutlook(null);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();

    const load = async (): Promise<void> => {
      try {
        const res = await fetch(`/api/outlook?symbol=${encodeURIComponent(symbol)}`, {
          headers: { accept: 'application/json' },
          signal: controller.signal,
        });
        if (!res.ok) return;
        if (!(res.headers.get('content-type') ?? '').toLowerCase().includes('json')) return;
        const parsed = parseOutlook(await res.json());
        if (!cancelled && parsed !== null) setOutlook(parsed);
      } catch {
        // An outlook is additive; a failure must not disturb the chart.
      }
    };

    void load();
    const timer = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      void load();
    }, pollMs);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [symbol, pollMs]);

  return outlook;
}
