/**
 * The dashboard's chart affordance: a small line-chart glyph used on every
 * symbol control that opens the chart, so the control says "chart" and not
 * "drill-down" (human ruling, 2026-10-16). Deliberately distinct from any
 * trade/drill icon inside the chart popup.
 */
export function ChartIcon({ size = 12 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="2,12 6,7 9,9 14,3" />
    </svg>
  );
}
