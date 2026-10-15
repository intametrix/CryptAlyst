/**
 * Glossary glue for the reusable tooltip.
 *
 * Copy comes from `GET /api/tools` when the backend has it, from a local
 * fallback map when it does not, and from the raw key as a last resort. It must
 * be impossible for a tooltip to render `undefined` or to throw — an offline
 * backend has to carry no penalty beyond terser copy.
 */

import { toNum } from './format';
import type { ToolEntry, ToolsMsg } from './types';

/* ------------------------------------------------------------- local copy */

/** Fallback copy for indicator/chart keys (the chart chips own more detail). */
export const LOCAL_INDICATORS: Readonly<Record<string, ToolEntry>> = {
  // The wire keys the backend actually sends, so a chart footer or chip can ask
  // for `atr20`/`adx14`/`rsi14` directly and still get real copy.
  atr20: {
    title: 'ATR(20) — Average True Range, the Turtles’ N',
    formula: 'Wilder-smoothed mean of True Range over 20 bars',
    meaning: 'Average distance price travels per bar, in price units.',
    reading: 'Sizes positions and stops. A 2N stop is twice this. Rising ATR means volatility expanding.',
    units: 'price',
  },
  adx14: {
    title: 'ADX(14) — Average Directional Index',
    formula: 'Wilder-smoothed DX(14), DX = 100·|+DI − −DI| / (+DI + −DI)',
    meaning: 'Trend strength regardless of direction, on a 0-100 scale.',
    reading: 'Below 20 is choppy, above 25 is trending, 40+ is a strong trend.',
    units: '0-100',
  },
  rsi14: {
    title: 'RSI(14) — Relative Strength Index',
    formula: 'RSI = 100 − 100/(1 + average gain / average loss) over 14 bars',
    meaning: 'Momentum as the ratio of recent gains to losses, 0-100.',
    reading: 'Above 70 is stretched, below 30 washed out. Strong trends stay pinned for a long time.',
    units: '0-100',
  },
  atr: {
    title: 'Average True Range (ATR)',
    formula: 'Wilder-smoothed mean of True Range over N bars',
    meaning: 'How far price travels per bar, in price units.',
    reading: 'Sets position size and stop distance — a 2N stop is twice this.',
    units: 'price',
  },
  adx: {
    title: 'ADX — Average Directional Index',
    formula: 'Wilder-smoothed DX, DX = 100·|+DI − −DI| / (+DI + −DI)',
    meaning: 'Trend strength, direction-agnostic, 0-100.',
    reading: 'Under 20 is chop, over 25 is a trend, 40+ is strong.',
    units: '0-100',
  },
  plus_di: {
    title: '+DI — positive directional indicator',
    formula: 'Wilder-smoothed upward movement / ATR × 100',
    meaning: 'Upward directional pressure.',
    reading: '+DI above −DI favours longs; the gap widening means the up-trend is strengthening.',
    units: '0-100',
  },
  minus_di: {
    title: '−DI — negative directional indicator',
    formula: 'Wilder-smoothed downward movement / ATR × 100',
    meaning: 'Downward directional pressure.',
    reading: '−DI above +DI favours shorts. Both falling means the trend is tiring.',
    units: '0-100',
  },
  rsi: {
    title: 'RSI — Relative Strength Index',
    formula: 'RSI = 100 − 100/(1 + average gain / average loss)',
    meaning: 'Momentum as the ratio of recent gains to losses, 0-100.',
    reading: 'Above 70 stretched, below 30 washed out. Trends can stay pinned.',
    units: '0-100',
  },
  donchian_channel: {
    title: 'Donchian channel',
    formula: 'highest high(N) and lowest low(N) per bar',
    meaning: 'The N-bar price range, the Turtles’ breakout envelope.',
    reading: 'Close outside the channel is a breakout signal; a squeeze precedes expansion.',
    units: 'price',
  },
  ema: {
    title: 'EMA — exponential moving average',
    formula: 'EMA_t = close_t·k + EMA_(t−1)·(1−k), k = 2/(N+1)',
    meaning: 'A moving average weighted toward recent bars.',
    reading: 'Price above a rising EMA is bullish; crossing it warns of a stall.',
    units: 'price',
  },
  sma: {
    title: 'SMA — simple moving average',
    formula: 'mean of the last N closes',
    meaning: 'An equal-weighted average of the last N bars.',
    reading: 'Used as a regime filter: above the 200 is a bull market.',
    units: 'price',
  },
  atr_pct: {
    title: 'ATR % — normalised volatility',
    formula: 'ATR / close',
    meaning: 'Per-bar travel as a fraction of price, so symbols are comparable.',
    reading: 'Rising means volatility expanding; the survey rejects symbols above its cap.',
    units: 'fraction',
  },
  volume_median_20: {
    title: 'Volume median (20)',
    formula: 'median of the previous 20 bars’ quote volume',
    meaning: 'The symbol’s own “normal” turnover, robust to spikes.',
    reading: 'Current volume divided by this is the volume ratio.',
    units: 'USD',
  },
  volume_ratio: {
    title: 'Volume ratio',
    formula: 'current quote volume / median of the previous 20 bars',
    meaning: 'How many times the usual turnover this bar is doing.',
    reading: 'Above 2x is unusual participation; the screen gates on it.',
    units: 'x',
  },
  trend: {
    title: 'Trend',
    formula: 'sign of the last close versus the previous close',
    meaning: 'A one-word read of the latest bar’s direction.',
    reading: 'up / down / flat. Context only — not a signal by itself.',
    units: 'text',
  },
  price: {
    title: 'Price',
    formula: 'last trade price',
    meaning: 'The most recent traded price for the symbol.',
    reading: 'Compare with the entry and stop levels in the survey plan.',
    units: 'price',
  },
  score: {
    title: 'Score',
    formula: 'weighted sum of flow, speed, aggression, and quality components',
    meaning: 'A single 0-100 number for how convincing the detected burst is.',
    reading: 'Higher is stronger. CRITICAL alerts sit at the top of this scale.',
    units: '0-100',
  },
  severity: {
    title: 'Severity',
    formula: 'thresholds applied to the score',
    meaning: 'WATCH is interesting, ALERT is actionable, CRITICAL is exceptional.',
    reading: 'Use it to triage: CRITICAL first, WATCH only if you have spare attention.',
    units: 'WATCH | ALERT | CRITICAL',
  },
  kind: {
    title: 'Kind',
    formula: 'direction of the detected burst',
    meaning: 'PUMP, DUMP, or a reversal variant.',
    reading: 'PUMP is up, DUMP is down, REVERSAL_* marks an exhaustion turn.',
    units: 'text',
  },
  interval: {
    title: 'Interval',
    formula: 'kline timeframe',
    meaning: 'How much time each candle covers.',
    reading: 'Shorter intervals resolve the burst better; longer ones show the trend it sits in.',
    units: '1m | 5m | 15m | 1h | 4h | 1d',
  },
  why_no_trade: {
    title: 'Why no trade',
    formula: 'the gate that refused the setup',
    meaning:
      'The survey scores every symbol but only trades some. This names the exact filter that said no.',
    reading:
      'Volume not confirmed, ATR out of band, ADX range, BTC regime shut, chasing, or the risk budget being full. When there is no trade, this string is the answer.',
    units: 'text',
  },
  unit_size: {
    title: 'Unit size',
    formula: 'risk per unit / (entry − stop)',
    meaning: 'How much of the instrument one unit buys.',
    reading: 'Sized so that a stop-out costs the same fraction of equity every time.',
    units: 'base asset',
  },
  mark_p: {
    title: 'P — pump marker',
    formula: 'an onset detected in the up direction (kind PUMP / BREAKOUT_LONG)',
    meaning: 'A pump was detected on this bar; the letter sits below the candle.',
    reading: 'Hover the letter for the score, flow and burst multiple behind it.',
    units: 'text',
  },
  mark_d: {
    title: 'D — dump marker',
    formula: 'an onset detected in the down direction (kind DUMP / BREAKOUT_SHORT)',
    meaning: 'A dump was detected on this bar; the letter sits above the candle.',
    reading: 'Dumps above the bar keep them clear of the candle body and wicks.',
    units: 'text',
  },
  mark_r: {
    title: 'R — reversal marker',
    formula: 'an exhaustion turn (kind REVERSAL_PUMP / REVERSAL_DUMP)',
    meaning: 'A trend reversal was detected, either direction.',
    reading: 'Reversals after a long trend are usually the highest-value events.',
    units: 'text',
  },
  mark_b: {
    title: 'B — breakout marker',
    formula: 'the Turtle detector found a channel breakout on some timeframe',
    meaning: 'A structural breakout, not a tape burst — a slower signal.',
    reading: 'Breakouts can arrive as batch alerts, so check the snapshot age.',
    units: 'text',
  },
  events_only: {
    title: 'Events only',
    formula: 'every indicator hidden, one click',
    meaning:
      'A clean read of when things fired: candles, volume, event badges and the flow pane, with no overlays or indicator panes in the way.',
    reading:
      'The set of indicators you had is remembered, so clicking again restores exactly that set rather than a default. Turning an individual indicator back on leaves the mode and shows it.',
    units: 'text',
  },
  support: {
    title: 'Support',
    formula: 'a price where buying has repeatedly absorbed selling',
    meaning:
      'A level below the current price with a measured strength and a count of how many times price has tested it.',
    reading:
      'The stronger and more-tested the level, the more meaningful a bounce from it. Lines on the chart are drawn more opaque the stronger they are.',
    units: 'price',
  },
  resistance: {
    title: 'Resistance',
    formula: 'a price where selling has repeatedly capped buying',
    meaning: 'A level above the current price, measured the same way as support.',
    reading: 'Distance to the level is shown as a percentage, so you can judge whether it is worth waiting for.',
    units: 'price',
  },
  eta_reversal: {
    title: 'ETA to reversal',
    formula: 'from the forecast half-life, measured against prior excursions on this symbol',
    meaning: 'How long the current excursion is expected to keep running before it turns.',
    reading:
      'It is an estimate with a range, not a deadline — the low/high figures are given because the distribution is wide. The basis names how many prior excursions it was measured from.',
    units: 'seconds',
  },
  short_horizon: {
    title: 'Short horizon',
    formula: 'p_up and p_down over the horizon, from an uncalibrated heuristic',
    meaning: 'The model\u2019s read on the next few seconds, with an explicit statement of whether it has any edge.',
    reading:
      'The edge line is quoted verbatim. It currently says there is no measurable edge at this horizon, and that is the honest answer — the model needs 30 resolved predictions in the ledger before it is fitted.',
    units: 'probability',
  },
  forecast: {
    title: 'Forecast',
    formula: 'half-life decay from the current anchor, with a lower and upper bound per point',
    meaning: 'A projection of where price is expected to go, drawn differently from history on purpose.',
    reading:
      'The dashed line is the expected path and the shaded band is the uncertainty around it. A bare line would imply precision the model does not have.',
    units: 'price',
  },
  flow_split: {
    title: 'Flow split',
    formula: 'the strongest bursts up and down in the current window',
    meaning:
      'The same flow measurement as the leaderboard, separated by direction so a pump and a dump are never mixed in one list.',
    reading:
      'Each row shows the 10-second move next to the 24-hour change: a +2% 10s bounce inside a -15% day is not a pump. When both panels are empty the drop counters say why.',
    units: 'text',
  },
  flow_burst: {
    title: 'Flow pane — burst multiple',
    formula: 'flow_quote / baseline_quote, per 10-second bin',
    meaning:
      'Each bar is one ten-second window: the quote value traded in it, divided by that symbol\u2019s own baseline for the same window. It is the number the onset detector actually thresholds on.',
    reading:
      'The dashed line is 1.0x — normal flow. Above it, volume arrived faster than this symbol usually manages; 8x is the alert gate and 20x+ is exceptional. Flow and price answer different questions: a candle shows what price did, this pane shows whether abnormal volume caused it.',
    units: 'x baseline',
  },
  live: {
    title: 'Live updates',
    formula: 'GET /api/klines/latest polled every second, plus SSE price ticks',
    meaning:
      'History is fetched once; after that only the forming candle is updated in place, so the chart keeps your pan and zoom.',
    reading:
      'The age shown is how long ago the last update arrived. It grows while the tab is hidden, because polling pauses.',
    units: 'seconds',
  },
  close: {
    title: 'Close chart',
    formula: 'clears the selected symbol',
    meaning: 'Dismisses the floating chart and returns the page to its normal layout.',
    reading: 'Escape does the same, as does clicking the dimmed backdrop. Clicking inside the panel never closes it.',
    units: 'text',
  },
  closing_price: {
    title: 'Closing price',
    formula: 'the last traded price of the bar',
    meaning: 'Where the instrument finished the period.',
    reading: 'The survey plans entries, stops and exits relative to this level.',
    units: 'price',
  },
  direction: {
    title: 'Direction',
    formula: '+1 for a long setup, −1 for a short',
    meaning: 'Which way the survey would trade this symbol.',
    reading: 'Long and short setups are sized from the same ATR risk model.',
    units: '+1 / −1',
  },
  plan: {
    title: 'Trade plan',
    formula: 'entry, stop, unit size, units, pyramid levels and exit level',
    meaning: 'The full sizing and exit blueprint for an accepted setup.',
    reading: 'Rejected setups carry no plan — that is the null you may see in the plan column.',
    units: 'text',
  },
  total_units: {
    title: 'Total units',
    formula: 'sum of units across accepted plans',
    meaning: 'How much total risk the survey wants to spend, in units.',
    reading: 'The budget caps this; the cap is what stops the survey over-betting.',
    units: 'count',
  },
  long_units: {
    title: 'Long units',
    formula: 'units allocated to long setups',
    meaning: 'The long side of the budget.',
    reading: 'Compare with short units for the survey’s net bias.',
    units: 'count',
  },
  short_units: {
    title: 'Short units',
    formula: 'units allocated to short setups',
    meaning: 'The short side of the budget.',
    reading: 'A one-sided budget is normal: the Turtles follow whatever breaks out.',
    units: 'count',
  },
  drag: {
    title: 'Move chart',
    formula: 'pointer drag on the header',
    meaning: 'Repositions the floating chart. Its position is remembered in this browser.',
    reading: 'The panel cannot be dragged off-screen; drag it back or press reset layout if it ends up somewhere awkward.',
    units: 'text',
  },
  heat: {
    title: 'Heat — composite interesting-ness',
    formula: 'weighted blend of flow, move, trend, level and range components, 0-100',
    meaning:
      'A single number for how interesting a ticker is right now, from any cause: volume/flow, a fast move, a trend, a level test, or a range break.',
    reading:
      'Under 25 the dashboard draws nothing. From 25 up, a light green border appears and gets brighter with the value — the brightest tickers deserve attention first.',
    units: '0-100',
  },
  layout: {
    title: 'Layout',
    formula: 'sizes persisted in localStorage under pumpdump.layout.v1',
    meaning: 'Every resizable box remembers its size in this browser.',
    reading: 'Drag a grip in the corner of any panel, or focus it and use the arrow keys (Shift for bigger steps).',
    units: 'text',
  },
  tools: {
    title: 'Indicator glossary',
    formula: 'GET /api/tools',
    meaning: 'The backend-supplied copy behind every tooltip: title, formula, meaning, reading and units.',
    reading:
      'When the endpoint is unavailable the dashboard falls back to built-in copy, so tooltips still work — it just says so.',
    units: 'text',
  },
  total_notional: {
    title: 'Total notional',
    formula: 'sum of unit_notional across accepted plans',
    meaning: 'The dollar value the survey wants deployed.',
    reading: 'Capped by the equity and the per-position notional limit.',
    units: 'USD',
  },
  total_risk: {
    title: 'Total risk',
    formula: 'sum of (entry − stop) × unit_size across accepted plans',
    meaning: 'What is lost if every stop is hit at once.',
    reading: 'This is the budget the Turtle sizing spends; it should stay within the equity risk cap.',
    units: 'USD',
  },
  positions: {
    title: 'Positions',
    formula: 'count of accepted setups',
    meaning: 'How many separate instruments the survey would hold.',
    reading: 'More positions spreads risk but also dilutes each signal.',
    units: 'count',
  },
  rejected: {
    title: 'Rejected',
    formula: 'count of setups that failed a gate',
    meaning: 'Candidates filtered out this pass.',
    reading: 'A high count with few accepted means the gates are doing their job.',
    units: 'count',
  },
  entry: {
    title: 'Entry',
    formula: 'planned fill price',
    meaning: 'Where the position would be opened.',
    reading: 'Compare with the live price; a big gap means the setup has moved.',
    units: 'price',
  },
  stop: {
    title: 'Stop',
    formula: 'entry − 2 × ATR, or the 10-bar exit level',
    meaning: 'The price at which the trade is admitted to be wrong.',
    reading: 'The distance to the stop is what sizes the position (the Turtles’ 2N rule).',
    units: 'price',
  },
  unit_notional: {
    title: 'Unit notional',
    formula: 'unit_size × entry',
    meaning: 'The dollar value of one unit.',
    reading: 'The survey caps this per position, flagging any plan that needed the cap.',
    units: 'USD',
  },
  pyramid_levels: {
    title: 'Pyramid levels',
    formula: 'entry + 0.5N, entry + 1.0N, entry + 1.5N',
    meaning: 'The prices at which the plan adds another unit.',
    reading: 'Turtles add on strength at half-N intervals and raise the stop to breakeven.',
    units: 'price',
  },
  exit_level: {
    title: 'Exit level',
    formula: 'the 10-bar low (long) or 10-bar high (short)',
    meaning: 'The Turtle exit trigger for the position.',
    reading: 'Tighter than the initial stop: it protects an open profit once the trade runs.',
    units: 'price',
  },
  notional_capped: {
    title: 'Notional capped',
    formula: 'unit_notional exceeded the per-position cap and was clipped',
    meaning: 'The plan wanted more size than the cap allows.',
    reading: 'Capped plans carry less risk than the ATR maths alone would suggest.',
    units: 'yes/no',
  },
  accepted: {
    title: 'Accepted',
    formula: 'all entry gates passed',
    meaning: 'Whether the survey would actually take the trade.',
    reading: 'Rejected rows list their reject_reason — that is why they are dimmed.',
    units: 'yes/no',
  },
  group: {
    title: 'Group',
    formula: 'correlation cluster assigned by the survey',
    meaning: 'Which bucket of correlated instruments the symbol fell into.',
    reading: 'The survey takes at most one position per group, so correlated bets are not doubled.',
    units: 'text',
  },
  pyramid: {
    title: 'Pyramid add-on',
    formula: 'a further unit added as the trend extends',
    meaning: 'Scale-in levels above the entry.',
    reading: 'Adding on strength is the Turtle way; it needs the stop to move with it.',
    units: 'price',
  },
  exit_long: {
    title: 'Exit long',
    formula: 'close ≤ the 10-bar low',
    meaning: 'A long position should be closed.',
    reading: 'The Turtle exit is mechanical: no discretion, just the level.',
    units: 'yes/no',
  },
  exit_short: {
    title: 'Exit short',
    formula: 'close ≥ the 10-bar high',
    meaning: 'A short position should be closed.',
    reading: 'Also the classic stop-and-reverse trigger in the original system.',
    units: 'yes/no',
  },
  exit_low_10: {
    title: '10-bar low',
    formula: 'lowest low of the last 10 bars',
    meaning: 'The level that triggers a long exit.',
    reading: 'Price closing below it ends the long regardless of the stop.',
    units: 'price',
  },
  symbol: {
    title: 'Symbol',
    formula: 'Binance market pair',
    meaning: 'The traded instrument, base asset quoted in USDT.',
    reading: 'Click any symbol anywhere in the dashboard to chart it here.',
    units: 'text',
  },
};

/** Fallback copy for alert-metric and stat keys. */
export const LOCAL_METRICS: Readonly<Record<string, ToolEntry>> = {
  timeframe: {
    title: 'Timeframe',
    formula: 'the bar interval the detector evaluated',
    meaning: 'The Turtle detector scores each symbol on several timeframes independently (1d, 4h, 1h).',
    reading: 'A daily breakout is a bigger claim than an hourly one; the timeframe says which system spoke.',
    units: '1d | 4h | 1h',
  },
  reject_reason: {
    title: 'Reject reason',
    formula: 'the gate the setup failed',
    meaning: 'Why the survey declined to take this setup.',
    reading: 'Rejected rows are dimmed; the reason is the audit trail for that decision.',
    units: 'text',
  },
  baseline_quote: {
    title: 'Baseline quote volume',
    formula: 'typical quote value for the same window',
    meaning: 'What this symbol normally trades in that window.',
    reading: 'The denominator of the flow burst multiple — a tiny baseline makes ratios noisy.',
    units: 'USD',
  },
  detector: {
    title: 'Detector',
    formula: 'the model that emitted the alert',
    meaning:
      'The feed mixes models: flow-onset (live tape bursts), pump-screen (60s market scan), turtle-breakout (indicator breakout).',
    reading:
      'Each detector reports different metrics, so compare like with like. A Turtle breakout is a slower, structural signal; a flow onset is immediate.',
    units: 'flow-onset | pump-screen | turtle-breakout',
  },
  batch: {
    title: 'Batch alert',
    formula: 'the detector evaluates on a timer rather than on every trade',
    meaning: 'Batch detectors only re-run periodically, so the signal is a snapshot, not a live event.',
    reading: 'Check the snapshot age before acting — a batch signal can be a minute old by the time it reaches you.',
    units: 'yes/no',
  },
  snapshot_ms: {
    title: 'Snapshot time',
    formula: 'epoch ms when the batch detector took its snapshot',
    meaning: 'How old the underlying data behind a batch alert is.',
    reading: 'A small age is close to live; a large one means the move may already be over.',
    units: 'epoch ms',
  },
  summary: {
    title: 'Summary',
    formula: 'one-line human description from the detector',
    meaning: 'The headline of the signal in plain words.',
    reading: 'Use it to triage, then read the reasons and metrics for the evidence.',
    units: 'text',
  },
  dispatched_ms: {
    title: 'Dispatched time',
    formula: 'epoch ms when the alert was emitted',
    meaning: 'When the backend sent this alert to the dashboard.',
    reading: 'Compare with the snapshot time: a large gap means a batch signal arrived late.',
    units: 'epoch ms',
  },
  reasons: {
    title: 'Reasons',
    formula: 'the detector’s own justification strings',
    meaning: 'Short evidence lines for the signal, written by the detector.',
    reading: 'These are the gate values that fired, e.g. a 16x flow burst or a 4.6% five-minute move.',
    units: 'text',
  },
  flow: {
    title: 'Flow component',
    formula: 'score points contributed by the flow burst',
    meaning: 'How much of the total score came from unusual traded value.',
    reading: 'A dominant flow component means size, not just speed.',
    units: 'points',
  },
  speed: {
    title: 'Speed component',
    formula: 'score points contributed by how fast the move was',
    meaning: 'How much of the score came from the rate of price change.',
    reading: 'A dominant speed component with weak flow is often a thin-book wick.',
    units: 'points',
  },
  aggression: {
    title: 'Aggression component',
    formula: 'score points contributed by aggressor-side pressure',
    meaning: 'How much of the score came from market orders crossing the spread.',
    reading: 'High aggression means someone is paying the spread to get filled now.',
    units: 'points',
  },
  quality: {
    title: 'Quality component',
    formula: 'score points contributed by data quality and confirmations',
    meaning: 'How much of the score came from the evidence being clean and corroborated.',
    reading: 'Low quality points mean the other components rest on patchy data.',
    units: 'points',
  },
  window_sec: {
    title: 'Window (seconds)',
    formula: 'length of the rolling detection window',
    meaning: 'The recent slice of tape the burst metrics were measured over.',
    reading: 'The gate is a 10s window; a longer window smooths, a shorter one is twitchier.',
    units: 'seconds',
  },
  flow_quote: {
    title: 'Flow (quote)',
    formula: 'sum of quote value traded inside the window',
    meaning: 'How much money actually changed hands in the window.',
    reading: 'The alert gate needs this to clear the configured minimum flow.',
    units: 'USD',
  },
  burst_multiple: {
    title: 'Flow burst multiple',
    formula: 'window quote volume / this symbol’s own baseline for that window',
    meaning: 'Volume in the last window divided by this symbol’s normal volume for the same window.',
    reading: '8x is the alert gate. 20x+ is exceptional.',
    units: 'x',
  },
  burst_z: {
    title: 'Burst z-score',
    formula: '(window volume − baseline mean) / baseline standard deviation',
    meaning: 'How many standard deviations the burst sits above normal.',
    reading: 'z ≥ 4 is the gate. Higher means the burst is rarer, not necessarily bigger.',
    units: 'sigma',
  },
  change_10s: {
    title: 'Price change, 10s',
    formula: 'close_now / close_10s_ago − 1',
    meaning: 'How far price moved in the last ten seconds.',
    reading: 'The PUMP/DUMP gate is ±0.4%. Compare it with the symbol’s own volatility.',
    units: 'fraction',
  },
  change_30s: {
    title: 'Price change, 30s',
    formula: 'close_now / close_30s_ago − 1',
    meaning: 'A slightly longer view of the move than the 10s figure.',
    reading: 'A 30s move that lags the 10s one means the burst is already fading.',
    units: 'fraction',
  },
  change_60s: {
    title: 'Price change, 60s',
    formula: 'close_now / close_60s_ago − 1',
    meaning: 'The one-minute move, for context around the burst.',
    reading: 'A big 60s move with a small 10s one means the pump happened earlier.',
    units: 'fraction',
  },
  sigma_multiple: {
    title: 'Sigma multiple',
    formula: '10s move / that symbol’s typical 10s move',
    meaning: 'The price move expressed in units of its own volatility.',
    reading: 'Above 5 sigma is a genuinely unusual move for that symbol.',
    units: 'x',
  },
  coverage: {
    title: 'Coverage',
    formula: 'observed trades / expected trades from the exchange stream',
    meaning: 'How complete the tape for this symbol is.',
    reading: 'Below 1.0 the metrics are built on partial data — treat them with caution.',
    units: '0-1',
  },
  trades_10s: {
    title: 'Trades in window',
    formula: 'count of trades in the window',
    meaning: 'Raw trade count, regardless of size.',
    reading: 'Many small trades is retail-like; few large ones is a single actor.',
    units: 'count',
  },
  trades_multiple: {
    title: 'Trades multiple',
    formula: 'window trade count / baseline trade count',
    meaning: 'Whether the burst is more trades or just bigger ones.',
    reading: 'High trade multiple means broader participation.',
    units: 'x',
  },
  acceleration: {
    title: 'Acceleration',
    formula: 'recent flow rate / earlier flow rate inside the window',
    meaning: 'Whether the burst is speeding up or already slowing.',
    reading: 'Above 1 means still accelerating — the more dangerous case.',
    units: 'x',
  },
  range_position: {
    title: 'Range position',
    formula: '(close − window low) / (window high − window low)',
    meaning: 'Where the close sits inside the window’s own range.',
    reading: 'Near 1.0 the burst closed at its highs (strong); near 0 it was rejected.',
    units: '0-1',
  },
  spread_pct: {
    title: 'Spread',
    formula: '(ask − bid) / mid',
    meaning: 'The cost of crossing the book right now.',
    reading: 'Wide spreads on a thin symbol can eat the entire move.',
    units: 'fraction',
  },
  book_depth_usd: {
    title: 'Book depth',
    formula: 'quote value resting within the tracked band of the book',
    meaning: 'How much size is available to trade against.',
    reading: 'Shallow books mean slippage; a big order moves price further.',
    units: 'USD',
  },
  quote_volume_24h: {
    title: '24h quote volume',
    formula: 'rolling 24-hour traded value',
    meaning: 'The symbol’s overall liquidity.',
    reading: 'The universe filter needs this above its minimum — thin names are excluded.',
    units: 'USD',
  },
  latency_ms: {
    title: 'Latency',
    formula: 'detection time − event time',
    meaning: 'Delay between the tape and the alert.',
    reading: 'Low is good. High latency means the move may already be over.',
    units: 'ms',
  },
  penalty: {
    title: 'Penalty',
    formula: 'score deductions for data-quality problems',
    meaning: 'Points removed because the evidence was incomplete or dirty.',
    reading: 'A non-zero penalty is a reason to distrust an otherwise high score.',
    units: 'points',
  },
  agg_confirmed: {
    title: 'Aggressor confirmed',
    formula: 'large-trade aggressor side agrees with the move',
    meaning: 'Whether the big prints back up the direction of the burst.',
    reading: 'Yes means aggressive buying/selling, not passive fills.',
    units: 'yes/no',
  },
  agg_buy_quote: {
    title: 'Aggressive buy value',
    formula: 'quote value of trades that lifted the offer',
    meaning: 'Money that crossed the spread to buy.',
    reading: 'Compare against the aggressive sell value to see who is in control.',
    units: 'USD',
  },
  agg_sell_quote: {
    title: 'Aggressive sell value',
    formula: 'quote value of trades that hit the bid',
    meaning: 'Money that crossed the spread to sell.',
    reading: 'Sell value dominating a PUMP alert is a warning sign.',
    units: 'USD',
  },
  agg_buy_ratio: {
    title: 'Aggressive buy ratio',
    formula: 'aggressive buy value / (buy + sell value)',
    meaning: 'The share of aggressive flow that was buying.',
    reading: 'Above 0.5 is net buying pressure; 0.685 is strongly one-sided.',
    units: '0-1',
  },
  agg_trades: {
    title: 'Aggressive trades',
    formula: 'count of trades classified as aggressor-driven',
    meaning: 'How many prints were large enough to be treated as aggressive.',
    reading: 'Few aggressive trades can still move a thin book a long way.',
    units: 'count',
  },
  agg_largest_trade_usd: {
    title: 'Largest aggressive trade',
    formula: 'quote value of the biggest single aggressive print',
    meaning: 'The size of the largest single order behind the move.',
    reading: 'One huge print suggests an institutional actor rather than a crowd.',
    units: 'USD',
  },
  taker_buy_ratio: {
    title: 'Taker buy ratio',
    formula: 'taker buy volume / total volume',
    meaning: 'The share of volume where the buyer crossed the spread.',
    reading: 'Above 0.5 is buy-side pressure. 0.5 exactly can mean “no trade data yet”.',
    units: '0-1',
  },
  ret_5m: {
    title: 'Return, 5 minutes',
    formula: 'close / close_5m_ago − 1',
    meaning: 'The five-minute price change.',
    reading: 'Short horizon: this is the move the screen is reacting to.',
    units: 'fraction',
  },
  ret_15m: {
    title: 'Return, 15 minutes',
    formula: 'close / close_15m_ago − 1',
    meaning: 'The fifteen-minute price change.',
    reading: 'Confirms whether the 5m move is a blip or part of a push.',
    units: 'fraction',
  },
  ret_60m: {
    title: 'Return, 60 minutes',
    formula: 'close / close_60m_ago − 1',
    meaning: 'The one-hour price change.',
    reading: 'A 1h return opposite in sign to the 5m one means mean reversion.',
    units: 'fraction',
  },
  ret_24h: {
    title: 'Return, 24 hours',
    formula: 'close / close_24h_ago − 1',
    meaning: 'The daily price change.',
    reading: 'Context for how extended the symbol already is.',
    units: 'fraction',
  },
  vol_ratio: {
    title: 'Volume ratio',
    formula: 'recent quote volume / baseline quote volume',
    meaning: 'How many times normal turnover the recent bars are doing.',
    reading: 'The screen’s primary evidence of unusual participation.',
    units: 'x',
  },
  vol_z: {
    title: 'Volume z-score',
    formula: '(recent volume − baseline mean) / baseline deviation',
    meaning: 'Volume surprise measured in standard deviations.',
    reading: 'z ≥ 4 separates a real burst from ordinary noise.',
    units: 'sigma',
  },
  recent_quote_volume: {
    title: 'Recent quote volume',
    formula: 'quote value traded over the recent window',
    meaning: 'How much money moved in the window the screen measures.',
    reading: 'Compare it with the baseline to judge how unusual it is.',
    units: 'USD',
  },
  baseline_quote_volume: {
    title: 'Baseline quote volume',
    formula: 'typical quote value for the same window',
    meaning: 'What this symbol normally does in that window.',
    reading: 'The denominator of the volume ratio — a tiny baseline makes ratios noisy.',
    units: 'USD',
  },
  trades_ratio: {
    title: 'Trades ratio',
    formula: 'recent trade count / baseline trade count',
    meaning: 'Whether participation broadened, not just order size.',
    reading: 'High ratios mean many market participants, not one whale.',
    units: 'x',
  },
  close_position: {
    title: 'Close position',
    formula: '(close − low) / (high − low) for the bar',
    meaning: 'Where the bar closed within its own range.',
    reading: 'Near 1.0 the buyers won the bar; near 0 the sellers did.',
    units: '0-1',
  },
  range_pct: {
    title: 'Range %',
    formula: '(high − low) / close',
    meaning: 'The bar’s height as a fraction of price.',
    reading: 'Wide range means a volatile, decisive bar.',
    units: 'fraction',
  },
  atr_pct: {
    title: 'ATR %',
    formula: 'ATR / close',
    meaning: 'Per-bar travel as a fraction of price.',
    reading: 'Comparable across symbols; the survey rejects extremes.',
    units: 'fraction',
  },
  from_24h_high: {
    title: 'From 24h high',
    formula: 'close / 24h_high − 1',
    meaning: 'How far below the day’s high the price is.',
    reading: 'Near 0% means the symbol is at its highs — breakouts live here.',
    units: 'fraction',
  },
  from_24h_low: {
    title: 'From 24h low',
    formula: 'close / 24h_low − 1',
    meaning: 'How far above the day’s low the price is.',
    reading: 'A large value after a dump means the bounce has already run.',
    units: 'fraction',
  },
  bars_since_extreme: {
    title: 'Bars since extreme',
    formula: 'bars elapsed since the window’s highest high',
    meaning: 'How fresh the extreme is.',
    reading: 'Zero or one means the extreme is happening now, not in the past.',
    units: 'bars',
  },
  extension_up_atr: {
    title: 'Extension above the channel (ATR)',
    formula: '(close − donchian_upper) / ATR',
    meaning: 'How far price has pushed beyond its channel, measured in N.',
    reading: 'Under 0.5 is a fresh breakout; large values are extended and prone to snap back.',
    units: 'ATR',
  },
  units: {
    title: 'Units',
    formula: 'risk budget / (2N stop distance)',
    meaning: 'How many position units the plan allocates.',
    reading: 'More units means a tighter stop for the same dollar risk.',
    units: 'count',
  },
  pumps: {
    title: 'Pumps',
    formula: 'count of kind = PUMP in the in-memory alert buffer',
    meaning: 'Upward onset alerts currently held in this session.',
    reading: 'A spike means the market is broadly ramping; compare with dumps.',
    units: 'count',
  },
  dumps: {
    title: 'Dumps',
    formula: 'count of kind = DUMP in the in-memory alert buffer',
    meaning: 'Downward onset alerts currently held in this session.',
    reading: 'Dumps outnumbering pumps is a risk-off tape.',
    units: 'count',
  },
  reversals: {
    title: 'Reversals',
    formula: 'count of REVERSAL_PUMP + REVERSAL_DUMP in the buffer',
    meaning: 'Alerts marking an exhaustion turn rather than an onset.',
    reading: 'Reversals after a long trend are the highest-value signals.',
    units: 'count',
  },
  critical: {
    title: 'Critical',
    formula: 'count of severity = CRITICAL in the buffer',
    meaning: 'The most extreme alerts detected so far.',
    reading: 'Treat these first; they cleared the strictest thresholds.',
    units: 'count',
  },
  universe_volume_24h: {
    title: 'Universe 24h volume',
    formula: 'summed 24h quote volume across tracked symbols',
    meaning: 'Total turnover of the tracked universe.',
    reading: 'A collapsing figure means the market is going quiet.',
    units: 'USD',
  },
  scans: {
    title: 'Scan passes',
    formula: 'status.scans counter',
    meaning: 'How many detection passes the engine has completed.',
    reading: 'A counter that stops rising means the engine stalled.',
    units: 'count',
  },
};

/** Combined local copy map, used when `/api/tools` is missing a key. */
export const LOCAL_TOOLS: Readonly<Record<string, ToolEntry>> = {
  ...LOCAL_INDICATORS,
  ...LOCAL_METRICS,
};

/* ---------------------------------------------------------------- resolving */

/** Where a resolved tooltip came from — surfaced in the UI for honesty. */
export type TooltipSource = 'api' | 'local' | 'key';

/** Fully resolved tooltip content. Never contains `undefined`. */
export interface TooltipSpec {
  title: string;
  /** Optional secondary line, e.g. the chip's own one-liner. */
  label: string | null;
  formula: string | null;
  meaning: string | null;
  reading: string | null;
  units: string | null;
  source: TooltipSource;
}

function clean(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const t = v.trim();
  return t === '' ? null : t;
}

/** Pick the first lookup key that the glossary actually knows about. */
function lookup(
  keys: readonly string[],
  tools: ToolsMsg | null,
): { entry: ToolEntry; source: TooltipSource; key: string } | null {
  for (const key of keys) {
    const fromApi = tools?.indicators?.[key] ?? tools?.metrics?.[key] ?? null;
    if (fromApi !== null && typeof fromApi === 'object') {
      return { entry: fromApi, source: 'api', key };
    }
  }
  for (const key of keys) {
    const local = LOCAL_TOOLS[key];
    if (local !== undefined) return { entry: local, source: 'local', key };
  }
  return null;
}

/**
 * Resolve tooltip content for one or more candidate keys.
 *
 * Order: `/api/tools` entry → local copy → the raw key itself as the title.
 * `fallbackTitle` lets a caller supply a nicer last-resort title (a chip label,
 * a column heading) than the wire key.
 */
export function resolveTooltip(
  keys: readonly string[],
  tools: ToolsMsg | null,
  fallbackTitle?: string,
  label?: string | null,
): TooltipSpec {
  const candidates = keys.filter((k) => typeof k === 'string' && k.trim() !== '');
  const hit = lookup(candidates, tools);
  const labelText = clean(label);

  if (hit === null) {
    const raw = candidates[0] ?? '';
    return {
      title: clean(fallbackTitle) ?? clean(raw) ?? 'no description',
      label: labelText,
      formula: null,
      meaning: null,
      reading: null,
      units: null,
      source: 'key',
    };
  }

  const { entry, source } = hit;
  return {
    title: clean(entry.title) ?? clean(fallbackTitle) ?? candidates[0] ?? 'no description',
    label: labelText,
    formula: clean(entry.formula),
    meaning: clean(entry.meaning),
    reading: clean(entry.reading),
    units: clean(entry.units),
    source,
  };
}

/** True when a resolved tooltip carries more than a bare title. */
export function hasDetail(spec: TooltipSpec): boolean {
  return spec.formula !== null || spec.meaning !== null || spec.reading !== null;
}

/** One-line summary used for container/child tooltips. */
export function summarize(spec: TooltipSpec): string {
  const parts = [spec.title];
  if (spec.meaning !== null) parts.push(spec.meaning);
  else if (spec.reading !== null) parts.push(spec.reading);
  if (spec.units !== null && spec.units !== 'text') parts.push(`units: ${spec.units}`);
  return parts.join(' — ');
}

/**
 * Guarded read of `indicators.*[i]` used by the chart footer: returns a finite
 * number or `null`, never a bogus zero and never a thrown error.
 */
export function indicatorAt(series: unknown, index: number): number | null {
  if (!Array.isArray(series) || index < 0 || index >= series.length) return null;
  return toNum(series[index]);
}
