import { memo, useEffect, useState } from 'react';
import { num, pct, px, usd } from '../format';
import { useSelection } from '../selection';
import type { ToolsMsg } from '../types';
import { ChartIcon } from './ChartIcon';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

/** One mover row — the shape GET /api/movers publishes per symbol. */
interface Mover {
  symbol: string;
  price: number;
  change_pct_24h: number;
  quote_volume_24h: number;
  high_24h: number;
  low_24h: number;
  /** 0 at the 24h low, 1 at the high — where the price sits inside today's range. */
  range_position: number;
  tradable: boolean;
}

interface MoversMsg {
  gainers: Mover[];
  losers: Mover[];
  scanned: number;
  tradable: number;
  min_quote_volume: number;
}

async function fetchMovers(): Promise<MoversMsg | null> {
  try {
    const res = await fetch('/api/movers?limit=5', { headers: { accept: 'application/json' } });
    if (!res.ok) return null;
    return (await res.json()) as MoversMsg;
  } catch {
    return null;
  }
}

function MoverRow({ m, tools }: { m: Mover; tools: ToolsMsg | null }) {
  const selection = useSelection();
  const up = m.change_pct_24h >= 0;
  return (
    <li className="turtle-row">
      <button
        type="button"
        className="turtle-sym num"
        onClick={() => selection.selectSymbol(m.symbol)}
        title={`chart ${m.symbol}`}
      >
        <ChartIcon /> {m.symbol}
      </button>
      <span className={`num ${up ? 'up' : 'down'}`}>{pct(m.change_pct_24h)}</span>
      <span className="num">{px(m.price)}</span>
      <Tooltip
        keys={['range_24h']}
        tools={tools}
        fallbackTitle="24h range position"
        label={`Where the price sits inside the 24h range: 0 at ${px(m.low_24h)}, 1 at ${px(m.high_24h)}.`}
        value={num(m.range_position, 2)}
        tone="stat"
      >
        <span className="num mover-range" tabIndex={0}>
          <span
            className="mover-range-dot"
            style={{ left: `${Math.round(Math.min(1, Math.max(0, m.range_position)) * 100)}%` }}
            aria-hidden="true"
          />
        </span>
      </Tooltip>
      <span className="num">{usd(m.quote_volume_24h)}</span>
    </li>
  );
}

/**
 * The 24h movers: greatest gainers and losers, with the range-position read so a
 * +20% that is still AT its low reads differently from one pinned at its high.
 */
export const MoversPanel = memo(function MoversPanel({ tools }: { tools: ToolsMsg | null }) {
  const [movers, setMovers] = useState<MoversMsg | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      void fetchMovers().then((m) => {
        if (alive) setMovers(m);
      });
    };
    tick();
    const id = window.setInterval(tick, 10_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const side = (rows: Mover[], title: string) => (
    <section className="movers-side">
      <h3 className="cp-data-heading">{title}</h3>
      <ul className="turtle-rows">
        {rows.map((m) => (
          <MoverRow key={m.symbol} m={m} tools={tools} />
        ))}
      </ul>
    </section>
  );

  return (
    <ResizablePanel id="movers.panel" axis="y" minHeight={160} maxHeight={900}>
      {() => (
        <>
          <div className="panel-head">
            <h2 className="panel-title">24h movers</h2>
            {movers !== null && (
              <span className="turtle-rules">
                {num(movers.scanned, 0)} scanned · {num(movers.tradable, 0)} tradable · min {usd(movers.min_quote_volume)}
              </span>
            )}
          </div>
          <div className="survey-body movers-body">
            {movers === null ? (
              <p className="empty">unavailable</p>
            ) : (
              <div className="movers-cols">
                {side(movers.gainers, 'gainers')}
                {side(movers.losers, 'losers')}
              </div>
            )}
          </div>
        </>
      )}
    </ResizablePanel>
  );
});
