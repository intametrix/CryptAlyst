import { memo, useEffect, useMemo, useState } from 'react';
import { age, int, text } from '../format';
import { ResizablePanel } from './Panel';

interface NetIface { name: string; ip: string; mac: string; up: boolean }

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
  interfaces: NetIface[];
  privileges: string[];
  net_rx_bps: number;
  net_tx_bps: number;
  disk_total: number;
  disk_free: number;
  ollama_models: string[];
  jobs_queued: number;
  jobs_running: number;
  jobs_done: number;
  jobs_failed: number;
  first_seen_ms: number;
  last_seen_ms: number;
}

const DEVICE_LABEL: Record<string, string> = { auto: 'auto', cpu: 'CPU', gpu: 'GPU', cuda: 'CUDA', metal: 'Metal' };

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
  return `${a.gpu_count > 0 ? `${int(a.gpu_count)}× ` : ''}${text(a.gpu_name)}${a.gpu_vram ? ` ${text(a.gpu_vram)}` : ''}`;
}

function Row({ label, value, bar, sub }: { label: string; value?: string; bar?: number; sub?: string }) {
  return (
    <div className="ag-row">
      <span className="ag-label">{label}</span>
      {value != null && <span className="num ag-val">{value}</span>}
      {bar != null && <Bar value={bar} />}
      {sub != null && <span className="ag-sub">{sub}</span>}
    </div>
  );
}

function Badges({ items, tone }: { items: string[] | undefined; tone?: 'priv' }) {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <div className="ag-caps">
      {items.map((c) => (
        <span key={c} className={`ag-cap${tone === 'priv' ? ' ag-priv' : ''}`}>{text(c)}</span>
      ))}
    </div>
  );
}

export const AgentsPanel = memo(function AgentsPanel() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [live, setLive] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [selected, setSelected] = useState<AgentInfo | null>(null);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [jobResult, setJobResult] = useState('');
  const [jobBusy, setJobBusy] = useState(false);

  useEffect(() => {
    let dead = false;
    const tick = async () => {
      try {
        const res = await fetch('/api/agents', { headers: { accept: 'application/json' } });
        if (!res.ok) { if (!dead) setLive(false); return; }
        const body = (await res.json()) as { agents?: AgentInfo[] };
        if (!dead) { setAgents(Array.isArray(body.agents) ? body.agents : []); setLive(true); }
      } catch { if (!dead) setLive(false); }
    };
    void tick();
    const poll = window.setInterval(() => void tick(), 5000);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { dead = true; window.clearInterval(poll); window.clearInterval(clock); };
  }, []);

  const sorted = useMemo(() => agents.slice().sort((a, b) => (b.last_seen_ms ?? 0) - (a.last_seen_ms ?? 0)), [agents]);

  async function runJob(agent: AgentInfo, type: 'forecast' | 'backtest') {
    setJobBusy(true);
    setJobResult('');
    const payload = type === 'forecast'
      ? { history_ref: { symbol, interval: '1m', limit: 60 }, horizon: 5, method: 'ema' }
      : { history_ref: { symbol, interval: '1m', limit: 100 }, window: 10 };
    try {
      const res = await fetch('/api/compute', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ agent: agent.host, type, model: type === 'forecast' ? 'ts' : 'mom', payload }),
      });
      setJobResult(JSON.stringify(await res.json(), null, 2));
    } catch (e) {
      setJobResult(String(e));
    } finally {
      setJobBusy(false);
    }
  }

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
              {sorted.map((a) => (
                <article key={a.id || a.host} className="ag-card" onClick={() => setSelected(a)}>
                  <header className="ag-card-head">
                    <div className="ag-title">
                      <span className="ag-name">{text(a.name) || text(a.host)}</span>
                      <span className={`pill${a.gpu_present ? ' pill-live' : ''}`}>{DEVICE_LABEL[a.device] ?? text(a.device)}</span>
                    </div>
                    <span className="num ag-seen">{age(a.last_seen_ms, now)}</span>
                  </header>
                  <div className="ag-meta">
                    <span>{text(a.ip) || '—'}</span><span className="ag-sep">·</span>
                    <span>{text(a.arch)}</span><span className="ag-sep">·</span>
                    <span className="num ag-ver">{a.git_sha ? a.git_sha.slice(0, 7) : '—'}</span>
                  </div>
                  <div className="ag-res">
                    <Row label="CPU" value={`${int(a.num_cpu)} cores · ${pct1(a.cpu_util)}`} bar={a.cpu_util} />
                    <Row label="MEM" value={`${gib(a.mem_used)} / ${gib(a.mem_total)}`} bar={a.mem_total > 0 ? (Number(a.mem_used || 0) / a.mem_total) * 100 : 0} />
                    {a.gpu_count > 0 && <Row label="GPU" value={`${gpuLabel(a)} · ${pct1(a.gpu_util)}`} bar={a.gpu_util} />}
                    <Row label="NET" value={`↓ ${bps(a.net_rx_bps)} · ↑ ${bps(a.net_tx_bps)}`} />
                  </div>
                  <Badges items={a.capabilities} />
                  <footer className="ag-tasks">
                    <span>tasks</span>
                    <span className="num">q {int(a.jobs_queued)}</span>
                    <span className="num">run {int(a.jobs_running)}</span>
                    <span className="num ag-done">done {int(a.jobs_done)}</span>
                    <span className="num ag-fail">fail {int(a.jobs_failed)}</span>
                  </footer>
                </article>
              ))}
            </div>
          )}

          {selected && (
            <div className="ag-overlay" onClick={() => setSelected(null)}>
              <div className="ag-detail" onClick={(e) => e.stopPropagation()}>
                <header className="ag-detail-head">
                  <div className="ag-dtitle">
                    <span className="ag-dname">{text(selected.name) || text(selected.host)}</span>
                    <span className="ag-dver">{selected.git_sha || '—'}</span>
                    <span className={`pill${selected.gpu_present ? ' pill-live' : ''}`}>{DEVICE_LABEL[selected.device] ?? text(selected.device)}</span>
                  </div>
                  <button type="button" className="ag-close" onClick={() => setSelected(null)}>✕</button>
                </header>

                <div className="ag-dgrid">
                  <div className="ag-dcol">
                    <div className="ag-sec-title">identity & network</div>
                    <Row label="address" value={text(selected.host)} />
                    <Row label="ip" value={text(selected.ip) || '—'} />
                    <Row label="arch" value={text(selected.arch)} />
                    <Row label="go" value={text(selected.go_version)} />
                    <Row label="seen" value={age(selected.last_seen_ms, now)} />

                    <div className="ag-sec-title">resources</div>
                    <Row label="CPU" value={`${int(selected.num_cpu)} cores · ${pct1(selected.cpu_util)}`} bar={selected.cpu_util} />
                    <Row label="MEM" value={`${gib(selected.mem_used)} / ${gib(selected.mem_total)}`} bar={selected.mem_total > 0 ? (Number(selected.mem_used || 0) / selected.mem_total) * 100 : 0} />
                    {selected.gpu_count > 0 && <Row label="GPU" value={`${gpuLabel(selected)} · ${pct1(selected.gpu_util)}`} bar={selected.gpu_util} />}
                    <Row label="DISK" value={`${gib(selected.disk_total - selected.disk_free)} / ${gib(selected.disk_total)}`} bar={selected.disk_total > 0 ? ((selected.disk_total - selected.disk_free) / selected.disk_total) * 100 : 0} />

                    <div className="ag-sec-title">compute packages</div>
                    <Badges items={selected.capabilities} />
                    <Badges items={selected.privileges} tone="priv" />
                  </div>

                  <div className="ag-dcol">
                    <div className="ag-sec-title">networking</div>
                    <Row label="speed" value={`↓ ${bps(selected.net_rx_bps)} · ↑ ${bps(selected.net_tx_bps)}`} />
                    <div className="ag-ifaces">
                      {Array.isArray(selected.interfaces) && selected.interfaces.map((n) => (
                        <div key={n.name} className="ag-iface" title={n.mac}>
                          <span className={n.up ? 'ag-up' : 'ag-down'}>{text(n.name)}</span>
                          <span className="num">{text(n.ip) || '—'}</span>
                        </div>
                      ))}
                    </div>

                    <div className="ag-sec-title">models (ollama)</div>
                    {Array.isArray(selected.ollama_models) && selected.ollama_models.length > 0
                      ? <div className="ag-models">{selected.ollama_models.map((m) => <span key={m} className="ag-cap">{text(m)}</span>)}</div>
                      : <span className="ag-sub">none / offline</span>}

                    <div className="ag-sec-title">tasks</div>
                    <div className="ag-tasks">
                      <span className="num">queued {int(selected.jobs_queued)}</span>
                      <span className="num">running {int(selected.jobs_running)}</span>
                      <span className="num ag-done">done {int(selected.jobs_done)}</span>
                      <span className="num ag-fail">failed {int(selected.jobs_failed)}</span>
                    </div>

                    <div className="ag-sec-title">run compute</div>
                    <div className="ag-run">
                      <input className="ag-sym" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} spellCheck={false} />
                      <button type="button" className="btn" disabled={jobBusy} onClick={() => runJob(selected, 'forecast')}>forecast</button>
                      <button type="button" className="btn" disabled={jobBusy} onClick={() => runJob(selected, 'backtest')}>backtest</button>
                    </div>
                    {jobResult && <pre className="ag-raw">{jobResult}</pre>}
                  </div>
                </div>

                <details className="ag-rawd">
                  <summary className="ag-label">raw beacon JSON</summary>
                  <pre className="ag-raw">{JSON.stringify(selected, null, 2)}</pre>
                </details>
              </div>
            </div>
          )}
        </section>
      )}
    </ResizablePanel>
  );
});
