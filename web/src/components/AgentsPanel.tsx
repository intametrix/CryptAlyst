import { memo, useEffect, useMemo, useState } from 'react';
import { age, int, text } from '../format';
import { ResizablePanel } from './Panel';

/** One enrolled CryptALAS agent from GET /api/agents. */
interface AgentInfo {
  id: string;
  host: string;
  name: string;
  ip: string;
  device: string;
  arch: string;
  num_cpu: number;
  mem_total: number;
  gpu_name: string;
  gpu_vram: string;
  gpu_count: number;
  go_version: string;
  git_sha: string;
  gpu_present: boolean;
  mem_used: number;
  cpu_util: number;
  gpu_util: number;
  gpu_mem_used: number;
  capabilities: string[];
  interfaces: { name: string; ip: string; mac: string; up: boolean }[];
  privileges: string[];
  net_rx_bps: number;
  net_tx_bps: number;
  jobs_queued: number;
  jobs_running: number;
  jobs_done: number;
  jobs_failed: number;
  first_seen_ms: number;
  last_seen_ms: number;
}

const DEVICE_LABEL: Record<string, string> = {
  auto: 'auto', cpu: 'CPU', gpu: 'GPU', cuda: 'CUDA', metal: 'Metal',
};

function gib(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return '—';
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function bps(v: unknown): string {
  const n = Number(v) || 0;
  if (n < 1024) return `${n.toFixed(0)} B/s`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB/s`;
  return `${(n / 1024 / 1024).toFixed(1)} MB/s`;
}

function pct1(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${n.toFixed(0)}%`;
}

/** A small horizontal utilisation bar. */
function Bar({ value, tone }: { value: number; tone?: 'down' }) {
  const w = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <span className="ag-bar" aria-hidden="true">
      <span className={`ag-bar-fill${tone === 'down' ? ' down' : ''}`} style={{ width: `${w}%` }} />
    </span>
  );
}

function gpuLabel(a: AgentInfo): string {
  if (!a.gpu_name && !a.gpu_count) return '—';
  const parts: string[] = [];
  if (a.gpu_count > 0) parts.push(`${int(a.gpu_count)}×`);
  if (a.gpu_name) parts.push(text(a.gpu_name));
  return parts.join(' ');
}

/**
 * Agents — the enrolled CryptALAS fleet as cards. Each card shows identity,
 * live resources (CPU/mem/GPU with utilisation bars), capabilities, and the
 * running-task counts. Polls GET /api/agents.
 */
export const AgentsPanel = memo(function AgentsPanel() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [live, setLive] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [rawFor, setRawFor] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    const tick = async () => {
      try {
        const res = await fetch('/api/agents', { headers: { accept: 'application/json' } });
        if (!res.ok) { if (!dead) setLive(false); return; }
        const body = (await res.json()) as { agents?: AgentInfo[] };
        if (!dead) {
          setAgents(Array.isArray(body.agents) ? body.agents : []);
          setLive(true);
        }
      } catch { if (!dead) setLive(false); }
    };
    void tick();
    const poll = window.setInterval(() => void tick(), 5000);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { dead = true; window.clearInterval(poll); window.clearInterval(clock); };
  }, []);

  const sorted = useMemo(
    () => agents.slice().sort((a, b) => (b.last_seen_ms ?? 0) - (a.last_seen_ms ?? 0)),
    [agents],
  );

  return (
    <ResizablePanel id="agents.panel" axis="x" minWidth={320} maxWidth={1600} fillHeight>
      {() => (
        <section className="panel agents">
          <div className="panel-head">
            <h2>agents</h2>
            <span className="sub num">{int(sorted.length)} enrolled</span>
          </div>

          {!live ? (
            <p className="empty">agent registry unavailable — /api/agents isn't answering</p>
          ) : sorted.length === 0 ? (
            <p className="empty">no agents enrolled — run <code>cryptalas</code> on a box; it multicasts its beacon</p>
          ) : (
            <div className="ag-cards">
              {sorted.map((a) => {
                const key = a.id || a.host;
                const open = rawFor === key;
                return (
                <article key={key} className={`ag-card${open ? ' open' : ''}`} onClick={() => setRawFor(open ? null : key)}>
                  <header className="ag-card-head">
                    <div className="ag-title">
                      <span className="ag-name">{text(a.name) || text(a.host)}</span>
                      <span className={`pill${a.gpu_present ? ' pill-live' : ''}`}>
                        {DEVICE_LABEL[a.device] ?? text(a.device)}
                      </span>
                    </div>
                    <span className="num ag-seen" title={`last seen ${a.last_seen_ms}`}>
                      {age(a.last_seen_ms, now)}
                    </span>
                  </header>

                  <div className="ag-meta">
                    <span title={a.host}>{text(a.ip) || '—'}</span>
                    <span className="ag-sep">·</span>
                    <span title={a.host}>{text(a.host)}</span>
                    <span className="ag-sep">·</span>
                    <span>{text(a.arch)}</span>
                    <span className="ag-sep">·</span>
                    <span title={a.git_sha}>{a.git_sha ? a.git_sha.slice(0, 7) : '—'}</span>
                  </div>

                  <div className="ag-res">
                    <div className="ag-row">
                      <span className="ag-label">CPU</span>
                      <span className="num">{int(a.num_cpu)} core{Number(a.num_cpu) === 1 ? '' : 's'}</span>
                      <span className="num ag-val">{pct1(a.cpu_util)}</span>
                      <Bar value={a.cpu_util} />
                    </div>
                    <div className="ag-row">
                      <span className="ag-label">MEM</span>
                      <span className="num">{gib(a.mem_used)} / {gib(a.mem_total)}</span>
                      <Bar value={a.mem_total > 0 ? (Number(a.mem_used || 0) / a.mem_total) * 100 : 0} />
                    </div>
                    {a.gpu_count > 0 && (
                      <div className="ag-row">
                        <span className="ag-label">GPU</span>
                        <span className="num">{gpuLabel(a)}</span>
                        <span className="num ag-val">{pct1(a.gpu_util)}</span>
                        <Bar value={a.gpu_util} />
                      </div>
                    )}
                    <div className="ag-row">
                      <span className="ag-label">NET</span>
                      <span className="num">↓ {bps(a.net_rx_bps)} · ↑ {bps(a.net_tx_bps)}</span>
                    </div>
                  </div>

                  {Array.isArray(a.capabilities) && a.capabilities.length > 0 && (
                    <div className="ag-caps">
                      {a.capabilities.map((c) => (
                        <span key={c} className="ag-cap">{text(c)}</span>
                      ))}
                    </div>
                  )}
                  {Array.isArray(a.privileges) && a.privileges.length > 0 && (
                    <div className="ag-caps ag-privs">
                      {a.privileges.map((p) => (
                        <span key={p} className="ag-cap ag-priv">{text(p)}</span>
                      ))}
                    </div>
                  )}

                  <footer className="ag-tasks">
                    <span>tasks</span>
                    <span className="num">queued {int(a.jobs_queued)}</span>
                    <span className="num">running {int(a.jobs_running)}</span>
                    <span className="num ag-done">done {int(a.jobs_done)}</span>
                    <span className="num ag-fail">failed {int(a.jobs_failed)}</span>
                  </footer>

                  {open && (
                    <div className="ag-detail" onClick={(e) => e.stopPropagation()}>
                      {Array.isArray(a.interfaces) && a.interfaces.length > 0 && (
                        <div className="ag-ifaces">
                          {a.interfaces.map((n) => (
                            <span key={n.name} className="ag-iface" title={n.mac}>
                              {text(n.name)} <span className="num">{text(n.ip) || '—'}</span>
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="ag-detail-head">
                        <span className="ag-label">raw beacon JSON</span>
                        <button type="button" className="ag-copy" onClick={() => void navigator.clipboard?.writeText(JSON.stringify(a, null, 2))}>
                          copy
                        </button>
                      </div>
                      <pre className="ag-raw">{JSON.stringify(a, null, 2)}</pre>
                    </div>
                  )}
                </article>
                );
              })}
            </div>
          )}
        </section>
      )}
    </ResizablePanel>
  );
});
