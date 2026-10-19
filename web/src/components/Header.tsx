import { memo, useEffect, useState } from 'react';
import { age, duration, int, isoUtc, multiple, num, text, usd } from '../format';
import type { Config, ConnectionState, StatusMsg, ToolsMsg } from '../types';
import { HeatLegend } from './HeatBorder';
import { Tooltip } from './Tooltip';

interface Props {
  connection: ConnectionState;
  status: StatusMsg | null;
  config: Config | null;
  alertCount: number;
  lastEventMs: number | null;
  serverTimeMs: number | null;
  paused: boolean;
  /** Glossary for tooltips (may be `null` — the local copy carries the UI). */
  tools: ToolsMsg | null;
  /** Number of symbols currently glowing. */
  heatCount: number;
  /** Clear every persisted size and toggle. */
  onResetLayout: () => void;
  /** Re-fetch `/api/tools`. */
  onReloadTools: () => void;
  /** True when `/api/tools` could not be used. */
  toolsFallback: boolean;
}

const PILL: Record<ConnectionState, { label: string; cls: string; title: string }> = {
  live: {
    label: 'live',
    cls: 'pill pill-live',
    title: 'EventSource is open — the stream is flowing',
  },
  connecting: {
    label: 'connecting',
    cls: 'pill pill-connecting',
    title: 'Opening /api/events…',
  },
  offline: {
    label: 'disconnected',
    cls: 'pill pill-offline',
    title: 'EventSource reported an error. The browser retries on its own; the pill clears on onopen.',
  },
};

/** Effective config from `hello`, rendered defensively (every key is optional). */
function configSummary(config: Config | null): string {
  if (config === null) return 'waiting for hello…';
  const parts: string[] = [];
  parts.push(text(config.market));
  if (config.window_sec != null) parts.push(`${int(config.window_sec)}s window`);
  if (config.min_move_pct_10s != null) parts.push(`min move ${num(config.min_move_pct_10s, 2)}%`);
  if (config.min_burst_multiple != null) parts.push(`≥${multiple(config.min_burst_multiple)} baseline`);
  if (config.min_burst_z != null) parts.push(`z ≥ ${num(config.min_burst_z, 1)}`);
  if (config.min_flow_usd != null) parts.push(`min flow ${usd(config.min_flow_usd)}`);
  if (config.cooldown_sec != null) parts.push(`cooldown ${duration(config.cooldown_sec)}`);
  if (config.max_symbols != null) parts.push(`top ${int(config.max_symbols)} symbols`);
  if (config.severity_min != null) parts.push(`severity ≥ ${text(config.severity_min)}`);
  return parts.join(' · ');
}

/** Ticking clock so "last event" ages even while the stream is silent. */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

function Meta({ label, value, note, tools }: { label: string; value: string; note: string; tools: ToolsMsg | null }) {
  return (
    <Tooltip keys={[label.replace(' ', '_')]} tools={tools} fallbackTitle={label} label={note} value={value} tone="stat">
      <div className="meta" tabIndex={0}>
        <span className="meta-label">{label}</span>
        <span className="meta-value num">{value}</span>
      </div>
    </Tooltip>
  );
}

export const Header = memo(function Header({
  connection,
  status,
  config,
  alertCount,
  lastEventMs,
  serverTimeMs,
  paused,
  tools,
  heatCount,
  onResetLayout,
  onReloadTools,
  toolsFallback,
}: Props) {
  const now = useNow(1000);
  const pill = PILL[connection];
  const lastAge = lastEventMs === null ? '—' : `${age(lastEventMs, now)} ago`;

  return (
    <header className="header">
      <div className="brand">
        <div className="brand-line">
          <h1>pumpdump</h1>
          <span className="brand-tag">live market watch</span>
        </div>
        <p className="brand-sub">{configSummary(config)}</p>
        <div className="brand-tools">
          <HeatLegend activeCount={heatCount} tools={tools} />
          <Tooltip
            keys={['heat']}
            tools={tools}
            fallbackTitle="Heat border"
            label="A light green border appears around a ticker when any price, volume or indicator reason makes it interesting. Brightness scales with heat; below 25 there is no border."
            value={`${int(heatCount)} lit`}
            tone="heat"
          >
            <span className="legend-note" tabIndex={0}>
              brighter = more interesting
            </span>
          </Tooltip>
        </div>
      </div>

      <div className="header-right">
        <div className="header-actions">
          <Tooltip
            keys={['layout']}
            tools={tools}
            fallbackTitle="Reset layout"
            label="Every panel size and the chart's indicator toggles are remembered in this browser. This clears them back to the defaults."
            tone="stat"
          >
            <button type="button" className="btn" onClick={onResetLayout}>
              reset layout
            </button>
          </Tooltip>
          <Tooltip
            keys={['tools']}
            tools={tools}
            fallbackTitle="Indicator glossary"
            label={
              toolsFallback
                ? 'GET /api/tools is unavailable, so tooltips use the built-in copy. Click to retry the endpoint.'
                : 'Tooltip copy is coming from GET /api/tools. Click to re-fetch it.'
            }
            value={toolsFallback ? 'local copy' : 'api'}
            tone="stat"
          >
            <button type="button" className="btn" onClick={onReloadTools}>
              {toolsFallback ? 'glossary: local' : 'glossary: api'}
            </button>
          </Tooltip>
        </div>
        <span className={pill.cls} title={pill.title}>
          <i className="dot" />
          {pill.label}
        </span>
        {paused && (
          <span className="pill pill-paused" title="New alerts are being counted, not inserted">
            <i className="dot" />
            feed paused
          </span>
        )}
        <div className="meta-row">
          <Meta label="uptime" value={duration(status?.uptime_sec)} note="status.uptime_sec" tools={tools} />
          <Meta
            label="symbols"
            value={
              config?.max_symbols != null
                ? `${int(status?.symbols_tracked)} / ${int(config.max_symbols)}`
                : int(status?.symbols_tracked)
            }
            note={`status.symbols_tracked · baselines ready ${int(status?.baselines_ready)}`}
            tools={tools}
          />
          <Meta
            label="alerts"
            value={int(alertCount)}
            note="status.alerts (falls back to the in-memory buffer)"
            tools={tools}
          />
          <Meta
            label="last event"
            value={lastAge}
            note={lastEventMs === null ? 'no message parsed yet' : `received ${isoUtc(lastEventMs)}`}
            tools={tools}
          />
          <Meta
            label="server clock"
            value={isoUtc(serverTimeMs).replace('T', ' ').replace('Z', ' UTC')}
            note="hello.server_time_ms"
            tools={tools}
          />
        </div>
      </div>
    </header>
  );
});
