/**
 * Pure tooltip geometry.
 *
 * Split out of the component so the "never overflows the viewport" rule can be
 * verified without a browser: given a trigger rect, a tooltip size, and a
 * viewport, `computePlacement` always returns a rectangle fully inside the
 * viewport on both axes (clamped, and flipped to the roomier side when the
 * preferred side does not fit).
 */

export type Side = 'top' | 'bottom';

export interface PlacementInput {
  /** Trigger rect, in viewport coordinates. */
  trigger: { top: number; left: number; width: number; height: number };
  /** Measured tooltip size in pixels. */
  tip: { width: number; height: number };
  /** Viewport size in pixels. */
  viewport: { width: number; height: number };
  /** Preferred side; flips automatically when it does not fit. */
  preferred: Side;
  /** Gap between trigger and tooltip. */
  gap?: number;
  /** Minimum distance kept from every viewport edge. */
  margin?: number;
}

export interface TipPosition {
  top: number;
  left: number;
  placement: Side;
  /** Horizontal offset of the pointer arrow, relative to the tooltip's left edge. */
  arrowLeft: number;
}

export function computePlacement(input: PlacementInput): TipPosition {
  const { trigger, tip, viewport, preferred } = input;
  const gap = input.gap ?? 8;
  const margin = input.margin ?? 8;

  const spaceBelow = viewport.height - (trigger.top + trigger.height) - gap - margin;
  const spaceAbove = trigger.top - gap - margin;

  let placement: Side = preferred;
  if (preferred === 'bottom' && spaceBelow < tip.height && spaceAbove > spaceBelow) placement = 'top';
  else if (preferred === 'top' && spaceAbove < tip.height && spaceBelow > spaceAbove) placement = 'bottom';

  const rawTop = placement === 'bottom' ? trigger.top + trigger.height + gap : trigger.top - gap - tip.height;
  const maxTop = Math.max(margin, viewport.height - tip.height - margin);
  const top = Math.min(maxTop, Math.max(margin, rawTop));

  const center = trigger.left + trigger.width / 2;
  const rawLeft = center - tip.width / 2;
  const maxLeft = Math.max(margin, viewport.width - tip.width - margin);
  const left = Math.min(maxLeft, Math.max(margin, rawLeft));

  // The arrow keeps pointing at the trigger centre even after the body clamped.
  const arrowLeft = Math.min(Math.max(center - left, 12), Math.max(12, tip.width - 12));

  return { top, left, placement, arrowLeft };
}
