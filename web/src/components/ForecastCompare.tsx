import { memo } from 'react';
import { priceLabel } from './ForecastChart';

export interface ComparePoint {
  t: number;
  forecast: number;
  actual: number;
  error: number;
}

export interface CompareErrors {
  mape_pct?: number;
  mae?: number;
  rmse?: number;
  bias?: number;
  max_error?: number;
}

/**
 * Forecast against what actually happened, bar by bar.
 *
 * Extracted from the forecast panel: it is a pure view over graded points, so it has
 * no reason to live inside the component that owns the request, the chart and the
 * model picker.
 */
export const ForecastCompare = memo(function ForecastCompare({
  method,
  symbol,
  source,
  device,
  compute,
  lambda,
  points,
  errs,
}: {
  method: string;
  symbol: string;
  source?: string | null;
  device?: string | null;
  compute?: string | null;
  lambda?: number | null;
  points: ComparePoint[];
  errs: CompareErrors | undefined;
}) {
  if (points.length === 0) return null;
  return (
    <div className="fl-cmp">
      <div className="fl-cmp-head">
        <span>
          forecast vs actual · {method} · {symbol}
        </span>
        <span className="num fl-cmp-mape">
          {source === 'agent' ? 'agent' : 'platform'} · {device ?? 'cpu'}/{compute ?? 'go'}
          {' '}· MAPE {(errs?.mape_pct ?? 0).toFixed(3)}%
          {lambda !== undefined && lambda !== null ? ` · λ ${lambda.toFixed(2)}` : ''}
        </span>
      </div>
      <div className="fl-cmp-table">
        <div className="fl-cmp-row fl-cmp-row-head">
          <span>#</span><span>forecast</span><span>actual</span><span>error</span>
        </div>
        {points.map((p, i) => (
          <div className="fl-cmp-row" key={p.t}>
            <span className="num">{i + 1}</span>
            <span className="num fl-cmp-fc">{priceLabel(p.forecast)}</span>
            <span className="num fl-cmp-real">{priceLabel(p.actual)}</span>
            <span className={`num ${p.error >= 0 ? 'fl-over' : 'fl-under'}`}>
              {p.error >= 0 ? '+' : ''}{priceLabel(p.error)}
            </span>
          </div>
        ))}
      </div>
      <div className="fl-cmp-stats">
        <span>MAE <b className="num">{priceLabel(errs?.mae ?? 0)}</b></span>
        <span>RMSE <b className="num">{priceLabel(errs?.rmse ?? 0)}</b></span>
        <span>bias <b className="num">{(errs?.bias ?? 0) >= 0 ? '+' : ''}{priceLabel(errs?.bias ?? 0)}</b></span>
        <span>max <b className="num">{priceLabel(errs?.max_error ?? 0)}</b></span>
      </div>
    </div>
  );
});
