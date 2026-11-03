import { memo } from 'react';
import { num, text } from '../format';

export interface ModelScore {
  model: string;
  note: string;
  mape: number;
  band80: number;
  coverage80: number;
}

/**
 * The model leaderboard: every model's error on the same walk-forward windows.
 *
 * Extracted from the forecast panel, which had grown to hold nine sections in one
 * file. It takes scores and the best model and nothing else — no selection state, no
 * fetch — so it can be read, tested and changed without touching the panel.
 */
export const ModelRanking = memo(function ModelRanking({
  scores,
  best,
  windowCount,
  points,
}: {
  scores: ModelScore[];
  best: string | null | undefined;
  windowCount: number;
  points: number;
}) {
  if (scores.length === 0) return null;
  const peak = scores[0]?.mape || 1;

  return (
    <>
      <div className="fl-table" role="table" aria-label="model leaderboard">
        <div className="fl-row fl-row-head" role="row">
          <span role="columnheader">model</span>
          <span role="columnheader">mape</span>
          <span role="columnheader">80% band</span>
          <span role="columnheader">cov</span>
        </div>
        {scores.map((s) => {
          const width = Math.max(2, Math.min(100, (s.mape / peak) * 100));
          return (
            <div className={`fl-row${s.model === best ? ' is-best' : ''}`} role="row" key={s.model}>
              <span className="fl-model" role="cell" title={s.note}>{text(s.model)}</span>
              <span className="fl-metric" role="cell">
                <span className="fl-bar" style={{ width: `${width}%` }} aria-hidden="true" />
                <span className="num">{num(s.mape, 3)}%</span>
              </span>
              <span className="num" role="cell">±{num(s.band80, 0)}</span>
              <span className={`num${s.coverage80 >= 0.75 && s.coverage80 <= 0.85 ? ' fl-cov-ok' : ''}`} role="cell">
                {num(s.coverage80 * 100, 0)}%
              </span>
            </div>
          );
        })}
      </div>
      <p className="fl-note">
        graded on {windowCount} walk-forward windows · {points} bars · out-of-sample only
      </p>
    </>
  );
});
