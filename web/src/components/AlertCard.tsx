import { memo, useState } from 'react';
import {
  DASH,
  age,
  clock,
  flag,
  int,
  isoUtc,
  multiple,
  num,
  pct,
  px,
  share,
  text,
  toNum,
  tone,
  usd,
} from '../format';
import { useElementSize } from '../hooks/useElementSize';
import { useSelection } from '../selection';
import { detectorLabel, prettifyEnum } from '../types';
import type { AlertMsg, AlertMetrics, ToolsMsg } from '../types';
import { BatchBadge, KindBadge, SeverityBadge } from './Badges';
import { EventGlyph } from './EventGlyph';
import type { ColorCanon } from '../colors';
import { Tooltip } from './Tooltip';

interface Props {
  alert: AlertMsg;
  /** True for events that arrived live — plays the one-shot flash animation. */
  flash: boolean;
  tools: ToolsMsg | null;
  /**
   * Shared clock (seconds precision) so "snapshot 42s ago" ticks without each
   * card owning a timer. It changes once a second, and the card is memoised, so
   * a heat tick or an unrelated alert does not re-render these.
   */
  nowMs: number;
  /** Clicking a severity / detector / kind badge narrows the feed to it. */
  onFilterToggle?: (key: 'severity' | 'detector' | 'kind', value: string) => void;
  /**
   * A metric row is a navigation target, not decoration: activating one opens
   * the drill-down for this alert's symbol.
   */
  onDrill?: (symbol: string, seed?: { alerts: AlertMsg[] }) => void;
  /** The canonical palette, so the feed badge matches the chart marker. */
  canon?: ColorCanon | null;
}

/** How one metrics key is rendered, and how it reads in a tooltip. */
interface MetricFormat {
  digits?: number;
  render: (v: unknown) => string;
}

const METRIC_FORMAT: Readonly<Record<string, MetricFormat>> = {
  window_sec: { render: (v) => `${int(v)}s` },
  flow_quote: { render: (v) => usd(v) },
  burst_multiple: { render: (v) => multiple(v) },
  burst_z: { render: (v) => num(v, 1) },
  sigma_multiple: { render: (v) => multiple(v) },
  change_10s: { render: (v) => pct(v) },
  change_30s: { render: (v) => pct(v) },
  change_60s: { render: (v) => pct(v) },
  coverage: { render: (v) => share(v, 0) },
  trades_10s: { render: (v) => int(v) },
  trades_multiple: { render: (v) => multiple(v) },
  acceleration: { render: (v) => multiple(v) },
  range_position: { render: (v) => share(v, 0) },
  spread_pct: { render: (v) => (toNum(v) === null ? DASH : `${(toNum(v) as number * 100).toFixed(4)}%`) },
  book_depth_usd: { render: (v) => usd(v) },
  quote_volume_24h: { render: (v) => usd(v) },
  latency_ms: { render: (v) => `${int(v)}ms` },
  penalty: { render: (v) => num(v, 1) },
  agg_confirmed: { render: (v) => flag(v) },
  agg_buy_quote: { render: (v) => usd(v) },
  agg_sell_quote: { render: (v) => usd(v) },
  agg_buy_ratio: { render: (v) => share(v, 1) },
  agg_trades: { render: (v) => int(v) },
  agg_largest_trade_usd: { render: (v) => usd(v) },
};

/** A signed/unsigned renderer for keys the backend may add later. */
function genericRender(v: unknown): string {
  if (typeof v === 'boolean') return flag(v);
  const n = toNum(v);
  if (n !== null) {
    if (Math.abs(n) < 1 && n !== 0) return share(n, 2);
    return num(n, 2);
  }
  return text(v);
}

export function MetricRow({
  name,
  value,
  tools,
  onActivate,
}: {
  name: string;
  value: unknown;
  tools: ToolsMsg | null;
  /** Activating the row drills into the symbol this metric belongs to. */
  onActivate?: () => void;
}) {
  const format = METRIC_FORMAT[name];
  const rendered = format !== undefined ? format.render(value) : genericRender(value);
  const interactive = onActivate !== undefined;
  return (
    <div
      className={`metric-row${interactive ? ' metric-row-clickable' : ''}`}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={onActivate}
      onKeyDown={
        interactive
          ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onActivate();
              }
            }
          : undefined
      }
      title={interactive ? `open the drill-down for ${name}` : undefined}
    >
      <dt className="metric-key">
        <Tooltip keys={[name]} tools={tools} fallbackTitle={name} tone="stat" value={rendered}>
          <span tabIndex={0}>{name}</span>
        </Tooltip>
      </dt>
      <dd className={`metric-value num${typeof value === 'number' ? ` ${tone(value)}` : ''}`}>{rendered}</dd>
    </div>
  );
}

function Figure({
  label,
  value,
  cls,
  tools,
  toolKeys,
}: {
  label: string;
  value: string;
  cls?: string;
  tools: ToolsMsg | null;
  toolKeys: string[];
}) {
  return (
    <Tooltip keys={toolKeys} tools={tools} fallbackTitle={label} value={value} tone="stat">
      <div className="figure" tabIndex={0}>
        <span className="figure-label">{label}</span>
        <span className={`figure-value num ${cls ?? ''}`}>{value}</span>
      </div>
    </Tooltip>
  );
}

/**
 * One alert card.
 *
 * - the header row is a button that expands the card to a definition list of
 *   **every** `metrics` key (not only the handful shown as figures)
 * - the symbol is its own button and selects it for the chart
 * - every metric label carries a glossary tooltip
 * - narrow containers stack the figures instead of clipping them
 */
export const AlertCard = memo(function AlertCard({
  alert,
  flash,
  tools,
  nowMs,
  onFilterToggle,
  onDrill,
  canon = null,
}: Props) {
  const [open, setOpen] = useState(false);
  const { ref, size } = useElementSize<HTMLElement>();
  const selection = useSelection();

  const m = alert.metrics ?? null;
  const components = alert.components ?? null;
  const reasons = Array.isArray(alert.reasons) ? alert.reasons : [];
  const symbol = text(alert.symbol);
  const selected = selection.isSelected(alert.symbol);
  const narrow = size.width > 0 && size.width < 460;

  const scoreTitle = [
    `score ${int(alert.score)}`,
    `flow ${num(components?.flow, 1)}`,
    `speed ${num(components?.speed, 1)}`,
    `aggression ${num(components?.aggression, 1)}`,
    `quality ${num(components?.quality, 1)}`,
  ].join(' + ');

  return (
    <article
      ref={ref}
      className={`alert-card${flash ? ' flash' : ''}${selected ? ' selected' : ''}${open ? ' open' : ''}${narrow ? ' narrow' : ''}`}
      data-event-id={alert.event_id ?? undefined}
    >
      <div className="alert-top">
        <button
          type="button"
          className="alert-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          title={open ? 'collapse metrics' : 'expand every metrics key'}
        >
          <span className="caret" aria-hidden="true">
            {open ? '▾' : '▸'}
          </span>
          <span className="alert-time num" title={`event ts_ms ${isoUtc(alert.ts_ms)}`}>
            {clock(alert.ts_ms)}
          </span>
          <span className="alert-symbol">{symbol}</span>
        </button>

        <button
          type="button"
          className={`sym-btn${selected ? ' selected' : ''}`}
          aria-pressed={selected}
          onClick={() => {
            // Selecting charts it; drilling opens the trade breakdown for it.
            selection.selectSymbol(String(alert.symbol ?? ''));
            onDrill?.(String(alert.symbol ?? ''));
          }}
          title={`open the trade drill-down for ${symbol}`}
        >
          {selected ? '◉ drilling' : '◎ drill'}
        </button>

        <Tooltip
          keys={['kind']}
          tools={tools}
          fallbackTitle="Alert kind"
          label="PUMP / DUMP / REVERSAL_* / BREAKOUT_* — the direction of the detected signal. Click to filter the feed by this kind."
          value={prettifyEnum(alert.kind, 'unclassified')}
          tone="badge"
        >
          <button
            type="button"
            className="badge-btn"
            onClick={() => onFilterToggle?.('kind', String(alert.kind ?? ''))}
            title={`filter the feed to ${prettifyEnum(alert.kind)}`}
          >
            {/*
              The same letter, colour and arrow the chart draws for this event:
              one visual identity, resolved from the server's own values.
            */}
            <EventGlyph
              kind={alert.kind}
              severity={alert.severity}
              color={alert.color}
              letter={alert.letter}
              direction={alert.direction}
              canon={canon}
              size="sm"
            />
            <KindBadge kind={alert.kind} color={alert.color} canon={canon} />
          </button>
        </Tooltip>
        <Tooltip
          keys={['severity']}
          tools={tools}
          fallbackTitle="Severity"
          label="WATCH is interesting, ALERT is actionable, CRITICAL is exceptional. Click to filter the feed by this severity."
          value={text(alert.severity)}
          tone="badge"
        >
          <button
            type="button"
            className="badge-btn"
            onClick={() => onFilterToggle?.('severity', String(alert.severity ?? ''))}
            title={`filter the feed to ${text(alert.severity)}`}
          >
            <SeverityBadge severity={alert.severity} canon={canon} />
          </button>
        </Tooltip>
        <Tooltip
          keys={['score']}
          tools={tools}
          fallbackTitle="Score"
          label={scoreTitle}
          value={int(alert.score)}
          tone="badge"
        >
          <span className="alert-score num" tabIndex={0}>
            {int(alert.score)}
          </span>
        </Tooltip>
      </div>

      {/* Which model produced this: the feed mixes flow, screen and Turtle. */}
      <div className="alert-model-line">
        <Tooltip
          keys={['detector']}
          tools={tools}
          fallbackTitle="Detector"
          label="Which model produced this alert. Components and metrics differ per detector."
          value={typeof alert.detector === 'string' ? detectorLabel(alert.detector) : null}
          tone="badge"
        >
          <button
            type="button"
            className="alert-detector"
            onClick={() => onFilterToggle?.('detector', String(alert.detector ?? ''))}
            title={`filter the feed to ${detectorLabel(alert.detector)}`}
          >
            {typeof alert.detector === 'string' && alert.detector !== ''
              ? detectorLabel(alert.detector)
              : 'detector not reported'}
          </button>
        </Tooltip>
        {alert.batch === true && (
          <Tooltip
            keys={['batch', 'snapshot_ms']}
            tools={tools}
            fallbackTitle="Batch snapshot"
            label="A batch detector re-evaluates on a timer, so this signal may already be older than the alert."
            value={alert.snapshot_ms != null ? `${age(alert.snapshot_ms, nowMs)} old` : 'age unknown'}
            tone="badge"
          >
            <span tabIndex={0}>
              <BatchBadge snapshotMs={typeof alert.snapshot_ms === 'number' ? alert.snapshot_ms : null} nowMs={nowMs} />
            </span>
          </Tooltip>
        )}
      </div>

      {typeof alert.summary === 'string' && alert.summary.trim() !== '' && (
        <Tooltip
          keys={['summary']}
          tools={tools}
          fallbackTitle="Summary"
          label="One-line description of the signal, as reported by the detector."
          value={alert.summary}
          tone="stat"
        >
          <p className="alert-summary">{alert.summary}</p>
        </Tooltip>
      )}

      <div className="alert-figures">
        <Figure
          label="price"
          value={px(alert.price)}
          tools={tools}
          toolKeys={['price']}
        />
        <Figure
          label="10s move"
          value={pct(m?.change_10s)}
          cls={tone(m?.change_10s)}
          tools={tools}
          toolKeys={['change_10s']}
        />
        <Figure label="10s flow" value={usd(m?.flow_quote)} tools={tools} toolKeys={['flow_quote']} />
        <Figure
          label="vs baseline"
          value={multiple(m?.burst_multiple)}
          tools={tools}
          toolKeys={['burst_multiple']}
        />
        <Figure label="z-score" value={num(m?.burst_z, 1)} tools={tools} toolKeys={['burst_z']} />
      </div>

      <div className="alert-meta-line">
        {(
          [
            { label: 'window', value: `${int(m?.window_sec)}s`, keys: ['window_sec'], signed: null },
            { label: 'trades', value: int(m?.trades_10s), keys: ['trades_10s'], signed: null },
            { label: 'coverage', value: share(m?.coverage, 0), keys: ['coverage'], signed: null },
            { label: 'aggressor', value: flag(m?.agg_confirmed), keys: ['agg_confirmed'], signed: null },
            { label: 'buy ratio', value: share(m?.agg_buy_ratio, 0), keys: ['agg_buy_ratio'], signed: null },
            { label: '30s', value: pct(m?.change_30s), keys: ['change_30s'], signed: m?.change_30s },
            { label: '60s', value: pct(m?.change_60s), keys: ['change_60s'], signed: m?.change_60s },
            { label: 'book', value: usd(m?.book_depth_usd), keys: ['book_depth_usd'], signed: null },
            { label: '24h', value: usd(m?.quote_volume_24h), keys: ['quote_volume_24h'], signed: null },
          ] as const
        ).map((item) => (
          <Tooltip
            key={item.label}
            keys={[...item.keys]}
            tools={tools}
            fallbackTitle={item.label}
            value={item.value}
            tone="stat"
          >
            <span className="meta-figure" tabIndex={0}>
              <span className="meta-figure-label">{item.label}</span>
              <span className={`meta-figure-value num ${tone(item.signed)}`}>{item.value}</span>
            </span>
          </Tooltip>
        ))}
      </div>

      <AlertDetails
        alert={alert}
        open={open}
        onCollapse={() => setOpen(false)}
        tools={tools}
        onDrill={onDrill}
      />

      {reasons.length > 0 && (
        <ul className="reasons">
          {reasons.map((reason, i) => (
            <li key={`${i}-${reason}`} title={typeof reason === 'string' ? reason : String(reason)}>
              {typeof reason === 'string' ? reason : String(reason)}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
});

/**
 * The expandable detail block: **every** key present in `metrics`, plus the
 * envelope fields, rendered as a definition list.
 *
 * Nothing here knows the metric set. The flow detector's
 * `burst_multiple`/`flow_quote`/`taker_buy_ratio`, the screen's
 * `ret_5m`/`vol_ratio`/`close_position` and the Turtle detector's
 * `atr`/`adx14`/`extension_up_atr` all render the same way, and a key nobody has
 * seen before renders too, labelled from `/api/tools` or the local copy map.
 */
export function AlertDetails({
  alert,
  open,
  onCollapse,
  tools,
  onDrill,
}: {
  alert: AlertMsg;
  open: boolean;
  onCollapse?: () => void;
  tools: ToolsMsg | null;
  /** Metric rows become drill-down links when this is provided. */
  onDrill?: (symbol: string, seed?: { alerts: AlertMsg[] }) => void;
}) {
  const symbol = typeof alert.symbol === 'string' ? alert.symbol : '';
  const drill = symbol !== '' && onDrill !== undefined ? () => onDrill(symbol) : undefined;
  const metrics = (alert.metrics ?? null) as (AlertMetrics & Record<string, unknown>) | null;
  const entries =
    metrics === null || typeof metrics !== 'object'
      ? []
      : Object.entries(metrics).filter(([key]) => key !== 'symbol' && key !== 'kind');

  const components = alert.components ?? null;
  const componentEntries =
    components !== null && typeof components === 'object'
      ? Object.entries(components).filter(([, value]) => value != null)
      : [];

  if (!open) return null;

  return (
    <>
      <dl className="metrics-dl">
        <div className="metrics-head">
          <span>every metrics key ({int(entries.length)})</span>
          {onCollapse !== undefined && (
            <button type="button" className="link-btn" onClick={onCollapse}>
              collapse
            </button>
          )}
        </div>
        {(
          [
            { name: 'detector', value: alert.detector },
            { name: 'batch', value: alert.batch },
            { name: 'snapshot_ms', value: alert.snapshot_ms },
            { name: 'dispatched_ms', value: alert.ts_ms },
            { name: 'reasons', value: alert.reasons },
          ] as const
        ).map((item) => (
          <MetricRow key={item.name} name={item.name} value={item.value} tools={tools} onActivate={drill} />
        ))}
        {entries.length === 0 ? (
          <div className="metric-row">
            <dt className="metric-key">metrics</dt>
            <dd className="metric-value">none reported</dd>
          </div>
        ) : (
          entries.map(([name, value]) => (
            <MetricRow key={name} name={name} value={value} tools={tools} onActivate={drill} />
          ))
        )}
      </dl>

      {componentEntries.length > 0 && (
        <dl className="metrics-dl components-dl">
          <div className="metrics-head">
            <span>score components ({int(componentEntries.length)})</span>
          </div>
          {componentEntries.map(([name, value]) => (
            <MetricRow key={name} name={name} value={value} tools={tools} onActivate={drill} />
          ))}
        </dl>
      )}
    </>
  );
}
