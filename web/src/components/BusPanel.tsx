import { memo, useEffect, useMemo, useState } from 'react';
import { clock, int, num, pct } from '../format';
import type { StatusMsg } from '../types';
import { ResizablePanel } from './Panel';

interface Props {
  status: StatusMsg | null;
}

/** GET /api/bus — recorder snapshot; absent until the backend handler lands. */
interface BusStats {
  received?: number | null;
  written?: number | null;
  refused?: number | null;
  rate_per_s?: number | null;
  by_topic?: Record<string, number> | null;
  ts_ms?: number | null;
}

/**
 * Message bus — messages in/out for the whole pipeline. The top strip is live
 * from the SSE status event (stream messages in, processed out, truncated,
 * clients); the per-topic table fills from GET /api/bus once the backend
 * handler lands, and says so honestly until then.
 */
export const BusPanel = memo(function BusPanel({ status }: Props) {
  const [bus, setBus] = useState<BusStats | null>(null);
  const [busLive, setBusLive] = useState(false);

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
    return Object.entries(bt).sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0));
  }, [bus]);
  const peak = topics.reduce((m, [, v]) => Math.max(m, v ?? 0), 1);
  const total = topics.reduce((s, [, v]) => s + (v ?? 0), 0);

  const inTotal = status?.stream_messages ?? null;
  const outTotal = status?.messages ?? null;

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
            <div className="bus-rows">
              <div className="bus-row bus-head" role="row">
                <span className="col-head">topic</span>
                <span className="col-head num">msgs</span>
                <span className="col-head num">share</span>
                <span className="col-head">flow</span>
                {bus?.refused != null && <span className="col-head num">refused</span>}
              </div>
              {topics.map(([name, count]) => (
                <div key={name} className="bus-row" role="row">
                  <span className="bus-topic">{name}</span>
                  <span className="num">{int(count)}</span>
                  <span className="num">{pct(total > 0 ? (count ?? 0) / total : 0)}</span>
                  <span className="bus-bar" title={`${int(count)} msgs`}>
                    <span className="bar-fill" style={{ width: `${Math.max(2, ((count ?? 0) / peak) * 100)}%` }} />
                  </span>
                  {bus?.refused != null && <span className="num bus-refused">{name.startsWith('refused') ? int(count) : ''}</span>}
                </div>
              ))}
              {bus?.refused != null && (
                <div className="bus-row bus-refused-row" role="row">
                  <span className="bus-topic">refused (all topics)</span>
                  <span className="num">{int(bus.refused)}</span>
                  <span className="num">{pct((bus.received ?? 0) > 0 ? (bus.refused ?? 0) / (bus.received ?? 1) : 0)}</span>
                  <span className="bus-bar" />
                  <span />
                </div>
              )}
            </div>
          )}
          <p className="bus-note">in = raw stream messages consumed · out = envelopes processed · truncated = dropped by backpressure</p>
        </section>
      )}
    </ResizablePanel>
  );
});

export type { BusStats };
