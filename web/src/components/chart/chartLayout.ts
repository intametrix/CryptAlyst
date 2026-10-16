/**
 * Persisted layout state for the chart popup's bottom panel.
 *
 * Follows the same `saveBoxSize` / `readBoxSize` + `saveLayoutFlag` /
 * `readLayoutFlag` pattern SignalsDock uses, so "reset layout" clears these
 * with everything else.
 */

import { readBoxSize, readLayoutFlag, saveBoxSize, saveLayoutFlag } from '../../layout';

/**
 * Default open height of the bottom (events + data) panel, in px.
 *
 * Default OPEN because the human asked for the panel to exist and an
 * undiscovered panel is useless. CONTESTED: the human previously asked for
 * collapsed-on-open twice; verdict pending — changing this is one line.
 */
export const PANEL_DEFAULT_HEIGHT_PX = 220;

/**
 * Default open/closed state of the bottom panel.
 *
 * Default OPEN because the human asked for the panel to exist and an
 * undiscovered panel is useless. CONTESTED: the human previously asked for
 * collapsed-on-open twice; verdict pending — changing this is one line.
 */
export const PANEL_DEFAULT_OPEN = true;

/** The smallest the panel may be while open. */
export const PANEL_MIN_HEIGHT_PX = 120;

const HEIGHT_KEY = 'chart.popup.panel';
const OPEN_KEY = 'chart.popup.panelOpen';

/** The persisted panel height, or the default. */
export function readPanelHeight(): number {
  const stored = readBoxSize(HEIGHT_KEY);
  if (stored !== null && stored.h >= PANEL_MIN_HEIGHT_PX) return Math.round(stored.h);
  return PANEL_DEFAULT_HEIGHT_PX;
}

export function savePanelHeight(h: number): void {
  saveBoxSize(HEIGHT_KEY, { w: 0, h: Math.round(h) });
}

/** The persisted open/closed flag, or the default. */
export function readPanelOpen(): boolean {
  return readLayoutFlag(OPEN_KEY, PANEL_DEFAULT_OPEN);
}

export function savePanelOpen(open: boolean): void {
  saveLayoutFlag(OPEN_KEY, open);
}
