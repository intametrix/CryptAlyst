/**
 * Forecast horizons in human terms.
 *
 * A user thinks in "a day" or "a week", not in bars, but the model forecasts a
 * number of bars. This module is the single conversion between the two, kept
 * free of React so `scripts/verify-logic.mjs` can test it directly.
 */
import type { Interval } from './types';

/** Interval length in milliseconds, matching the backend's table. */
export const INTERVAL_MS: Record<string, number> = {
  '1s': 1_000,
  '1m': 60_000,
  '5m': 300_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '1d': 86_400_000,
};

/** Selectable forward windows, in hours. */
export const HORIZON_CHOICES: { hours: number; label: string }[] = [
  { hours: 1, label: '1 hour' },
  { hours: 4, label: '4 hours' },
  { hours: 12, label: '12 hours' },
  { hours: 24, label: '1 day' },
  { hours: 72, label: '3 days' },
  { hours: 168, label: '1 week' },
  { hours: 336, label: '2 weeks' },
  { hours: 720, label: '1 month' },
];

/** barsFor converts a forward window into the number of bars it covers. */
export function barsFor(hours: number, interval: Interval): number {
  const ms = INTERVAL_MS[interval] ?? 3_600_000;
  const bars = Math.round((hours * 3_600_000) / ms);
  return Math.max(1, bars);
}

/** barsToHours is the inverse, used to state how long a horizon really is. */
export function barsToHours(bars: number, interval: Interval): number {
  const ms = INTERVAL_MS[interval] ?? 3_600_000;
  return (bars * ms) / 3_600_000;
}

/** horizonLabel is the human phrase for a window at a given interval. */
export function horizonLabel(hours: number, interval: Interval): string {
  const bars = barsFor(hours, interval);
  const choice = HORIZON_CHOICES.find((c) => c.hours === hours);
  const name = choice ? choice.label : `${hours}h`;
  return `${name} · ${bars} bars`;
}

/**
 * historyNeeded is how many bars are required before a horizon can be graded:
 * the lab needs a training window plus the holdout itself.
 */
export function historyNeeded(bars: number, minTrain = 60): number {
  return bars + minTrain;
}

/** Selectable training windows, in hours (0 = everything available). */
export const TRAIN_CHOICES: { hours: number; label: string }[] = [
  { hours: 24, label: '1 day' },
  { hours: 72, label: '3 days' },
  { hours: 168, label: '1 week' },
  { hours: 336, label: '2 weeks' },
  { hours: 720, label: '1 month' },
  { hours: 2160, label: '3 months' },
  { hours: 0, label: 'all history' },
];

/**
 * trainLabel is the human phrase for a training window, including how many bars
 * it works out to at this interval.
 */
export function trainLabel(hours: number, interval: Interval): string {
  const bars = trainBars(hours, interval);
  const choice = TRAIN_CHOICES.find((c) => c.hours === hours);
  const name = choice ? choice.label : `${hours}h`;
  return hours === 0 ? name : `${name} · ${bars} bars`;
}

/** trainBars converts a training window to bars; 0 means "all available". */
export function trainBars(hours: number, interval: Interval): number {
  if (hours <= 0) return 0;
  return barsFor(hours, interval);
}
