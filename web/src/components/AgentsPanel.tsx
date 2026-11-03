import { memo, useEffect, useMemo, useState } from 'react';
import { age, int, text } from '../format';
import { isAgentActive } from '../agentStatus';
import { useSelection } from '../selection';
import type { Interval } from '../types';
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
  active_jobs?: ActiveJob[];
  jobs_queued: number;
  jobs_running: number;
  jobs_done: number;
  jobs_failed: number;
  first_seen_ms: number;
  last_seen_ms: number;
}

interface ActiveJob {
  id: string;
  type: string;
  model: string;
  device: string;
  elapsed_ms: number;
}

interface VerifyPoint {
  t: number;
  forecast: number;
  actual: number;
  error: number;
  error_pct: number;
}

interface VerifyResult {
  symbol: string;
  interval: string;
  method: string;
  agent: string;
  job: string;
  horizon: number;
  last_train: number;
  points: VerifyPoint[];
  errors: { mae: number; rmse: number; mape_pct: number; bias: number; max_error: number; direction: number };
}

interface BacktestResult {
  runner?: string;
  method?: string;
  points?: number;
  trades?: number;
  final_equity?: number;
  total_return?: number;
  sharpe?: number;
  error?: string;
}

/** A job type as a verb the user can read at a glance. */
function doing(job: ActiveJob): string {
  switch (job.type) {
    case 'install':
      return 'installing packages';
    case 'optimize':
      return 'optimising parameters';
    case 'forecast':
      return 'forecasting';
    case 'backtest':
      return 'backtesting';
    case 'train':
      return 'training';
    case 'ollama':
      return 'running a model';
    default:
      return job.type || 'working';
  }
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

export const AgentsPanel = memo(function AgentsPanel({ onOpenForecast }: { onOpenForecast?: () => void }) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [live, setLive] = useState(false);
  const selection = useSelection();
  const [now, setNow] = useState(() => Date.now());
  const [selected, setSelected] = useState<AgentInfo | null>(null);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [jobResult, setJobResult] = useState('');
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
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

  // Only agents still beaconing are listed; a dead agent is not shown at all.
  const active = useMemo(() => agents.filter((a) => isAgentActive(a.last_seen_ms, now)), [agents, now]);
  const sorted = useMemo(() => active.slice().sort((a, b) => (b.last_seen_ms ?? 0) - (a.last_seen_ms ?? 0)), [active]);

  /**
   * POST with one retry, and a message that says what actually happened.
   * A raw "TypeError: Load failed" tells the user nothing; it usually means the
   * platform is restarting or the agent dropped the connection.
   */
  async function postJson(url: string, body: unknown, tries = 2): Promise<unknown> {
    let lastErr = '';
    for (let attempt = 0; attempt < tries; attempt++) {
      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        });
        const parsed = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        if (!res.ok) {
          lastErr = String(parsed.error ?? `HTTP ${res.status}`);
          continue;
        }
        return parsed;
      } catch (e) {
        lastErr = e instanceof Error && e.name === 'TypeError'
          ? 'network error — the platform or agent stopped responding (retrying)'
          : String(e);
        await new Promise((r) => window.setTimeout(r, 400));
      }
    }
    throw new Error(lastErr || 'request failed');
  }

  async function runJob(agent: AgentInfo, type: 'forecast' | 'backtest') {
    setJobBusy(true);
    setJobResult('');
    setVerify(null);
    setBacktest(null);
    try {
      if (type === 'forecast') {
        // Grade the forecast against the bars that actually followed, so the
        // result on screen is a forecast AND its error, never a bare line.
        const out = (await postJson('/api/forecast/verify', {
          agent: agent.host,
          symbol,
          interval: '1h',
          horizon: 12,
          method: 'trend',
        })) as Record<string, unknown>;
        if (out.ok === false) throw new Error(String(out.error ?? 'forecast failed'));
        const graded = out as unknown as VerifyResult;
        setVerify(graded);
        // The forecast is only meaningful against real bars, so open the chart
        // on the exact symbol and interval that were just graded: the forecast
        // line is drawn by the chart itself.
        selection.selectInterval(graded.interval as Interval);
        selection.selectSymbol(graded.symbol);
      } else {
        const created = (await postJson('/api/compute', {
          agent: agent.host,
          type: 'backtest',
          model: 'mom',
          payload: { history_ref: { symbol, interval: '1h', limit: 200 }, window: 10 },
        })) as Record<string, unknown>;
        const id = String(created.id ?? '');
        if (!id) throw new Error(String(created.error ?? 'the agent rejected the job'));
        // Poll the agent directly for the finished job.
        let status = 'queued';
        let job: Record<string, unknown> = created;
        for (let i = 0; i < 40 && (status === 'queued' || status === 'running'); i++) {
          await new Promise((r) => window.setTimeout(r, 500));
          const res = await fetch(`${agent.host}/jobs/${id}`);
          job = (await res.json()) as Record<string, unknown>;
          status = String(job.status ?? '');
        }
        if (status === 'failed') throw new Error(String(job.error ?? 'the backtest failed'));
        if (status !== 'done') throw new Error('the backtest did not finish in time');
        setBacktest((job.result ?? {}) as BacktestResult);
      }
    } catch (e) {
      setJobResult(e instanceof Error ? e.message : String(e));
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
            <span className="sub num">{int(sorted.length)} active</span>
          </div>

          {!live ? (
            <p className="empty">agent registry unavailable — /api/agents isn't answering</p>
          ) : sorted.length === 0 ? (
            <p className="empty">no active agents — run <code>cryptalas</code> on a box; it multicasts its beacon</p>
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
                  {(a.active_jobs ?? []).length > 0 && (
                    <div className="ag-busy">
                      {(a.active_jobs ?? []).slice(0, 2).map((j) => (
                        <span className="ag-busy-item" key={j.id}>
                          <span className="ag-spin" aria-hidden="true">◐</span>
                          {doing(j)} · {Math.round(j.elapsed_ms / 1000)}s
                        </span>
                      ))}
                    </div>
                  )}
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
                    {(selected.active_jobs ?? []).length > 0 && (
                      <div className="ag-busy">
                        {(selected.active_jobs ?? []).map((j) => (
                          <span className="ag-busy-item" key={j.id}>
                            <span className="ag-spin" aria-hidden="true">◐</span>
                            {doing(j)} · {j.device} · {Math.round(j.elapsed_ms / 1000)}s
                          </span>
                        ))}
                      </div>
                    )}
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
                      <button type="button" className="btn" disabled={jobBusy} onClick={() => onOpenForecast?.()}>forecast</button>
                      <button type="button" className="btn" disabled={jobBusy} onClick={() => runJob(selected, 'backtest')}>backtest</button>
                    </div>
                    {jobResult && <p className="ag-err">{jobResult}</p>}

                    {verify && (
                      <div className="ag-verify">
                        <div className="ag-vhead">
                          <span className="num">
                            {verify.method} · {verify.horizon} bars · {verify.interval}
                          </span>
                          <span className="num ag-vmape">MAPE {verify.errors.mape_pct.toFixed(3)}%</span>
                        </div>
                        <p className="ag-vhint">
                          forecast vs actual for {verify.symbol} — the line is drawn on the candle
                          chart; open the symbol to see it against the real bars
                        </p>
                        <div className="ag-vstats">
                          <span>MAE <b className="num">{verify.errors.mae.toFixed(2)}</b></span>
                          <span>RMSE <b className="num">{verify.errors.rmse.toFixed(2)}</b></span>
                          <span>bias <b className="num">{verify.errors.bias >= 0 ? '+' : ''}{verify.errors.bias.toFixed(2)}</b></span>
                          <span>max <b className="num">{verify.errors.max_error.toFixed(2)}</b></span>
                          <span>dir <b className="num">{verify.errors.direction.toFixed(0)}%</b></span>
                        </div>
                        <div className="ag-vtable">
                          <div className="ag-vrow ag-vrow-head">
                            <span>#</span>
                            <span>forecast</span>
                            <span>actual</span>
                            <span>error</span>
                            <span>err%</span>
                          </div>
                          {(verify.points ?? []).map((pt, i) => (
                            <div className="ag-vrow" key={pt.t}>
                              <span className="num">{i + 1}</span>
                              <span className="num">{pt.forecast.toFixed(2)}</span>
                              <span className="num">{pt.actual.toFixed(2)}</span>
                              <span className={`num ${pt.error >= 0 ? 'ag-pos' : 'ag-neg'}`}>
                                {pt.error >= 0 ? '+' : ''}
                                {pt.error.toFixed(2)}
                              </span>
                              <span className="num">{pt.error_pct.toFixed(2)}%</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {backtest && (
                      <div className="ag-verify">
                        <div className="ag-vhead">
                          <span className="num">{backtest.method ?? backtest.runner ?? 'backtest'}</span>
                          {backtest.error && <span className="num ag-err">{backtest.error}</span>}
                        </div>
                        <div className="ag-vstats">
                          <span>bars <b className="num">{backtest.points ?? 0}</b></span>
                          <span>trades <b className="num">{backtest.trades ?? 0}</b></span>
                          <span>equity <b className="num">{(backtest.final_equity ?? 1).toFixed(4)}</b></span>
                          <span>return <b className="num">{(((backtest.total_return ?? 0) * 100)).toFixed(2)}%</b></span>
                          <span>sharpe <b className="num">{(backtest.sharpe ?? 0).toFixed(2)}</b></span>
                        </div>
                      </div>
                    )}
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
