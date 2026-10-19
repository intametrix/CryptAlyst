import { memo, useState } from 'react';
import { clock, flag, int, isoUtc, joinList, num, px, share, text, tone, usd } from '../format';
import { useSelection } from '../selection';
import type { Setup, SetupPlan, SurveyMsg, ToolsMsg } from '../types';
import { HeatBorder } from './HeatBorder';
import { ResizablePanel } from './Panel';
import { Tooltip } from './Tooltip';

interface Props {
  survey: SurveyMsg | null;
  onRefresh: () => void;
  tools: ToolsMsg | null;
  /** Opens the trade drill-down for a symbol, seeded with this setup. */
  onDrill?: (symbol: string, seed?: { setup: Setup; trade: SetupPlan | null }) => void;
}

function BudgetCell({
  label,
  value,
  tools,
  toolKeys,
}: {
  label: string;
  value: string;
  tools: ToolsMsg | null;
  toolKeys: string[];
}) {
  return (
    <Tooltip keys={toolKeys} tools={tools} fallbackTitle={label} value={value} tone="stat">
      <div className="budget-cell" tabIndex={0}>
        <span className="budget-label">{label}</span>
        <span className="budget-value num">{value}</span>
      </div>
    </Tooltip>
  );
}

/** A sortable-looking, tooltip-bearing survey column header. */
function Head({
  label,
  toolKeys,
  tools,
  numeric = false,
}: {
  label: string;
  toolKeys: string[];
  tools: ToolsMsg | null;
  numeric?: boolean;
}) {
  return (
    <th scope="col" className={numeric ? 'num-col' : ''}>
      <Tooltip keys={toolKeys} tools={tools} fallbackTitle={label} tone="header">
        <span className="col-head" tabIndex={0}>
          {label}
        </span>
      </Tooltip>
    </th>
  );
}

/** Collapsible turtle-indicator survey: budget summary, setups with plans, exits. */
export const SurveyPanel = memo(function SurveyPanel({ survey, onRefresh, tools, onDrill }: Props) {
  const [open, setOpen] = useState(true);
  const selection = useSelection();

  const budget = survey?.budget ?? null;
  const setups = Array.isArray(survey?.setups) ? survey.setups : [];
  const exits = Array.isArray(survey?.exit_signals) ? survey.exit_signals : [];

  return (
    <ResizablePanel id="survey.panel" axis="y" minHeight={200} maxHeight={1400}>
      {() => (
    <section className="panel survey">
      <div className="panel-head">
        <button
          type="button"
          className="disclosure"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title={open ? 'collapse the survey' : 'expand the survey'}
        >
          <span className="caret">{open ? '▾' : '▸'}</span>
          <h2>turtle survey</h2>
        </button>
        <span className="sub num" title={`survey.ts_ms ${isoUtc(survey?.ts_ms)}`}>
          equity {usd(survey?.equity)} · {int(survey?.symbols_with_data)}/{int(survey?.symbols_scanned)} symbols ·{' '}
          {joinList(survey?.timeframes, '/')} · {clock(survey?.ts_ms)}
        </span>
        <div className="actions">
          <button
            type="button"
            className="btn"
            onClick={onRefresh}
            title="POST /api/survey/refresh — the new survey arrives over SSE (202)"
          >
            refresh
          </button>
        </div>
      </div>

      {!open ? null : survey === null ? (
        <p className="empty">no survey yet — it runs every 5 minutes (or press refresh)</p>
      ) : (
        <div className="survey-body">
          <div className="budget">
            <BudgetCell label="total units" value={int(budget?.total_units)} tools={tools} toolKeys={['units']} />
            <BudgetCell label="long units" value={int(budget?.long_units)} tools={tools} toolKeys={['units']} />
            <BudgetCell label="short units" value={int(budget?.short_units)} tools={tools} toolKeys={['units']} />
            <BudgetCell
              label="notional"
              value={usd(budget?.total_notional)}
              tools={tools}
              toolKeys={['total_notional']}
            />
            <BudgetCell label="risk" value={usd(budget?.total_risk)} tools={tools} toolKeys={['total_risk']} />
            <BudgetCell label="positions" value={int(budget?.positions)} tools={tools} toolKeys={['positions']} />
            <BudgetCell label="rejected" value={int(budget?.rejected)} tools={tools} toolKeys={['rejected']} />
          </div>

          {setups.length === 0 ? (
            <p className="empty">no setups in this survey</p>
          ) : (
            <div className="table-wrap">
              <table className="survey-table">
                <thead>
                  <tr>
                    <Head label="symbol" toolKeys={['symbol']} tools={tools} />
                    <Head label="dir" toolKeys={['kind']} tools={tools} />
                    <Head label="score" toolKeys={['score']} tools={tools} numeric />
                    <Head label="close" toolKeys={['closing_price', 'price']} tools={tools} numeric />
                    <Head label="atr %" toolKeys={['atr_pct']} tools={tools} numeric />
                    <Head label="adx" toolKeys={['adx14', 'adx']} tools={tools} numeric />
                    <Head label="rsi" toolKeys={['rsi14', 'rsi']} tools={tools} numeric />
                    <Head label="ext atr" toolKeys={['extension_up_atr']} tools={tools} numeric />
                    <Head label="group" toolKeys={['group']} tools={tools} />
                    <Head label="status" toolKeys={['accepted']} tools={tools} />
                    <Head label="entry" toolKeys={['entry']} tools={tools} numeric />
                    <Head label="stop" toolKeys={['stop']} tools={tools} numeric />
                    <Head label="units" toolKeys={['units']} tools={tools} numeric />
                    <Head label="unit $" toolKeys={['unit_notional']} tools={tools} numeric />
                    <Head label="total $" toolKeys={['total_notional']} tools={tools} numeric />
                    <Head label="risk $" toolKeys={['total_risk']} tools={tools} numeric />
                    <Head label="pyramid levels" toolKeys={['pyramid_levels']} tools={tools} numeric />
                    <Head label="exit" toolKeys={['exit_level']} tools={tools} numeric />
                    <Head label="capped" toolKeys={['notional_capped']} tools={tools} />
                  </tr>
                </thead>
                <tbody>
                  {setups.map((s, i) => {
                    const plan = s.plan ?? null;
                    const dir = s.direction;
                    // Never "UNKNOWN": an unset direction reads as "undirected".
                    const dirLabel = dir === 1 ? 'long' : dir === -1 ? 'short' : 'undirected';
                    return (
                      <tr
                        key={typeof s.symbol === 'string' && s.symbol !== '' ? s.symbol : `setup-${i}`}
                        className={`${s.accepted === false ? 'rejected' : ''}${selection.isSelected(s.symbol) ? ' selected' : ''}`}
                        title={s.accepted === false ? `rejected: ${text(s.reject_reason)}` : 'plan from survey.setups[]'}
                      >
                        <td className="sym">
                          <HeatBorder symbol={typeof s.symbol === 'string' ? s.symbol : ''} tools={tools}>
                            <button
                              type="button"
                              className={`sym-btn${selection.isSelected(s.symbol) ? ' selected' : ''}`}
                              aria-pressed={selection.isSelected(s.symbol)}
                              onClick={() => {
                                selection.selectSymbol(String(s.symbol ?? ''));
                                onDrill?.(String(s.symbol ?? ''), { setup: s, trade: s.plan ?? null });
                              }}
                              title={`${text(s.symbol)} — click for the trade drill-down`}
                            >
                              {text(s.symbol)}
                            </button>
                          </HeatBorder>
                        </td>
                        <td className={tone(dir)}>{dirLabel}</td>
                        <td className="num">{int(s.score)}</td>
                        <td className="num">{px(s.close)}</td>
                        <td className="num">{share(s.atr_pct, 2)}</td>
                        <td className="num">{num(s.adx, 1)}</td>
                        <td className="num">{num(s.rsi, 1)}</td>
                        <td className="num">{num(s.extension_up_atr, 2)}</td>
                        <td>{text(s.group)}</td>
                        <td className={s.accepted === true ? 'up' : s.accepted === false ? 'down' : ''}>
                          {s.accepted === true
                            ? 'accepted'
                            : s.accepted === false
                              ? text(s.reject_reason)
                              : 'not scored'}
                        </td>
                        <td className="num">{px(plan?.entry)}</td>
                        <td className="num">{px(plan?.stop)}</td>
                        <td className="num">{int(plan?.units)}</td>
                        <td className="num">{usd(plan?.unit_notional)}</td>
                        <td className="num">{usd(plan?.total_notional)}</td>
                        <td className="num">{usd(plan?.total_risk)}</td>
                        <td className="num">{joinList(plan?.pyramid_levels)}</td>
                        <td className="num">{px(plan?.exit_level)}</td>
                        <td>{flag(plan?.notional_capped)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="subhead">
            exit signals <span className="num">{int(exits.length)}</span>
          </h3>
          {exits.length === 0 ? (
            <p className="empty">none</p>
          ) : (
            <div className="table-wrap">
              <table className="survey-table">
                <thead>
                  <tr>
                    <Head label="symbol" toolKeys={['symbol']} tools={tools} />
                    <Head label="close" toolKeys={['closing_price', 'price']} tools={tools} numeric />
                    <Head label="exit long" toolKeys={['exit_long']} tools={tools} />
                    <Head label="exit short" toolKeys={['exit_short']} tools={tools} />
                    <Head label="low 10" toolKeys={['exit_low_10']} tools={tools} numeric />
                    <Head label="adx" toolKeys={['adx14', 'adx']} tools={tools} numeric />
                  </tr>
                </thead>
                <tbody>
                  {exits.map((e, i) => (
                    <tr
                      key={typeof e.symbol === 'string' && e.symbol !== '' ? e.symbol : `exit-${i}`}
                      className={selection.isSelected(e.symbol) ? 'selected' : ''}
                    >
                      <td className="sym">
                        <HeatBorder symbol={typeof e.symbol === 'string' ? e.symbol : ''} tools={tools}>
                          <button
                            type="button"
                            className={`sym-btn${selection.isSelected(e.symbol) ? ' selected' : ''}`}
                            aria-pressed={selection.isSelected(e.symbol)}
                            onClick={() => {
                              selection.selectSymbol(String(e.symbol ?? ''));
                              onDrill?.(String(e.symbol ?? ''));
                            }}
                            title={`${text(e.symbol)} — click for the trade drill-down`}
                          >
                            {text(e.symbol)}
                          </button>
                        </HeatBorder>
                      </td>
                      <td className="num">{px(e.close)}</td>
                      <td className={e.exit_long === true ? 'down' : ''}>{flag(e.exit_long)}</td>
                      <td className={e.exit_short === true ? 'down' : ''}>{flag(e.exit_short)}</td>
                      <td className="num">{px(e.exit_low_10)}</td>
                      <td className="num">{num(e.adx, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
      )}
    </ResizablePanel>
  );
});
