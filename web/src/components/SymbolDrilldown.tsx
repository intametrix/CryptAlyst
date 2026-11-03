import { memo, useMemo, type ReactNode } from 'react';
import { useTools } from '../glossaryContext';
import { clock, flag, int, isoUtc, joinList, multiple, num, pct, px, share, text, toNum, tone, usd } from '../format';
import { useSymbolDetail } from '../hooks/useKlines';
import { detectorLabel, prettifyEnum } from '../types';
import type { AlertMsg, SymbolDetailMsg } from '../types';
import { KindBadge, SeverityBadge } from './Badges';
import { HeatBorder } from './HeatBorder';
import { Tooltip } from './Tooltip';

/** One labelled field in the trade card. Every one is tooltipped. */
function Field({
  label,
  value,
  toolKeys,
  big = false,
}: {
  label: string;
  value: string;
  toolKeys: string[];
  big?: boolean;
}) {
  const tools = useTools();
  return (
    <Tooltip keys={toolKeys} tools={tools} fallbackTitle={label} value={value} tone="stat">
      <div className={`dd-field${big ? ' dd-field-big' : ''}`} tabIndex={0}>
        <span className="dd-field-label">{label}</span>
        <span className="dd-field-value num">{value}</span>
      </div>
    </Tooltip>
  );
}

/** A section wrapper that never renders an empty shell. */
function Section({
  title,
  note,
  children,
  tone: sectionTone = 'plain',
}: {
  title: string;
  note?: string;
  children: ReactNode;
  tone?: 'plain' | 'trade' | 'blocked';
}) {
  return (
    <section className={`dd-section dd-section-${sectionTone}`}>
      <h3 className="dd-section-title">
        {title}
        {note !== undefined && <span className="dd-section-note">{note}</span>}
      </h3>
      {children}
    </section>
  );
}

/**
 * The trade card — the destination of every drill-down.
 *
 * When a trade exists it is the visually dominant element: entry, stop, size,
 * notional, risk, units, the pyramid ladder and the exit level. When it does not,
 * `why_no_trade` is shown just as prominently, because the reason a setup was
 * refused *is* the answer. Either way the panel is never empty.
 */
function TradeCard({
  detail,
  onBack,
}: {
  detail: SymbolDetailMsg;
  onBack?: () => void;
}) {
  const tools = useTools();
  const trade = detail.trade ?? null;
  const why = typeof detail.why_no_trade === 'string' ? detail.why_no_trade.trim() : '';

  if (trade === null) {
    return (
      <Section title="trade" note={why === '' ? 'no plan' : 'refused'} tone="blocked">
        <div className="dd-no-trade">
          <span className="dd-no-trade-label">why no trade</span>
          <p className="dd-no-trade-reason">
            {why !== '' ? why : 'the survey has not evaluated this symbol yet — no setup and no rejection reason'}
          </p>
          <div className="dd-no-trade-actions">
            <Tooltip keys={['why_no_trade']} tools={tools} fallbackTitle="Why no trade">
              <span className="dd-hint" tabIndex={0}>
                this is the gate that refused the setup
              </span>
            </Tooltip>
            {onBack !== undefined && (
              <button type="button" className="btn" onClick={onBack}>
                back to alerts
              </button>
            )}
          </div>
        </div>
      </Section>
    );
  }

  const pyramid = Array.isArray(trade.pyramid_levels) ? trade.pyramid_levels : [];
  return (
    <Section title="trade" note={`entry ${px(trade.entry)}`} tone="trade">
      <div className="dd-trade-grid">
        <Field label="entry" value={px(trade.entry)} toolKeys={['entry']} big />
        <Field label="stop" value={px(trade.stop)} toolKeys={['stop']} big />
        <Field label="exit level" value={px(trade.exit_level)} toolKeys={['exit_level']} big />
        <Field label="unit size" value={num(trade.unit_size, 4)} toolKeys={['unit_size']} />
        <Field label="unit notional" value={usd(trade.unit_notional)} toolKeys={['unit_notional']} />
        <Field label="total notional" value={usd(trade.total_notional)} toolKeys={['total_notional']} />
        <Field label="total risk" value={usd(trade.total_risk)} toolKeys={['total_risk']} big />
        <Field label="units" value={int(trade.units)} toolKeys={['units']} />
        <Field label="capped" value={flag(trade.notional_capped)} toolKeys={['notional_capped']} />
      </div>
      <div className="dd-pyramid">
        <Tooltip
          keys={['pyramid_levels']}
          tools={tools}
          fallbackTitle="Pyramid ladder"
          label="The prices at which the plan adds another unit — the Turtles add on strength at half-N intervals."
          value={joinList(pyramid)}
          tone="stat"
        >
          <span className="dd-pyramid-label" tabIndex={0}>
            pyramid ladder
          </span>
        </Tooltip>
        <ol className="dd-pyramid-steps">
          <li className="dd-pyramid-entry">{px(trade.entry)} entry</li>
          {pyramid.map((level, i) => (
            <li key={`${i}-${String(level)}`}>
              {px(level)}
              <span className="dd-pyramid-add">+ unit {i + 2}</span>
            </li>
          ))}
          <li className="dd-pyramid-exit">{px(trade.exit_level)} exit</li>
        </ol>
      </div>
    </Section>
  );
}

export interface SymbolDrilldownProps {
  symbol: string;
  /** Breadcrumb trail, most general first (e.g. ['alerts', 'ENAUSDT']). */
  trail: readonly string[];
  /** Called when the user clicks a breadcrumb step. */
  onTrailStep?: (step: string) => void;
  /** Called when the panel wants another symbol opened (metric-row drill). */
  onInspect?: (symbol: string) => void;
  /**
   * A payload to render immediately, before the fetch resolves.
   *
   * Used when the caller already holds the data (for example a row the user
   * clicked, whose screen/setup row is in memory), and by the render smoke test
   * so the trade card can be asserted without a server. The fetch still runs and
   * replaces it.
   */
  initialDetail?: SymbolDetailMsg | null;
}

/**
 * The shared drill-down panel: one symbol, every angle on it, and the trade (or
 * the reason there is not one) as the destination rather than a footnote.
 */
export const SymbolDrilldown = memo(function SymbolDrilldown({
  symbol,
  trail,
  onTrailStep,
  onInspect,
  initialDetail = null,
}: SymbolDrilldownProps) {
  const tools = useTools();
  const live = useSymbolDetail(symbol);
  const detail = live.detail ?? (initialDetail !== null && initialDetail.symbol === symbol ? initialDetail : null);
  const { loading, error, atMs, refresh } = live;

  const metrics = useMemo(() => {
    const raw = detail?.metrics ?? null;
    if (raw === null || typeof raw !== 'object') return [] as Array<[string, unknown]>;
    return Object.entries(raw).filter(([, value]) => value !== null && value !== undefined);
  }, [detail]);

  const alerts = useMemo<AlertMsg[]>(
    () => (Array.isArray(detail?.alerts) ? detail.alerts.slice(0, 8) : []),
    [detail],
  );

  const context = detail?.context ?? null;
  const flowRow = detail?.flow ?? null;
  const setup = detail?.setup ?? null;
  const screenRow = detail?.screen ?? null;
  const heat = detail?.heat ?? null;

  return (
    <div className="dd">
      <nav className="dd-crumbs" aria-label="drill-down path">
        {trail.map((step, i) => (
          <span key={`${i}-${step}`} className="dd-crumb-item">
            {i > 0 && <span className="dd-crumb-sep" aria-hidden="true">›</span>}
            {i === trail.length - 1 ? (
              <span className="dd-crumb dd-crumb-current" aria-current="page">
                {step}
              </span>
            ) : (
              <button
                type="button"
                className="dd-crumb"
                onClick={() => onTrailStep?.(step)}
                title={`back to ${step}`}
              >
                {step}
              </button>
            )}
          </span>
        ))}
        <span className="dd-crumb-spacer" />
        <span className="dd-updated num" title={atMs === null ? 'never refreshed' : `updated ${isoUtc(atMs)}`}>
          {atMs === null ? (loading ? 'loading…' : '—') : `updated ${clock(atMs)}`}
        </span>
        <button type="button" className="link-btn" onClick={refresh}>
          refresh
        </button>
      </nav>

      {error !== null && <p className="dd-error">{error}</p>}

      {/* 1. The destination: the trade, or the reason there isn't one. */}
      {detail === null ? (
        <Section title="trade" note={loading ? 'loading' : 'unavailable'}>
          <p className="empty">{loading ? `loading ${symbol}…` : `no data for ${symbol}`}</p>
        </Section>
      ) : (
        <TradeCard detail={detail} />
      )}

      {/* 2. Heat */}
      <Section
        title="heat"
        note={heat?.heat != null ? `${num(heat.heat, 1)} / 100` : 'not rated'}
      >
        {heat === null ? (
          <p className="dd-muted">no heat score for this symbol right now</p>
        ) : (
          <>
            <div className="dd-heat-row">
              <HeatBorder symbol={symbol} tools={tools}>
                <span className={`dd-heat-value num${(toNum(heat.heat) ?? 0) >= 25 ? ' hot' : ''}`}>
                  {num(heat.heat, 1)}
                </span>
              </HeatBorder>
              <span className="dd-muted">{(heat.heat ?? 0) < 25 ? 'below the drawable threshold' : 'glowing in every list'}</span>
            </div>
            {Array.isArray(heat.reasons) && heat.reasons.length > 0 && (
              <ul className="dd-reasons">
                {heat.reasons.map((reason, i) => (
                  <li key={`${i}-${reason}`}>{reason}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </Section>

      {/* 3. Live flow + flattened metrics */}
      <Section title="live flow" note={flowRow?.updated_ms != null ? `updated ${clock(flowRow.updated_ms)}` : 'live'}>
        {flowRow === null && metrics.length === 0 ? (
          <p className="dd-muted">no live flow row for this symbol (it is outside the tracked window)</p>
        ) : (
          <>
            {flowRow !== null && (
              <div className="dd-grid">
                <Field label="last price" value={px(flowRow.last_price)} toolKeys={['price']} />
                <Field label="10s flow" value={usd(flowRow.flow_quote)} toolKeys={['flow_quote']} />
                <Field label="vs baseline" value={multiple(flowRow.burst_multiple)} toolKeys={['burst_multiple']} />
                <Field label="z-score" value={num(flowRow.burst_z, 1)} toolKeys={['burst_z']} />
                <Field label="10s move" value={pct(flowRow.change_10s)} toolKeys={['change_10s']} />
                <Field label="taker buy" value={share(flowRow.taker_buy_ratio, 1)} toolKeys={['taker_buy_ratio']} />
              </div>
            )}
            {metrics.length > 0 && (
              <table className="dd-metrics">
                <tbody>
                  {metrics.map(([key, value]) => {
                    const n = toNum(value);
                    const rendered =
                      typeof value === 'boolean'
                        ? flag(value)
                        : n !== null
                          ? Math.abs(n) < 1 && n !== 0
                            ? share(n, 3)
                            : num(n, 2)
                          : text(value);
                    return (
                      <tr key={key} onClick={() => onInspect?.(symbol)} className="dd-metric-row">
                        <th scope="row">
                          <Tooltip keys={[key]} tools={tools} fallbackTitle={key} value={rendered} tone="stat">
                            <span tabIndex={0}>{key}</span>
                          </Tooltip>
                        </th>
                        <td className={`num ${tone(n)}`}>{rendered}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </>
        )}
      </Section>

      {/* 4. Turtle setup */}
      <Section
        title="turtle setup"
        note={setup === null ? 'none' : setup.accepted === true ? 'accepted' : setup.accepted === false ? 'rejected' : 'unscreened'}
      >
        {setup === null ? (
          <p className="dd-muted">this symbol has no setup in the last survey pass</p>
        ) : (
          <>
            <div className="dd-grid">
              <Field label="direction" value={setup.direction === 1 ? 'long' : setup.direction === -1 ? 'short' : 'undirected'} toolKeys={['direction']} />
              <Field label="score" value={int(setup.score)} toolKeys={['score']} />
              <Field label="ATR" value={num(setup.atr, 4)} toolKeys={['atr']} />
              <Field label="ATR %" value={share(setup.atr_pct, 2)} toolKeys={['atr_pct']} />
              <Field label="ADX" value={num(setup.adx, 1)} toolKeys={['adx14', 'adx']} />
              <Field label="RSI" value={num(setup.rsi, 1)} toolKeys={['rsi14', 'rsi']} />
              <Field label="extension" value={num(setup.extension_up_atr, 2)} toolKeys={['extension_up_atr']} />
              <Field label="group" value={text(setup.group)} toolKeys={['group']} />
            </div>
            {setup.accepted === false && (
              <p className="dd-reject">
                <span className="dd-reject-label">rejected</span> {text(setup.reject_reason)}
              </p>
            )}
          </>
        )}
      </Section>

      {/* 5. Screen row */}
      <Section title="screen" note={screenRow === null ? 'not in the last pass' : `score ${int(screenRow.score)}`}>
        {screenRow === null ? (
          <p className="dd-muted">this symbol did not clear the market-wide screen</p>
        ) : (
          <div className="dd-grid">
            <Field label="kind" value={prettifyEnum(screenRow.kind)} toolKeys={['kind']} />
            <Field label="5m" value={pct(screenRow.ret_5m)} toolKeys={['ret_5m']} />
            <Field label="15m" value={pct(screenRow.ret_15m)} toolKeys={['ret_15m']} />
            <Field label="1h" value={pct(screenRow.ret_60m)} toolKeys={['ret_60m']} />
            <Field label="vol ratio" value={multiple(screenRow.vol_ratio)} toolKeys={['vol_ratio']} />
            <Field label="close pos" value={share(screenRow.close_position, 0)} toolKeys={['close_position']} />
            <Field label="5m vol" value={usd(screenRow.recent_quote_volume)} toolKeys={['recent_quote_volume']} />
            <Field label="24h vol" value={usd(screenRow.quote_volume_24h)} toolKeys={['quote_volume_24h']} />
          </div>
        )}
      </Section>

      {/* 6. Alert history */}
      <Section title="alert history" note={`${int(alerts.length)} recent`}>
        {alerts.length === 0 ? (
          <p className="dd-muted">no alerts for this symbol in the buffer</p>
        ) : (
          <ul className="dd-alerts">
            {alerts.map((alert, i) => (
              <li key={alert.event_id ?? `${i}`} className="dd-alert">
                <span className="dd-alert-time num">{clock(alert.ts_ms)}</span>
                <KindBadge kind={alert.kind} />
                <SeverityBadge severity={alert.severity} />
                <span className="dd-alert-score num">{int(alert.score)}</span>
                <span className="dd-alert-det">{detectorLabel(alert.detector)}</span>
                <span className="dd-alert-summary">{text(alert.summary ?? joinList(alert.reasons, ' · '))}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {context !== null && (
        <Section title="context" note="24h">
          <div className="dd-grid">
            <Field label="last price" value={px(context.last_price)} toolKeys={['price']} />
            <Field label="24h change" value={pct(context.change_24h_pct)} toolKeys={['ret_24h']} />
            <Field label="24h high" value={px(context.high_price)} toolKeys={['price']} />
            <Field label="24h low" value={px(context.low_price)} toolKeys={['price']} />
            <Field label="24h volume" value={usd(context.quote_volume_24h)} toolKeys={['quote_volume_24h']} />
          </div>
        </Section>
      )}
    </div>
  );
});
