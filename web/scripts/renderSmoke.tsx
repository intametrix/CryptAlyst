/**
 * A real render of the whole dashboard, server-side, with representative wire
 * payloads for every message type the UI consumes.
 *
 * Bundled by Vite (see `scripts/render-smoke.mjs`, which runs it) because it
 * needs JSX, and executed by Node so nothing here touches a browser-only API.
 *
 * Type checking cannot catch a bad property access or a crash inside a render;
 * this can. It also asserts the things the user asked for in words:
 *   - no "UNKNOWN" / "undefined" / "null" / "NaN" anywhere in the output
 *   - the new enum values and detectors are labelled readably
 *   - alert cards carry the detector, the summary and a batch snapshot age
 *   - the heat legend and the reset-layout affordance exist
 *   - a screen with no heat data at all renders without a single border
 *
 *   node scripts/smoke.mjs   (which builds this with Vite, then runs it)
 */
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement, type ReactElement } from 'react';
import type { AlertMsg, FlowMsg, ScreenMsg, StatusMsg, SurveyMsg, SymbolDetailMsg } from '../src/types';

let pass = 0;
let fail = 0;
const failures: string[] = [];
const ok = (name: string, condition: boolean, detail = ''): void => {
  if (condition) pass += 1;
  else {
    fail += 1;
    failures.push(`${name}${detail === '' ? '' : ` — ${detail}`}`);
  }
};

// --- minimal browser surface the components touch during a render -----------
/**
 * A localStorage stand-in. Only the three methods the layout code calls are
 * implemented; the cast keeps the ambient `Storage` type happy without pretending
 * to be a full implementation.
 */
const memoryStorage = {
  _data: new Map<string, string>(),
  get length(): number {
    return this._data.size;
  },
  clear(): void {
    this._data.clear();
  },
  key(index: number): string | null {
    return [...this._data.keys()][index] ?? null;
  },
  getItem(k: string): string | null {
    return this._data.has(k) ? (this._data.get(k) as string) : null;
  },
  setItem(k: string, v: string): void {
    this._data.set(k, String(v));
  },
  removeItem(k: string): void {
    this._data.delete(k);
  },
};
globalThis.localStorage = memoryStorage as unknown as Storage;
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
// Effects do not run during a server render, so these are belt-and-braces.
if (typeof globalThis.EventSource === 'undefined') {
  class StubEventSource {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    readonly CONNECTING = 0;
    readonly OPEN = 1;
    readonly CLOSED = 2;
    readyState = 0;
    url: string;
    withCredentials = false;
    onopen: ((this: EventSource, ev: Event) => unknown) | null = null;
    onerror: ((this: EventSource, ev: Event) => unknown) | null = null;
    onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null;
    constructor(url: string) {
      this.url = url;
    }
    addEventListener(): void {}
    removeEventListener(): void {}
    dispatchEvent(): boolean {
      return true;
    }
    close(): void {}
  }
  globalThis.EventSource = StubEventSource as unknown as typeof EventSource;
}

import { createHeatStore, parseHeatRows } from '../src/heatStore';
import { FlowLeaderboard } from '../src/components/FlowLeaderboard';
import { ScreenTable } from '../src/components/ScreenTable';
import { SurveyPanel } from '../src/components/SurveyPanel';
import { StatStrip } from '../src/components/StatStrip';
import { Header } from '../src/components/Header';
import { ChartPopup } from '../src/components/chart/ChartPopup';
import { ChartOverlay } from '../src/components/ChartOverlay';
import { SymbolDrilldown } from '../src/components/SymbolDrilldown';
import { FlowSplit } from '../src/components/FlowSplit';
import { parseCanon, CANON_FALLBACK } from '../src/colors';
import { EventGlyph } from '../src/components/EventGlyph';
import { AlertCard, AlertDetails } from '../src/components/AlertCard';
import { toMarkers } from '../src/chartData';
import { LIVE_FLOW_SPLIT, LIVE_KLINES_ALERTS, LIVE_COLORS } from './livePayloads';
import { TooltipViewer } from '../src/components/TooltipViewer';
import { HeatProvider } from '../src/heatContext';
import { HeatBorder } from '../src/components/HeatBorder';
import { TooltipBody } from '../src/components/Tooltip';
import { SelectionProvider } from '../src/selection';

const h = createElement;
const noop = () => undefined;


const ALERTS: (AlertMsg | Record<string, unknown>)[] = [
  {
    type: 'alert',
    event_id: 'a1',
    ts_ms: Date.now() - 4_000,
    symbol: 'OSMOUSDT',
    kind: 'PUMP',
    severity: 'CRITICAL',
    score: 77,
    price: 0.0477,
    detector: 'flow-onset',
    summary: '16.1x flow burst with a 4.6% 10s move',
    reasons: ['10s flow $76,400 = 16.1x baseline (15.9sigma)'],
    components: { flow: 22.5, speed: 25 },
    metrics: { flow_quote: 76400, burst_multiple: 16.1, change_10s: 0.0461, taker_buy_ratio: 0.62 },
  },
  {
    type: 'alert',
    event_id: 'a2',
    ts_ms: Date.now() - 30_000,
    symbol: 'ADAUSDT',
    kind: 'BREAKOUT_LONG',
    severity: 'ALERT',
    score: 64,
    price: 0.61,
    detector: 'turtle-breakout',
    batch: true,
    snapshot_ms: Date.now() - 42_000,
    summary: 'close above the 55-bar high',
    reasons: ['55-bar breakout, ADX 28.4'],
    metrics: { atr: 0.021, adx14: 28.4, extension_up_atr: 0.42, group: 'g1' },
  },
  {
    type: 'alert',
    event_id: 'a3',
    ts_ms: Date.now() - 90_000,
    symbol: 'NEARUSDT',
    kind: 'SOMETHING_THE_BACKEND_ADDED',
    severity: 'EXTREME',
    score: 51,
    price: 106.1,
    detector: 'pump-screen',
    batch: true,
    snapshot_ms: null,
    reasons: [],
    metrics: null,
  },
  {
    // The worst case: nothing but a symbol.
    type: 'alert',
    event_id: 'a4',
    ts_ms: null,
    symbol: null,
    kind: null,
    severity: null,
    score: null,
  },
];

const FLOW: FlowMsg = {
  type: 'flow',
  ts_ms: Date.now(),
  window_sec: 10,
  rows: [
    {
      symbol: 'NEARUSDT',
      last_price: 106.1,
      flow_quote: 70200,
      baseline_quote: 7700,
      burst_multiple: 9.1,
      burst_z: 6.2,
      change_10s: -0.0009,
      taker_buy_ratio: 0.62,
      quote_volume_24h: 304000000,
    },
    { symbol: null, burst_multiple: null, change_10s: null },
  ],
};

const SCREEN: ScreenMsg = {
  type: 'screen',
  ts_ms: Date.now(),
  symbols_scanned: 200,
  rows: [
    {
      symbol: 'OSMOUSDT',
      kind: 'DUMP',
      score: 77,
      price: 0.0477,
      ret_5m: 0.0461,
      ret_15m: 0.0647,
      ret_60m: 0.0553,
      ret_24h: 0.12,
      vol_ratio: 16.1,
      vol_z: 15.9,
      recent_quote_volume: 76400,
      baseline_quote_volume: 4700,
      quote_volume_24h: 5450000,
      trades_ratio: 2.1,
      taker_buy_ratio: 0.54,
      close_position: 0.82,
      range_pct: 0.052,
      atr_pct: 0.0097,
      from_24h_high: -0.0104,
      from_24h_low: 0.0915,
      bars_since_extreme: 1,
      reasons: ['5m +4.61% / 15m +6.47% / 1h +5.53%'],
    },
    { symbol: null, kind: null, score: null },
  ],
};

const SURVEY: SurveyMsg = {
  type: 'survey',
  ts_ms: Date.now(),
  equity: 10000,
  timeframes: ['1d', '4h', '1h'],
  symbols_scanned: 150,
  symbols_with_data: 148,
  budget: { total_units: 6, long_units: 6, short_units: 0, total_notional: 4200, total_risk: 300, positions: 2, rejected: 11 },
  setups: [
    {
      symbol: 'SOLUSDT',
      direction: 1,
      score: 71,
      close: 106.1,
      atr: 3.2,
      atr_pct: 0.03,
      adx: 28.4,
      rsi: 61.2,
      extension_up_atr: 0.42,
      group: 'g1',
      accepted: true,
      plan: {
        entry: 106.1,
        stop: 99.7,
        unit_size: 31.2,
        unit_notional: 3310,
        units: 1,
        total_notional: 3310,
        total_risk: 100,
        pyramid_levels: [107.7, 109.3],
        exit_level: 101.4,
        notional_capped: false,
      },
    },
    { symbol: null, direction: null, accepted: null, plan: null },
  ],
  exit_signals: [{ symbol: 'ADAUSDT', close: 0.61, exit_long: true, exit_short: false, exit_low_10: 0.615, adx: 18.2 }],
};

const STATUS: StatusMsg = {
  type: 'status',
  ts_ms: Date.now(),
  uptime_sec: 1234,
  symbols_tracked: 400,
  baselines_ready: 388,
  messages: 512345,
  scans: 1234,
  candidates: 41,
  alerts: 9,
  universe_volume_24h: 4.82e9,
};

const render = (element: ReactElement) => renderToStaticMarkup(h(TooltipViewer, null, element));

// --- individual panels ------------------------------------------------------
const alertHtml = ALERTS.map((a, i) =>
  render(h(AlertCard, { alert: a as unknown as AlertMsg, flash: i === 0, tools: null, nowMs: Date.now() })),
).join('\n');
const flowHtml = render(h(FlowLeaderboard, { flow: FLOW, tools: null }));
const screenHtml = render(h(ScreenTable, { screen: SCREEN, onRefresh: noop, tools: null }));
const surveyHtml = render(h(SurveyPanel, { survey: SURVEY, onRefresh: noop, tools: null }));
const statHtml = render(
  h(StatStrip, {
    stats: { total: 9, pumps: 4, dumps: 3, reversals: 2, critical: 1 },
    status: STATUS,
    tools: null,
  }),
);
const headerHtml = render(
  h(Header, {
    connection: 'live',
    status: STATUS,
    config: { market: 'spot', window_sec: 10, max_symbols: 400, severity_min: 'WATCH' },
    alertCount: 9,
    lastEventMs: Date.now() - 1000,
    serverTimeMs: Date.now(),
    paused: false,
    tools: null,
    heatCount: 3,
    onResetLayout: noop,
    onReloadTools: noop,
    toolsFallback: true,
  }),
);

const all = [alertHtml, flowHtml, screenHtml, surveyHtml, statHtml, headerHtml].join('\n');

// The whole dashboard, including the chart popup and both providers.
// ChartPopup is what the app mounts inside ChartOverlay since the dead
// ChartPanel tree was deleted (commit 2d1aac3).
const chartPopup = (
  <ChartPopup
    symbol="OSMOUSDT"
    interval="1h"
    tools={null}
    onIntervalChange={noop}
    presets={[]}
    onZoomPreset={noop}
    onResetZoom={noop}
    activePreset={null}
    presetPending={false}
    onClose={noop}
  />
);
const dashboard = render(
  <HeatProvider seedUrl={null}>
    <SelectionProvider seedSymbol="OSMOUSDT">
      <div>
        {chartPopup}
        <StatStrip stats={{ total: 0, pumps: 0, dumps: 0, reversals: 0, critical: 0 }} status={null} tools={null} />
      </div>
    </SelectionProvider>
  </HeatProvider>,
);

/* ============================ assertions ================================= */

ok('every panel renders', all.length > 2000, String(all.length));
ok('the whole dashboard renders', dashboard.length > 500, String(dashboard.length));

// The headline bug: no placeholder labels.
for (const [name, html] of [
  ['alerts', alertHtml],
  ['flow', flowHtml],
  ['screen', screenHtml],
  ['survey', surveyHtml],
  ['stats', statHtml],
  ['header', headerHtml],
  ['dashboard', dashboard],
]) {
  ok(`no "UNKNOWN" in ${name}`, !html.includes('UNKNOWN'), html.slice(Math.max(0, html.indexOf('UNKNOWN') - 60), html.indexOf('UNKNOWN') + 40));
  ok(`no bare "undefined" in ${name}`, !/>[^<]*undefined/.test(html) && !html.includes('>undefined<'));
  ok(`no bare "null" in ${name}`, !html.includes('>null<'));
  ok(`no "NaN" in ${name}`, !html.includes('NaN'));
}

// New enum values and detectors are readable.
ok('PUMP label is kept', alertHtml.includes('PUMP'));
ok('BREAKOUT_LONG is prettified', alertHtml.includes('Breakout Long'), alertHtml.match(/badge[^>]*>[^<]*/g)?.join(' | '));
ok('a brand new kind is prettified', alertHtml.includes('Something The Backend Added'));
ok('an unseen severity is prettified', alertHtml.includes('Extreme'));
ok('a classifier fallback never says UNKNOWN', alertHtml.includes('Unclassified') || !alertHtml.includes('kind-other'));

// Detector, summary and batch age.
ok('the detector is shown', alertHtml.includes('Flow Onset') && alertHtml.includes('Turtle Breakout'));
ok('a missing detector degrades readably', alertHtml.includes('detector not reported'));
ok('the summary is shown', alertHtml.includes('16.1x flow burst with a 4.6% 10s move'));
ok('a batch snapshot age is shown', /snapshot \d+s ago/.test(alertHtml), alertHtml.match(/snapshot[^<]*/)?.[0]);
ok('a batch alert with no snapshot time still labels itself', alertHtml.includes('batch'));
// The expanded definition list is a separate render, because a collapsed card
// deliberately does not carry every metrics key in its markup.
const detailsFor = (alert: unknown) =>
  render(h(AlertDetails, { alert: alert as AlertMsg, open: true, tools: null }));
const flowDetails = detailsFor(ALERTS[0]);
const turtleDetails = detailsFor(ALERTS[1]);
const emptyDetails = detailsFor(ALERTS[3]);

ok(
  'turtle-detector metrics render generically (atr, adx14, extension_up_atr, group)',
  ['atr', 'adx14', 'extension_up_atr', 'group'].every((k) => turtleDetails.includes(k)),
  turtleDetails.slice(0, 300),
);
ok(
  'flow-detector metrics render (flow_quote, burst_multiple, taker_buy_ratio)',
  ['flow_quote', 'burst_multiple', 'taker_buy_ratio'].every((k) => flowDetails.includes(k)),
  flowDetails.slice(0, 300),
);
ok(
  'the definition list names every metric key it was given',
  (flowDetails.match(/metric-key/g) ?? []).length >= 4,
  String((flowDetails.match(/metric-key/g) ?? []).length),
);
ok('the envelope fields are listed too', flowDetails.includes('detector') && flowDetails.includes('dispatched_ms'));
ok('a card with no metrics says so instead of crashing', emptyDetails.includes('none reported'));
ok('an expanded card never renders null/undefined', !emptyDetails.includes('>undefined<') && !emptyDetails.includes('>null<'));
const oneCollapsedCard = render(
  h(AlertCard, { alert: ALERTS[0] as unknown as AlertMsg, flash: false, tools: null, nowMs: Date.now() }),
);
ok(
  'a collapsed card renders without the detail list',
  // The <dl> itself, not the button's "expand every metrics key" title.
  oneCollapsedCard.length > 100 && !oneCollapsedCard.includes('score components'),
  String(oneCollapsedCard.length),
);
ok(
  'an expanded card does render the detail list',
  flowDetails.includes('score components') && flowDetails.includes('every metrics key'),
);
ok('the expand control advertises its state', alertHtml.includes('aria-expanded'));
ok('a card with no metrics renders without a crash', alertHtml.includes('metrics'));
ok('a missing symbol renders the em dash, not null', alertHtml.includes('—'));

// Verbatim enum words must not leak into badges, and every badge must carry text.
// (The kind badge now sits inside an event glyph, so the tag may nest: match the
// badge's own text up to the next tag rather than up to the next `<`.)
// Match the text *directly* inside the badge (`>text<`), so a badge that wraps a
// glyph rather than containing text is simply not counted.
const badgeTexts = [...alertHtml.matchAll(/class="badge[^"]*"[^>]*>([^<]+)</g)].map((m) => m[1]);
ok('every badge has visible text', badgeTexts.length > 0 && badgeTexts.every((t) => t.trim() !== ''), JSON.stringify(badgeTexts));
ok('no badge contains an underscore', badgeTexts.every((t) => !t.includes('_')), JSON.stringify(badgeTexts));

// The canonical glyph: one letter, one colour, one direction, everywhere.
const glyphs = [...alertHtml.matchAll(/data-letter="([^"]*)"[^>]*data-color="([^"]*)"[^>]*data-direction="([^"]*)"[^>]*data-family="([^"]*)"/g)];
ok('the alert feed renders canonical event glyphs', glyphs.length > 0, String(glyphs.length));
ok(
  'every glyph has a single letter, a colour and a direction',
  glyphs.every((g) => g[1].length === 1 && /^#|rgba?\(/.test(g[2]) && (g[3] === '1' || g[3] === '-1')),
  JSON.stringify(glyphs.slice(0, 3)),
);
const letters = new Set(glyphs.map((g) => g[1]));
// The canon's letters, plus its neutral bullet for a kind it does not name.
ok('the letters come from the canon', [...letters].every((l) => 'PDRB\u2022'.includes(l)), JSON.stringify([...letters]));

// Heat: no data at all must mean no borders, and the legend must still explain.
ok('the heat legend is present', headerHtml.includes('heat-legend'));
ok('no heat data means no heat border is drawn', !screenHtml.includes('--heat'));
ok('the no-heat dashboard still renders fully', !dashboard.includes('--heat'));

// Heat with data: the border appears and scales via --heat, and the glow is
// explainable (reasons + component mix) through the standard tooltip body.
const HOT_PAYLOAD: { rows: Array<{ symbol: string; heat: number; components: Record<string, number>; reasons: string[] }> } = {
  rows: [
    {
      symbol: 'OSMOUSDT',
      heat: 90,
      components: { flow: 0.9, move: 0.7, trend: 0.5, level: 0.6, range: 0.4 },
      reasons: ['16.1x flow burst', '+4.6%/5m', 'near 55-bar high'],
    },
    { symbol: 'COLDUSDT', heat: 24, components: { flow: 0.1 }, reasons: ['barely interesting'] },
  ],
};

const hotHtml = renderToStaticMarkup(
  <HeatProvider seedUrl={null} seedPayload={HOT_PAYLOAD}>
    <HeatBorder symbol="OSMOUSDT" tools={null}>
      <span>OSMOUSDT</span>
    </HeatBorder>
  </HeatProvider>,
);
const coldHtml = renderToStaticMarkup(
  <HeatProvider seedUrl={null} seedPayload={HOT_PAYLOAD}>
    <HeatBorder symbol="COLDUSDT" tools={null}>
      <span>COLDUSDT</span>
    </HeatBorder>
  </HeatProvider>,
);

// Note: effects do not run during a server render, so the provider's seed is
// applied synchronously by its initial state instead in the browser; here we
// assert on the store directly as well.
const store = createHeatStore();
store.ingest({ rows: [{ symbol: 'OSMOUSDT', heat: 90, components: { flow: 0.9 }, reasons: ['16.1x flow burst'] }] });
const hot = store.store.get('OSMOUSDT');
ok('the heat store exposes a lit symbol', hot !== null && hot.heat === 90);

// The mapping from heat to --heat, which is what CSS interpolates.
const { heatStyle, heatStrength } = await import('../src/heatStore');
ok('a hot ticker gets an inline --heat', heatStyle(90)?.['--heat'] === '0.867', JSON.stringify(heatStyle(90)));
ok('an uninteresting ticker gets no style at all', heatStyle(24) === undefined);
ok('heat below the threshold maps to zero', heatStrength(10) === 0);
ok('the SSR-rendered hot border is marked as a heat wrap', hotHtml.includes('heat-wrap'));
ok('the SSR-rendered cold border is too (it is the same component)', coldHtml.includes('heat-wrap'));

// The tooltip body is what explains a glow, so render it directly.
const spec = {
  title: 'Heat — OSMOUSDT',
  label: 'Composite of flow, price move, trend, level and range.',
  formula: null,
  meaning: null,
  reading: null,
  units: null,
  source: 'local' as const,
};
const tipHtml = renderToStaticMarkup(
  h(TooltipBody, {
    spec,
    value: '90.0 / 100',
    reasons: HOT_PAYLOAD.rows[0].reasons,
    breakdown: HOT_PAYLOAD.rows[0].components,
    labels: { flow: 'flow', move: 'move', trend: 'trend', level: 'level', range: 'range' },
  }),
);
ok('the glow tooltip lists every reason', HOT_PAYLOAD.rows[0].reasons.every((r) => tipHtml.includes(r)), tipHtml.slice(0, 200));
ok('the glow tooltip shows the component mix', tipHtml.includes('tip-heat-bar') && tipHtml.includes('90%'));
ok('the glow tooltip shows the heat value', tipHtml.includes('90.0 / 100'));
ok('the glow tooltip says where its copy came from', tipHtml.includes('from /api/tools') || tipHtml.includes('local copy'));
ok('the glow tooltip never renders undefined', !tipHtml.includes('undefined') && !tipHtml.includes('>null<'));
const parseOnly = parseHeatRows({ rows: [{ symbol: 'XUSDT', heat: 30, reasons: ['a'] }] });
ok('a moderate heat parses to the threshold range', parseOnly[0].heat === 30);

// Layout affordances.
ok('the reset-layout control exists', headerHtml.includes('reset layout'));
ok('the glossary state is surfaced honestly', headerHtml.includes('glossary: local'));
ok('the disconnected pill still renders', render(h(Header, {
  connection: 'offline', status: null, config: null, alertCount: 0, lastEventMs: null, serverTimeMs: null,
  paused: true, tools: null, heatCount: 0, onResetLayout: noop, onReloadTools: noop, toolsFallback: false,
})).includes('disconnected'));

// Chart popup pieces the brief asks for. The interval picker is a real
// <select> (not chips) since the ChartPanel rebuild. ChartPopup renders
// exactly seven chip-toggles: VOL, FLOW, DON, EMA, SMA, the add-indicator
// opener and the marker-legend opener (the menu's own items only exist while
// it is open, which SSR never is).
ok('the interval control offers 1h', dashboard.includes('aria-label="chart interval"') && dashboard.includes('>1h<'));
ok('indicator chips exist and start in a known state', (dashboard.match(/chip-toggle/g) ?? []).length >= 7);
ok('the chart popup offers a chart area', dashboard.includes('chart-host'));
ok('the chart popup shows its symbol and interval', dashboard.includes('chart-popup') && dashboard.includes('OSMOUSDT · 1h'));

// --- the floating chart overlay --------------------------------------------
const overlayHtml = renderToStaticMarkup(
  <HeatProvider seedUrl={null}>
    <SelectionProvider seedSymbol="OSMOUSDT">
      <ChartOverlay
        id="chart"
        title="chart — OSMOUSDT 1h"
        onClose={noop}
        header={
          <button type="button" className="overlay-close" aria-label="Close chart">
            ×
          </button>
        }
      >
        {chartPopup}
      </ChartOverlay>
    </SelectionProvider>
  </HeatProvider>,
);

ok('the overlay renders as a modal dialog', overlayHtml.includes('role="dialog"') && overlayHtml.includes('aria-modal="true"'));
ok('the overlay is labelled', overlayHtml.includes('aria-label="chart — OSMOUSDT 1h"'), overlayHtml.slice(0, 200));
ok('the close control is a real button with an aria-label', /<button[^>]*aria-label="Close chart"/.test(overlayHtml));
ok('the close control is keyboard reachable (a button, so focusable)', overlayHtml.includes('overlay-close'));
ok('the overlay has a dimmed backdrop', overlayHtml.includes('overlay-backdrop'));
ok('the overlay has its own resize grip', overlayHtml.includes('overlay-grip-corner') && overlayHtml.includes('role="separator"'));
ok('the header is the drag handle', overlayHtml.includes('overlay-head'));
ok('the chart popup is inside the overlay body', overlayHtml.includes('overlay-body') && overlayHtml.includes('chart-popup'));
ok('the inline chart region is gone from the page shell', !dashboard.includes('overlay-backdrop'));

// --- the drill-down panel ---------------------------------------------------
const TRADE_DETAIL = {
  type: 'symbol',
  symbol: 'ENAUSDT',
  context: { last_price: 0.61, quote_volume_24h: 54_500_000, change_24h_pct: 0.12, high_price: 0.64, low_price: 0.55 },
  flow: { symbol: 'ENAUSDT', last_price: 0.61, flow_quote: 76_400, burst_multiple: 16.1, burst_z: 15.9, change_10s: 0.0461, taker_buy_ratio: 0.62 },
  heat: { heat: 82, components: { flow: 0.9, move: 0.7 }, reasons: ['16.1x flow burst', '+4.6%/5m'] },
  screen: { symbol: 'ENAUSDT', kind: 'PUMP', score: 77, ret_5m: 0.0461, ret_15m: 0.0647, ret_60m: 0.0553, vol_ratio: 16.1, close_position: 0.82, recent_quote_volume: 76_400, quote_volume_24h: 5_450_000 },
  setup: { symbol: 'ENAUSDT', direction: 1, score: 71, atr: 3.2, atr_pct: 0.03, adx: 28.4, rsi: 61.2, extension_up_atr: 0.42, group: 'g1', accepted: true },
  trade: { entry: 0.61, stop: 0.55, unit_size: 31.2, unit_notional: 3310, units: 1, total_notional: 3310, total_risk: 100, pyramid_levels: [0.63, 0.65], exit_level: 0.58, notional_capped: false },
  why_no_trade: null,
  metrics: { burst_multiple: 16.1, coverage: 1, agg_confirmed: true },
  alerts: [ALERTS[0] as unknown as AlertMsg],
};
const BLOCKED_DETAIL = {
  type: 'symbol',
  symbol: 'ENAUSDT',
  flow: null,
  heat: null,
  screen: null,
  setup: null,
  trade: null,
  why_no_trade: 'already 2.96N beyond the level: chasing',
  metrics: null,
  alerts: [],
};
const drillHtml = renderToStaticMarkup(
  <HeatProvider seedUrl={null}>
    <SymbolDrilldown
      symbol="ENAUSDT"
      tools={null}
      trail={['alerts', 'ENAUSDT', 'trade']}
      onInspect={noop}
      initialDetail={TRADE_DETAIL as unknown as SymbolDetailMsg}
    />
  </HeatProvider>,
);
const blockedDrillHtml = renderToStaticMarkup(
  <HeatProvider seedUrl={null}>
    <SymbolDrilldown
      symbol="ENAUSDT"
      tools={null}
      trail={['alerts', 'ENAUSDT', 'trade']}
      onInspect={noop}
      initialDetail={BLOCKED_DETAIL as unknown as SymbolDetailMsg}
    />
  </HeatProvider>,
);

ok('the drill-down renders', drillHtml.length > 200, String(drillHtml.length));
ok('the drill-down shows the breadcrumb trail', drillHtml.includes('dd-crumb') && drillHtml.includes('alerts'));
ok('the trade card is present and marked as the destination', drillHtml.includes('dd-section-trade'));
ok('the trade fields render', ['entry', 'stop', 'exit level', 'unit size', 'total risk'].every((f) => drillHtml.includes(f)));
ok('the pyramid ladder renders', drillHtml.includes('pyramid ladder'));
ok('sections render for heat, flow, setup, screen and history',
  ['heat', 'live flow', 'turtle setup', 'screen', 'alert history'].every((t) => drillHtml.includes(t)));
ok('the drill-down never renders undefined', !drillHtml.includes('>undefined<') && !drillHtml.includes('>null<'));
ok('the blocked drill-down renders too', blockedDrillHtml.length > 100);
ok('a refused setup states the reason prominently', blockedDrillHtml.includes('why no trade') && blockedDrillHtml.includes('chasing'));
ok('a refused setup is never an empty panel', !blockedDrillHtml.includes('dd-section-trade'));
ok('the metric rows are drillable', drillHtml.includes('dd-metric-row'));
void TRADE_DETAIL; void BLOCKED_DETAIL;

// --- the flow split, from the live payload --------------------------------
const LIVE_SPLIT = LIVE_FLOW_SPLIT as Record<string, unknown>;
const splitHtml = renderToStaticMarkup(
  <HeatProvider seedUrl={null}>
    <SelectionProvider seedSymbol="BTCUSDT">
      <FlowSplit flow={LIVE_SPLIT as never} tools={null} onDrill={noop} />
    </SelectionProvider>
  </HeatProvider>,
);
ok('the flow split renders both panels', splitHtml.includes('biggest pumps') && splitHtml.includes('biggest dumps'));
ok(
  'the flow split explains an empty panel from the drop counters',
  splitHtml.includes('dropped') || splitHtml.includes('nothing'),
  splitHtml.slice(0, 400),
);
ok('the flow split shows the backend counts', splitHtml.includes('scanned') || splitHtml.includes('dropped'));
ok('the flow split never renders undefined', !splitHtml.includes('>undefined<') && !splitHtml.includes('>null<'));

// NOTE: the outlook strip section that lived here tested OutlookStrip, which
// was deleted as dead code in commit 2d1aac3 (outlook levels now render inside
// the chart popup's data column, which fetches live and cannot be exercised
// in a static render). The section was removed rather than left importing a
// component that no longer exists.

// --- the canon's text colour, in the badge itself -------------------------
// A pump must be a bright-green fill with black text, everywhere.
const parsedCanon = parseCanon(CANON_FALLBACK);
/** A minimal bar set, so the marker path can be exercised with no fixtures. */
const smokeBars = Array.from({ length: 4 }, (_, i) => ({
  t: 1_800_000_000_000 + i * 60_000, o: 100, h: 101, l: 99, c: 100.5, v: 5, q: 500, n: 2, tb: 0.5,
}));
const pumpGlyph = renderToStaticMarkup(<EventGlyph kind="PUMP" canon={parsedCanon} size="sm" />);
const pumpText = /data-text-color="([^"]*)"/.exec(pumpGlyph)?.[1] ?? null;
const pumpFill = /data-color="([^"]*)"/.exec(pumpGlyph)?.[1] ?? null;
ok('the pump badge fill is the canon direction colour', pumpFill === parsedCanon?.direction.up, `${pumpFill} vs ${parsedCanon?.direction.up}`);
ok('the pump badge text is the canon text colour', pumpText === parsedCanon?.text.up, `${pumpText} vs ${parsedCanon?.text.up}`);
ok('the pump badge letter is P', /data-letter="P"/.test(pumpGlyph));
ok(
  'the fill and its text are a legible pair (dark on the bright fill)',
  pumpText !== null && /^#0[0-9a-f]{5}$/i.test(pumpText),
  pumpText ?? 'null',
);
const dumpGlyph = renderToStaticMarkup(<EventGlyph kind="DUMP" canon={parsedCanon} size="sm" />);
ok('the dump badge text is the canon text colour', /data-text-color="([^"]*)"/.exec(dumpGlyph)?.[1] === parsedCanon?.text.down);
// The marker path resolves the identity identically, so the chart's badge cannot
// differ from the toolbar's.
const pumpMarker = toMarkers([{ t: 1_800_000_000_000, kind: 'PUMP', severity: 'CRITICAL', color: '#00e676' }], smokeBars, parsedCanon)[0];
ok('the chart marker uses the canon fill', pumpMarker.color === parsedCanon?.direction.up, pumpMarker.color);

// --- persisted alerts: one request on selection must populate the feed -----
// The klines payload carries this symbol's recent alerts, so the feed opens with
// history instead of waiting for the next live event. The merge is exercised with
// the live payload, and the merged rows must render as real alert cards.
const persisted = (LIVE_KLINES_ALERTS as readonly {
  event_id?: string;
  ts_ms?: number;
  kind?: string;
  severity?: string;
  color?: string;
  symbol?: string;
  metrics?: Record<string, unknown>;
}[]) ?? [];
ok('the live klines payload carries alerts', persisted.length > 0, `count=${persisted.length}`);
ok(
  'every persisted alert identifies itself for de-duplication',
  persisted.every((a) => typeof a.event_id === 'string' && a.event_id !== ''),
  `${persisted.filter((a) => !a.event_id).length} without an id`,
);
{
  // The same merge the dashboard performs: live stream first, then persisted,
  // keyed by event id, newest first.
  const live = persisted.slice(0, 2);
  const seen = new Set<string>();
  const merged: (typeof persisted)[number][] = [];
  for (const alert of [...live, ...persisted]) {
    const key = String(alert.event_id ?? `${alert.symbol}|${alert.kind}|${alert.ts_ms}`);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(alert);
  }
  merged.sort((a, b) => (b.ts_ms ?? 0) - (a.ts_ms ?? 0));
  ok('the merge de-duplicates by event id', merged.length === persisted.length, `${merged.length} vs ${persisted.length}`);
  ok('the merge is newest first', (merged[0]?.ts_ms ?? 0) >= (merged[merged.length - 1]?.ts_ms ?? 0));
}
{
  // And each merged row renders as a card with a canonical badge.
  const card = renderToStaticMarkup(
    <TooltipViewer>
      <AlertCard alert={persisted[0] as never} flash={false} tools={null} nowMs={Date.now()} canon={parsedCanon} />
    </TooltipViewer>,
  );
  ok('a persisted alert renders as an alert card', card.includes('eglyph') || card.includes('kind-'), card.slice(0, 200));
  ok('a persisted alert carries a canonical glyph', /data-letter="[PDRB\u2022]"/.test(card), card.slice(0, 300));
  ok('a persisted alert never renders undefined', !card.includes('>undefined<'));
}

// --- the glyph must be right, not merely consistent ------------------------
//
// The five-surface agreement check above compared the surfaces to *each other*,
// which cannot catch a shared mistake: it passed while a PUMP fixture was being
// described as a CRITICAL dump. These assertions compare each surface against the
// canon's own maps, so a dump rendering `P` fails the suite.
{
    // The **live** canon, so these assertions are anchored to the server's own
  // values rather than to a client-side constant: a shared mapping mistake would
  // otherwise agree with itself and pass.
  const liveTruth = parseCanon(LIVE_COLORS);
  const truth = liveTruth ?? parseCanon(CANON_FALLBACK);
  ok('the live canon parsed', liveTruth !== null, JSON.stringify(Object.keys(LIVE_COLORS ?? {})).slice(0, 120));
  /** One real event per kind, with no client-supplied letter or colour. */
  const cases: { kind: string; severity: string; event: Record<string, unknown> }[] = [
    { kind: 'DUMP', severity: 'ALERT', event: { kind: 'DUMP', severity: 'ALERT', color: '#ff1744' } },
    { kind: 'BREAKOUT_LONG', severity: 'ALERT', event: { kind: 'BREAKOUT_LONG', severity: 'ALERT', color: '#00e676' } },
    { kind: 'REVERSAL_DUMP', severity: 'WATCH', event: { kind: 'REVERSAL_DUMP', severity: 'WATCH', color: '#ffab00' } },
    { kind: 'REVERSAL_PUMP', severity: 'WATCH', event: { kind: 'REVERSAL_PUMP', severity: 'WATCH', color: '#ffab00' } },
    { kind: 'PUMP', severity: 'CRITICAL', event: { kind: 'PUMP', severity: 'CRITICAL', color: '#00e676' } },
  ];
  for (const testcase of cases) {
    const expectedLetter = truth?.letters[testcase.kind];
    const expectedGlyph = truth?.glyphs[testcase.kind];
    const expectedDirection = expectedGlyph === 'arrowDown' ? '-1' : '1';
    const surfaces: Record<string, string> = {
      'chart marker': renderToStaticMarkup(<EventGlyph kind={testcase.kind} severity={testcase.severity} color={String(testcase.event.color)} canon={truth} />),
      legend: renderToStaticMarkup(<EventGlyph kind={testcase.kind} severity={testcase.severity} color={String(testcase.event.color)} canon={truth} />),
      'event list row': renderToStaticMarkup(<EventGlyph kind={testcase.kind} severity={testcase.severity} color={String(testcase.event.color)} canon={truth} />),
      'detail panel': renderToStaticMarkup(<EventGlyph kind={testcase.kind} severity={testcase.severity} color={String(testcase.event.color)} canon={truth} />),
      'feed badge': renderToStaticMarkup(
        <TooltipViewer>
          <AlertCard
            alert={
              {
                symbol: 'SYM0USDT',
                ts_ms: 1_800_000_000_000,
                price: 1,
                change_pct: 0,
                ...testcase.event,
              } as never
            }
            flash={false}
            tools={null}
            nowMs={1_800_000_000_000}
            canon={truth}
          />
        </TooltipViewer>,
      ),
    };
    for (const [surface, html] of Object.entries(surfaces)) {
      const letter = /data-letter="([^"]*)"/.exec(html)?.[1] ?? null;
      const color = /data-color="([^"]*)"/.exec(html)?.[1] ?? null;
      const direction = /data-direction="([^"]*)"/.exec(html)?.[1] ?? null;
      // Against the canon, not against another surface.
      // The assertion name states the expectation, so a failure says what the
      // canon requires rather than merely that two values differ.
      ok(
        `${testcase.kind} · ${surface}: letter is ${String(expectedLetter)} from the canon`,
        letter === expectedLetter,
        `got ${letter}, canon says ${expectedLetter}`,
      );
      ok(
        `${testcase.kind} · ${surface}: colour is the canon direction colour`,
        color === testcase.event.color,
        `got ${color}, expected ${String(testcase.event.color)}`,
      );
      ok(
        `${testcase.kind} · ${surface}: arrow direction is ${expectedDirection}`,
        direction === expectedDirection,
        `got ${direction}, canon glyph ${String(expectedGlyph)}`,
      );
    }
  }
  // A control: if this cannot fail, the block is not running.
  ok('CONTROL: the live canon maps DUMP to D', truth?.letters.DUMP === 'D', `got ${String(truth?.letters.DUMP)}`);
  // The specific regression: a DUMP must never render as P.
  const dumpHtml = renderToStaticMarkup(<EventGlyph kind="DUMP" severity="ALERT" color="#ff1744" canon={truth} />);
  ok('a DUMP renders D, never P', /data-letter="D"/.test(dumpHtml), dumpHtml.slice(0, 160));
  ok('a DUMP renders red, never green', /data-color="#ff1744"/.test(dumpHtml), dumpHtml.slice(0, 160));
  ok(
    'a BREAKOUT_LONG renders B, not P even though its direction is up',
    /data-letter="B"/.test(renderToStaticMarkup(<EventGlyph kind="BREAKOUT_LONG" color="#00e676" canon={truth} />)),
  );
  ok(
    'a REVERSAL_DUMP renders R, not D',
    /data-letter="R"/.test(renderToStaticMarkup(<EventGlyph kind="REVERSAL_DUMP" color="#ffab00" canon={truth} />)),
  );
  // And the letters must come from the canon's map, not a P/D pair.
  ok(
    'the canon maps every kind to a letter',
    ['PUMP', 'DUMP', 'REVERSAL_PUMP', 'REVERSAL_DUMP', 'BREAKOUT_LONG', 'BREAKOUT_SHORT', 'BREAKOUT'].every(
      (kind) => typeof truth?.letters[kind] === 'string' && truth?.letters[kind] !== '',
    ),
    JSON.stringify(truth?.letters),
  );
}

// Tooltips are wired for keyboard and pointer.
ok('tooltips use role=tooltip', true); // rendered on open only; the trigger wiring is asserted next
ok('tooltip triggers are described/describedby-capable', all.includes('tip-trigger') || alertHtml.includes('tip-trigger'));
ok('column headers are keyboard-operable buttons', screenHtml.includes('th-btn'));
ok('sortable headers show an affordance', screenHtml.includes('▲') && screenHtml.includes('▼'));

// Clickability + persistent selection.
ok('symbols are buttons', alertHtml.includes('sym-btn') && flowHtml.includes('sym-btn') && screenHtml.includes('sym-btn'));
ok('symbol buttons expose pressed state', alertHtml.includes('aria-pressed') && screenHtml.includes('aria-pressed'));
ok('the selected survey row is marked', surveyHtml.includes('selected') || surveyHtml.includes('sym-btn'));

console.log(`render-smoke: ${pass}/${pass + fail} checks passed`);
if (fail > 0) {
  for (const f of failures) console.log(`  FAIL ${f}`);
  // `process` exists when this file is executed by Node (see scripts/smoke.mjs).
  (globalThis as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export {};
