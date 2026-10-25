import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { clock, int, num, pct } from '../format';
import { subscribeStreamTally } from '../api';
import type { StatusMsg } from '../types';
import { ResizablePanel } from './Panel';

interface Props {
  status: StatusMsg | null;
}

/** GET /api/bus — recorder snapshot. */
interface BusStats {
  received?: number | null;
  written?: number | null;
  refused?: number | null;
  rate_per_s?: number | null;
  by_topic?: Record<string, number> | null;
  ts_ms?: number | null;
}

type BusSortKey = 'topic' | 'msgs' | 'share';

/**
 * Message bus — a sortable data grid of the pipeline's topics: message count,
 * share of the whole, and a relative volume bar. The top strip is live from
 * the SSE status event; the grid fills from GET /api/bus.
 */
export const BusPanel = memo(function BusPanel({ status }: Props) {
  const [bus, setBus] = useState<BusStats | null>(null);
  const [busLive, setBusLive] = useState(false);
  const [sortKey, setSortKey] = useState<BusSortKey>('msgs');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Live per-type tally of every SSE message this browser session receives.
  const [tally, setTally] = useState<Record<string, number>>({});
  const tallyRef = useRef<Record<string, number>>({});
  useEffect(() => {
    const unsub = subscribeStreamTally((type) => {
      tallyRef.current[type] = (tallyRef.current[type] ?? 0) + 1;
    });
    const id = window.setInterval(() => setTally({ ...tallyRef.current }), 1000);
    return () => {
      unsub();
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    let dead = false;
    const tick = async () => {
      try {
        const res = await fetch('/api/bus', { headers: { accept: 'application/json' } });
        if (!res.ok) {
          if (!dead) setBusLive(false);
          return;
        }
        const body = (await res.json()) as BusStats;
        if (!dead) {
          setBus(body);
          setBusLive(true);
        }
      } catch {
        if (!dead) setBusLive(false);
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 3000);
    return () => {
      dead = true;
      window.clearInterval(id);
    };
  }, []);

  const topics = useMemo(() => {
    const bt = bus?.by_topic ?? {};
    return Object.entries(bt)
      .map(([name, count]) => ({ name, count: count ?? 0 }))
      .filter((r) => r.name !== 'refused');
  }, [bus]);

  const total = topics.reduce((s, r) => s + r.count, 0);
  const peak = topics.reduce((m, r) => Math.max(m, r.count), 1);

  const sorted = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    const rows = topics.slice();
    rows.sort((a, b) => {
      if (sortKey === 'topic') return dir * a.name.localeCompare(b.name);
      const av = sortKey === 'msgs' ? a.count : total > 0 ? a.count / total : 0;
      const bv = sortKey === 'msgs' ? b.count : total > 0 ? b.count / total : 0;
      return dir * (av - bv);
    });
    return rows;
  }, [topics, sortKey, sortDir, total]);

  const toggleSort = (key: BusSortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortKey(key);
    setSortDir(key === 'topic' ? 'asc' : 'desc');
  };

  const headerFor = (key: BusSortKey, label: string, numeric: boolean) => {
    const active = sortKey === key;
    const arrow = active ? (sortDir === 'asc' ? '▲' : '▼') : '▲▼';
    return (
      <th
        key={key}
        scope="col"
        className={`${numeric ? 'num-col' : ''}${active ? ' sorted' : ''}`}
        aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        <button type="button" className="th-btn" onClick={() => toggleSort(key)} title={`Sort by ${label}`}>
          {label}
          <span className={`arrow${active ? ' arrow-active' : ''}`} aria-hidden="true">
            {arrow}
          </span>
        </button>
      </th>
    );
  };

  const inTotal = status?.stream_messages ?? null;
  const outTotal = status?.messages ?? null;

  const tallyRows = useMemo(
    () => Object.entries(tally).sort((a, b) => b[1] - a[1]),
    [tally],
  );
  const tallyPeak = tallyRows.reduce((m, [, v]) => Math.max(m, v), 1);

  return (
    <ResizablePanel id="bus.panel" axis="x" minWidth={320} maxWidth={1600} fillHeight>
      {() => (
        <section className="panel bus">
          <div className="panel-head">
            <h2>message bus</h2>
            <span className="sub num" title={`status ts_ms ${status?.ts_ms ?? '—'}`}>
              {status?.ts_ms != null ? clock(status.ts_ms) : '—'} · {int(status?.uptime_sec)}s up
            </span>
          </div>

          <div className="bus-strip">
            <span className="bus-stat">
              <b className="num">{int(inTotal)}</b> in
              <i className="num">({num(status?.stream_streams, 0)} streams)</i>
            </span>
            <span className="bus-stat">
              <b className="num">{int(outTotal)}</b> out
            </span>
            <span className="bus-stat">
              <b className="num">{int(status?.truncated)}</b> truncated
            </span>
            <span className="bus-stat">
              <b className="num">{int(status?.clients)}</b> clients
            </span>
            <span className="bus-stat">
              <b className="num">{num(bus?.rate_per_s, 1)}</b>/s
              <i className="num">{busLive ? 'bus api live' : 'bus api pending'}</i>
            </span>
          </div>

          {!busLive ? (
            <p className="empty">
              per-topic detail arrives with the backend GET /api/bus handler — the in/out totals
              above are live from the status stream
            </p>
          ) : (
            <table className="bus-grid">
              <thead>
                <tr>
                  {headerFor('topic', 'topic', false)}
                  {headerFor('msgs', 'msgs', true)}
                  {headerFor('share', 'share', true)}
                  <th scope="col" className="num-col">flow</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(({ name, count }) => (
                  <tr key={name}>
                    <td className="bus-topic" title={name}>{name}</td>
                    <td className="num">{int(count)}</td>
                    <td className="num">{pct(total > 0 ? count / total : 0)}</td>
                    <td className="bus-bar-cell">
                      <span className="bus-bar" title={`${int(count)} msgs`}>
                        <span
                          className="bar-fill"
                          style={{ width: `${Math.max(2, (count / peak) * 100)}%` }}
                        />
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {tallyRows.length > 0 && (
            <div className="bus-tally">
              <div className="col-head">queue (this session)</div>
              <div className="bus-rows">
                {tallyRows.map(([type, count]) => (
                  <div key={type} className="bus-row" role="row">
                    <span className="bus-topic">{type}</span>
                    <span className="num">{int(count)}</span>
                    <span className="num">{pct(tallyPeak > 0 ? count / tallyPeak : 0)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <p className="bus-note">in = raw stream messages consumed · out = envelopes processed · truncated = dropped by backpressure · session tally counts every SSE message as it lands</p>
        </section>
      )}
    </ResizablePanel>
  );
});

export type { BusStats };
