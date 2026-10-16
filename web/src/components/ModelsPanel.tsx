import { memo, useEffect, useState } from 'react';
import { clock, int, num } from '../format';
import { useSelection } from '../selection';
import type { ToolsMsg } from '../types';
import { ChartIcon } from './ChartIcon';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

/**
 * The detection models: a HEALTH table (a model that silently stopped and a
 * model finding nothing produce identical empty signal lists — the health
 * numbers are how we tell them apart) plus the flow-imbalance signal feed.
 * Scoped to flow-imbalance + health per the agreed plan; the other models join
 * as their live thresholds are validated.
 */

interface ModelHealth {
  model: string;
  alive: boolean;
  ticks: number;
  signals: number;
  panics: number;
  errors: number;
  slow_ticks: number;
  ms_since_last_tick: number;
  last_error: string;
}

interface ModelSignal {
  model: string;
  symbol: string;
  direction: string;
  score: number;
  reasons: string[];
  at_ms: number;
  detail: { buy_ratio?: number | null; taker_quote?: number | null } | null;
}

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url, { headers: { accept: 'application/json' } });
  if (!res.ok) throw new Error(`http ${res.status}`);
  return res.json();
}

export const ModelsPanel = memo(function ModelsPanel({ tools }: { tools: ToolsMsg | null }) {
  const [health, setHealth] = useState<ModelHealth[]>([]);
  const [signals, setSignals] = useState<ModelSignal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const selection = useSelection();

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const [h, s] = await Promise.all([fetchJson('/api/models'), fetchJson('/api/models/signals')]);
        if (!alive) return;
        setHealth(((h as { models?: ModelHealth[] }).models ?? []).slice().sort((a, b) => b.signals - a.signals));
        setSignals(
          ((s as { signals?: ModelSignal[] }).signals ?? [])
            .filter((sig) => sig.model === 'flow-imbalance')
            .slice(0, 25),
        );
        setError(null);
      } catch (e) {
        if (alive) setError(String(e).slice(0, 80));
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 3000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <ResizablePanel id="models.panel" axis="y" minHeight={200} maxHeight={1200}>
      {() => (
        <>
          <div className="panel-head">
            <h2 className="panel-title">models</h2>
            {error !== null && <span className="turtle-error">{error}</span>}
          </div>
          <div className="survey-body">
            <h3 className="cp-data-heading">health</h3>
            <ul className="turtle-rows">
              <li className="turtle-row turtle-head-row">
                <span>model</span>
                <span>alive</span>
                <span>ticks</span>
                <span>signals</span>
                <span>panics</span>
                <span>slow</span>
                <span>last tick</span>
              </li>
              {health.map((m) => (
                <li key={m.model} className={`turtle-row${m.alive ? '' : ' turtle-rejected'}`}>
                  <span>{m.model}</span>
                  <span className={m.alive ? 'up' : 'down'}>{m.alive ? 'yes' : 'NO'}</span>
                  <span className="num">{int(m.ticks)}</span>
                  <span className="num">{int(m.signals)}</span>
                  <span className="num">{int(m.panics)}</span>
                  <span className="num">{int(m.slow_ticks)}</span>
                  <Tooltip
                    keys={['models']}
                    tools={tools}
                    fallbackTitle={m.model}
                    label={m.last_error !== '' ? `last error: ${m.last_error}` : 'no errors recorded'}
                    value={`${m.ms_since_last_tick}ms ago`}
                    tone="stat"
                  >
                    <span className="num" tabIndex={0}>{int(m.ms_since_last_tick)}ms</span>
                  </Tooltip>
                </li>
              ))}
            </ul>

            <h3 className="cp-data-heading">flow-imbalance signals ({int(signals.length)} shown)</h3>
            {signals.length === 0 ? (
              <p className="empty">none right now — quiet is not the same as broken; the health table above proves the models are running</p>
            ) : (
              <ul className="turtle-rows">
                {signals.map((sig, i) => (
                  <li key={`${sig.symbol}-${sig.at_ms}-${i}`} className="turtle-row">
                    <span className="num">{clock(sig.at_ms)}</span>
                    <button
                      type="button"
                      className="turtle-sym num"
                      onClick={() => selection.selectSymbol(sig.symbol)}
                      title={`chart ${sig.symbol}`}
                    >
                      <ChartIcon /> {sig.symbol}
                    </button>
                    <span className={sig.direction === 'PUMP' ? 'up' : 'down'}>{sig.direction}</span>
                    <span className="num">score {num(sig.score, 2)}</span>
                    <span className="num">
                      buy {num((sig.detail?.buy_ratio ?? 0) * 100, 0)}%
                    </span>
                    <span className="turtle-reasons">{sig.reasons.join(' · ')}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </ResizablePanel>
  );
});
