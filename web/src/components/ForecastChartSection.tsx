import { memo } from 'react';
import { ChartPanel } from './ChartPanel';
import { ForecastChart } from './ForecastChart';
import type { VerifyResult } from '../hooks/useForecastOverlay';

/** One model's overlay: its path and the score it earned. */
export interface ChartRun {
  method: string;
  mape: number;
  points: { t: number; forecast: number }[];
}

export interface ChartPoint {
  t: number;
  actual: number;
  forecast: number;
}

/**
 * The forecast chart section, extracted from the forecast panel.
 *
 * It owns nothing: the runs, the overlay toggles and the graded result come in as
 * props, so the section can be rendered, checked and changed without the panel's
 * request state. The anchor is prepended to every series here, once, so the chart
 * and the table cannot disagree about where a forecast starts.
 */
export const ForecastChartSection = memo(function ForecastChartSection({
  graded,
  points,
  runs,
  showFan,
  showSigma,
  showError,
  rmse,
  notRun,
}: {
  graded: { result: VerifyResult | null; error: string | null };
  points: ChartPoint[];
  runs: ChartRun[];
  showFan: boolean;
  showSigma: boolean;
  showError: boolean;
  rmse: number | undefined;
  notRun: string;
}) {
  const result = graded.result;
  const modelCount = runs.length;
  const plural = modelCount === 1 ? '' : 's';

  if (result === null) return <p className="fl-hint">{notRun}</p>;
  if (graded.error !== null) return <p className="fl-hint">forecast unavailable — {graded.error}</p>;
  if (points.length === 0) return <p className="fl-hint">waiting for the first forecast…</p>;

  const anchorT = result.anchor_t;
  const anchorPrice = result.anchor_price;
  const withAnchor = (values: number[]): number[] =>
    anchorPrice !== undefined ? [anchorPrice, ...values] : values;

  return (
    <ChartPanel title="forecast" meta={<span>{result.symbol} · {modelCount} model{plural}</span>}>
      <div className="fl-chart-wrap">
        <ForecastChart
          times={anchorT !== undefined ? [anchorT, ...points.map((p) => p.t)] : points.map((p) => p.t)}
          actual={withAnchor(points.map((p) => p.actual))}
          forecasts={runs.map((r) => ({ label: r.method, values: withAnchor(r.points.map((p) => p.forecast)), mape: r.mape }))}
          sigma={showSigma ? rmse : undefined}
          paths={result.paths?.map((pth: number[]) => withAnchor(pth)) ?? null}
          history={result.history}
          historyTimes={result.history_times}
          showWhiskers={showError}
          fan={
            !showFan
              ? null
              : result.fan !== null && result.fan !== undefined && anchorPrice !== undefined
                ? {
                    p10: [anchorPrice, ...result.fan.p10],
                    p25: [anchorPrice, ...result.fan.p25],
                    p50: [anchorPrice, ...result.fan.p50],
                    p75: [anchorPrice, ...result.fan.p75],
                    p90: [anchorPrice, ...result.fan.p90],
                  }
                : result.fan ?? null
          }
        />
      </div>
    </ChartPanel>
  );
});
