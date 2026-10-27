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
  first_seen_ms: number;
  last_seen_ms: number;
}

const DEVICE_LABEL: Record<string, string> = {
  auto: 'auto',
  cpu: 'CPU',
  gpu: 'GPU',
  cuda: 'CUDA',
  metal: 'Metal',
};

function gib(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return '—';
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function gpuLabel(a: AgentInfo): string {
  if (!a.gpu_name && !a.gpu_count) return '—';
  const parts: string[] = [];
  if (a.gpu_count > 0) parts.push(`${int(a.gpu_count)}×`);
  if (a.gpu_name) parts.push(text(a.gpu_name));
  if (a.gpu_vram) parts.push(text(a.gpu_vram));
  return parts.join(' ');
}

/**
 * Agents — the enrolled CryptALAS fleet. Polls GET /api/agents and shows each
 * agent's name, the IP the platform sees, device, and the resources it
 * reported (CPU count, memory, GPUs). Agents self-enroll via
 * `cryptalas -register <platform> -name <name>`.
 */
export const AgentsPanel = memo(function AgentsPanel() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [live, setLive] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let dead = false;
    const tick = async () => {
      try {
        const res = await fetch('/api/agents', { headers: { accept: 'application/json' } });
        if (!res.ok) {
          if (!dead) setLive(false);
          return;
        }
        const body = (await res.json()) as { agents?: AgentInfo[] };
        if (!dead) {
          setAgents(Array.isArray(body.agents) ? body.agents : []);
          setLive(true);
        }
      } catch {
        if (!dead) setLive(false);
      }
    };
    void tick();
    const poll = window.setInterval(() => void tick(), 5000);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      dead = true;
      window.clearInterval(poll);
      window.clearInterval(clock);
    };
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
            <p className="empty">
              no agents enrolled — run <code>cryptalas -register http://&lt;platform&gt;:8787 -name &lt;name&gt;</code>
            </p>
          ) : (
            <table className="bus-grid agents-grid">
              <thead>
                <tr>
                  <th scope="col">name</th>
                  <th scope="col">ip</th>
                  <th scope="col">device</th>
                  <th scope="col" className="num-col">cpu</th>
                  <th scope="col" className="num-col">mem</th>
                  <th scope="col">gpu</th>
                  <th scope="col" className="num-col">last seen</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((a) => (
                  <tr key={a.id || a.host}>
                    <td className="bus-topic" title={a.host}>{text(a.name) || text(a.host)}</td>
                    <td>{text(a.ip) || '—'}</td>
                    <td>
                      <span className={`pill${a.gpu_present ? ' pill-live' : ''}`}>
                        {DEVICE_LABEL[a.device] ?? text(a.device)}
                      </span>
                    </td>
                    <td className="num">{int(a.num_cpu)}</td>
                    <td className="num">{gib(a.mem_total)}</td>
                    <td className="bus-topic" title={gpuLabel(a)}>{gpuLabel(a)}</td>
                    <td className="num">{age(a.last_seen_ms, now)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </ResizablePanel>
  );
});
