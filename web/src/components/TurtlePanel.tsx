import { memo, useCallback, useEffect, useState } from 'react';
import { int, num, px, usd } from '../format';
import type { ToolsMsg } from '../types';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

/**
 * The Turtle trading session panel: state, controls, PnL with its disclosure,
 * the watchlist the session decided on, open positions, and both trades and
 * orders — including REJECTED orders with their reasons, because a filter
 * nobody can see is indistinguishable from a bug.
 *
 * Everything renders from what GET /api/turtle publishes (config included), so
 * the UI cannot drift from the engine.
 */

interface TurtleConfig {
  starting_equity?: number | null;
  risk_per_unit?: number | null;
  stop_n?: number | null;
  entry_lookback?: number | null;
  exit_lookback?: number | null;
  max_units_per_symbol?: number | null;
  vol_veto_pct?: number | null;
  max_concurrent?: number | null;
}

interface TurtlePosition {
  symbol: string;
  side: string;
  units: number;
  entry_price: number;
  stop_price: number;
  n: number;
  notional: number;
  opened_at: string;
  last: number;
}

interface TurtleWatch {
  symbol: string;
  score: number;
  reasons: string[];
}

interface TurtleState {
  state: string;
  config: TurtleConfig | null;
  started_at: string | null;
  stopped_at: string | null;
  equity: number;
  cash: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  return_pct: number;
  peak_equity: number;
  max_drawdown_pct: number;
  /** Null while idle — the wire sends null, not [], before a session starts. */
  positions: TurtlePosition[] | null;
  watchlist: TurtleWatch[] | null;
  entries_filled: number;
  entries_vetoed: number;
  entries_blocked: number;
  note: string;
}

interface Trade {
  symbol: string;
  side: string;
  units: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  opened_at: string;
  closed_at: string;
  reason: string;
  hold_sec: number;
}

interface TurtleStats {
  win_rate_pct: number;
  expectancy_usdt: number;
  veto_rate_pct: number;
  watchlist_size: number;
}

interface Order {
  id: string;
  symbol: string;
  side: string;
  type: string;
  units: number;
  price: number;
  status: string;
  reason: string;
  created_at: string;
}

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url, { headers: { accept: 'application/json' } });
  if (!res.ok) throw new Error(`http ${res.status}`);
  return res.json();
}

function agoSec(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  return `${int((Date.now() - t) / 1000)}s`;
}

export const TurtlePanel = memo(function TurtlePanel({ tools }: { tools: ToolsMsg | null }) {
  const [session, setSession] = useState<TurtleState | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [stats, setStats] = useState<TurtleStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const running = session?.state === 'running';

  const refresh = useCallback(async () => {
    try {
      const [s, t, o, st] = await Promise.all([
        fetchJson('/api/turtle'),
        fetchJson('/api/turtle/trades'),
        fetchJson('/api/turtle/orders'),
        fetchJson('/api/turtle/stats'),
      ]);
      setStats(st as TurtleStats);
      setSession(s as TurtleState);
      setTrades(((t as { trades?: Trade[] }).trades ?? []).slice(0, 30));
      setOrders(((o as { orders?: Order[] }).orders ?? []).slice(0, 30));
      setError(null);
    } catch (e) {
      setError(String(e).slice(0, 80));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), running ? 3000 : 10000);
    return () => window.clearInterval(id);
  }, [refresh, running]);

  const act = useCallback(
    async (action: 'start' | 'stop') => {
      setBusy(true);
      try {
        const res = await fetch(`/api/turtle?action=${action}`, { method: 'POST' });
        if (!res.ok) throw new Error(`http ${res.status}`);
        setSession((await res.json()) as TurtleState);
      } catch (e) {
        setError(String(e).slice(0, 80));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  // The wire sends null (not []) for several collections while idle; treat
  // every collection as possibly-null so one idle payload cannot blank the app.
  const cfg = session?.config ?? {};
  const positions = session?.positions ?? [];
  const watchlist = session?.watchlist ?? [];
  const rules = [
    cfg.starting_equity !== undefined && cfg.starting_equity !== null ? `${usd(cfg.starting_equity)} start` : null,
    cfg.risk_per_unit !== undefined && cfg.risk_per_unit !== null ? `${num(cfg.risk_per_unit * 100, 0)}% risk/unit` : null,
    cfg.stop_n !== undefined && cfg.stop_n !== null ? `${num(cfg.stop_n, 0)}N stop` : null,
    cfg.entry_lookback !== undefined && cfg.entry_lookback !== null && cfg.exit_lookback !== null
      ? `${int(cfg.entry_lookback)}/${int(cfg.exit_lookback)} channels`
      : null,
    cfg.max_concurrent !== undefined && cfg.max_concurrent !== null ? `max ${int(cfg.max_concurrent)} positions` : null,
    cfg.vol_veto_pct !== undefined && cfg.vol_veto_pct !== null ? `vol veto ${num(cfg.vol_veto_pct, 1)}%` : null,
  ]
    .filter((x): x is string => x !== null)
    .join(' · ');

  return (
    <ResizablePanel id="turtle.panel" axis="y" minHeight={220} maxHeight={1400}>
      {() => (
        <>
          <div className="panel-head">
            <h2 className="panel-title">
              turtle <span className={`turtle-state ${running ? 'running' : (session?.state ?? 'idle')}`}>{session?.state ?? '…'}</span>
            </h2>
            <div className="actions">
              {running ? (
                <button type="button" className="btn btn-warn" disabled={busy} onClick={() => void act('stop')}>
                  stop
                </button>
              ) : (
                <button type="button" className="btn" disabled={busy} onClick={() => void act('start')}>
                  start
                </button>
              )}
            </div>
          </div>

          <div className="survey-body turtle-body">
            {error !== null && <p className="turtle-error">{error}</p>}
            {session === null ? (
              <p className="empty">no session yet — start one</p>
            ) : (
              <>
                <div className="turtle-stats">
                  <span className="num">equity {usd(session.equity)}</span>
                  <span className={`num ${session.total_pnl >= 0 ? 'up' : 'down'}`}>
                    pnl {usd(session.total_pnl)} ({num(session.return_pct, 3)}%)
                  </span>
                  <span className="num">realized {usd(session.realized_pnl)}</span>
                  <span className="num">unrealized {usd(session.unrealized_pnl)}</span>
                  <span className="num">drawdown {num(session.max_drawdown_pct, 3)}%</span>
                  {stats !== null && stats.expectancy_usdt !== 0 && (
                    <Tooltip
                      keys={['expectancy']}
                      tools={tools}
                      fallbackTitle="Expectancy"
                      label="Average PnL per closed trade — the number that decides whether the system has an edge. A system can win most trades and still lose money; expectancy is the honest summary."
                      tone="stat"
                    >
                      <span className="num" tabIndex={0}>
                        expectancy {usd(stats.expectancy_usdt)} / trade
                      </span>
                    </Tooltip>
                  )}
                  {stats !== null && stats.veto_rate_pct !== 0 && (
                    <span className="num">veto rate {num(stats.veto_rate_pct, 0)}%</span>
                  )}
                </div>
                <p className="turtle-rules">{rules}</p>
                {session.note !== '' && <p className="turtle-note">{session.note}</p>}
                <div className="turtle-counters">
                  <span className="num">entries filled {int(session.entries_filled)}</span>
                  <Tooltip
                    keys={['volatility_veto']}
                    tools={tools}
                    fallbackTitle="Vetoed entries"
                    label="Alerts the volatility filter refused. The veto exists because alerts in high-vol symbols tend to mark the END of a move (mean reversion) — a 2N stop is exactly what that takes out. Vetoed going up is the system working, not a failure count."
                    tone="stat"
                  >
                    <span className="num" tabIndex={0}>vetoed {int(session.entries_vetoed)}</span>
                  </Tooltip>
                  <span className="num">blocked {int(session.entries_blocked)}</span>
                </div>

                <h3 className="cp-data-heading">positions ({int(positions.length)})</h3>
                {positions.length === 0 ? (
                  <p className="empty">flat</p>
                ) : (
                  <ul className="turtle-rows">
                    {positions.map((p) => (
                      <li key={`${p.symbol}-${p.opened_at}`} className="turtle-row">
                        <span className="num">{p.symbol} {p.side}</span>
                        <span className="num">{int(p.units)}u @{px(p.entry_price)}</span>
                        <span className="num">stop {px(p.stop_price)}</span>
                        <span className="num">n {num(p.n, 4)}</span>
                        <span className="num">{usd(p.notional)}</span>
                        <span className="num">{agoSec(p.opened_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}

                <h3 className="cp-data-heading">watchlist ({int(watchlist.length)})</h3>
                <ul className="turtle-rows">
                  {watchlist.slice(0, 15).map((w) => {
                    const fallback = w.reasons.some((r) => r.includes('liquidity fallback'));
                    return (
                      <li key={w.symbol} className={`turtle-row${fallback ? ' turtle-fallback' : ''}`}>
                        <span className="num">{w.symbol}</span>
                        <span className="num">score {num(w.score, 2)}</span>
                        <span className="turtle-reasons">
                          {fallback ? 'watched for liquidity — not a pick' : w.reasons.join(' · ')}
                        </span>
                      </li>
                    );
                  })}
                </ul>

                <h3 className="cp-data-heading">trades ({int(trades.length)} shown)</h3>
                {trades.length === 0 ? (
                  <p className="empty">none yet</p>
                ) : (
                  <ul className="turtle-rows">
                    {trades.map((t, i) => (
                      <li key={`${t.symbol}-${t.opened_at}-${i}`} className="turtle-row">
                        <span className="num">{t.symbol} {t.side}</span>
                        <span className={`num ${t.pnl >= 0 ? 'up' : 'down'}`}>
                          {usd(t.pnl)} ({num(t.pnl_pct, 2)}%)
                        </span>
                        <span className="num">{int(t.hold_sec)}s</span>
                        <span className="turtle-reasons">{t.reason}</span>
                      </li>
                    ))}
                  </ul>
                )}

                <h3 className="cp-data-heading">orders ({int(orders.length)} shown)</h3>
                <ul className="turtle-rows">
                  {orders.map((o) => (
                    <li key={o.id} className={`turtle-row${o.status === 'rejected' ? ' turtle-rejected' : ''}`}>
                      <span className="num">{o.id} {o.symbol} {o.side} {o.type}</span>
                      <span className="num">{o.status}</span>
                      <span className="turtle-reasons">{o.reason}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </>
      )}
    </ResizablePanel>
  );
});
