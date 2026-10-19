/**
 * Runtime verification for the dashboard's DOM-free logic.
 *
 * These modules are free of React and the DOM, so Node's own TypeScript support
 * can import them straight from source (see `scripts/ts-resolve.mjs` for the
 * extensionless-import hook) and exercise them for real rather than only
 * type-check them.
 *
 * Run via `scripts/verify.mjs` (or `pnpm verify`), which supplies the hooks.
 *
 * Exit code 0 = every check passed.
 */
import {
  parseBars,
  alignIndicator,
  alignIndicators,
  toMarkers,
  snapToBar,
  priceFormatFor,
  toCandles,
  toVolumes,
  defaultIndicatorState,
  INDICATOR_DEFS,
  MARK_LETTERS,
  markTitle,
  summarizeLatest,
} from '../src/chartData.ts';
import {
  resolveTooltip,
  LOCAL_TOOLS,
  LOCAL_INDICATORS,
  LOCAL_METRICS,
  hasDetail,
  indicatorAt,
} from '../src/glossary.ts';
import { computePlacement } from '../src/components/tooltipPlacement.ts';
import { visibleColumns, visibleFlowColumns, SCREEN_COLUMNS, FLOW_COLUMNS } from '../src/columns.ts';
import {
  heatStrength,
  heatStyle,
  createHeatStore,
  heatKey,
  parseHeatRows,
  HEAT_MIN,
} from '../src/heatStore.ts';
import { prettifyEnum, enumWord, kindStyle, severityStyle, detectorLabel } from '../src/types.ts';
import { resolvePalette, markColor } from '../src/palette.ts';
import { eventIdentity, parseCanon, severityColor, familyOf, CANON_FALLBACK } from '../src/colors.ts';

let pass = 0;
let fail = 0;
const failures = [];

function ok(name, condition, detail = '') {
  if (condition) {
    pass += 1;
    return;
  }
  fail += 1;
  failures.push(`${name}${detail === '' ? '' : ` — ${detail}`}`);
}

function eq(name, actual, expected) {
  ok(name, Object.is(actual, expected), `expected ${String(expected)}, got ${String(actual)}`);
}

function near(name, actual, expected, epsilon = 1e-6) {
  ok(
    name,
    typeof actual === 'number' && Math.abs(actual - expected) <= epsilon,
    `expected ~${expected}, got ${String(actual)}`,
  );
}

/* ============================== API.md alignment rule ====================== */

const bar = (t, c = 100) => ({ t, o: c - 1, h: c + 2, l: c - 3, c, v: 10, q: 1000, n: 5, tb: 0.6 });
const bars = parseBars([bar(1_000), bar(2_000), bar(3_000), bar(4_000)]);

eq('parseBars length', bars.length, 4);
eq('parseBars keeps integer open times', bars[0].t, 1_000);
eq('high is the max of o/h/l/c', bars[0].h, 102);
eq('low is the min of o/h/l/c', bars[0].l, 97);

const dirty = parseBars([
  bar(1_000),
  { t: 2_000, o: null, h: 3, l: 1, c: 2, v: 1 },
  { t: 'x', o: 1, h: 2, l: 0.5, c: 1.5 },
  { t: 3_000, o: '10', h: '12', l: '9', c: '11', v: '5', q: '50', n: '2', tb: '0.4' },
]);
eq('bars with a non-finite price are dropped', dirty.length, 2, JSON.stringify(dirty));
eq('numeric strings are coerced', dirty[1].c, 11);
eq('present numeric strings survive', dirty[1].v, 5);

const dupes = parseBars([bar(1_000, 10), bar(1_000, 20), bar(2_000, 30)]);
eq('duplicate bar times are collapsed', dupes.length, 2);
eq('the later duplicate wins', dupes[0].c, 20);

const short = alignIndicator('rsi14', [null, 55, 60], bars);
eq('a short indicator is not padded', short.data.length, 2, JSON.stringify(short.data));
eq('a short indicator maps to the right bar', short.data[0].time, 2);
eq('a short indicator keeps its value', short.data[0].value, 55);

const holed = alignIndicator('ema20', [1, null, 3, null], bars);
eq('null holes are skipped', holed.data.length, 2);
ok('no zero is fabricated from null', holed.data.every((p) => p.value !== 0));

const long = alignIndicator('ema50', [1, 2, 3, 4, 5, 6, 7], bars);
eq('an over-long indicator is truncated', long.data.length, 4);

eq('a missing indicator yields nothing', alignIndicator('sma200', undefined, bars).data.length, 0);
eq('a null indicator yields nothing', alignIndicator('sma200', null, bars).data.length, 0);
eq('NaN members are skipped', alignIndicator('x', [Number.NaN, 2], bars).data.length, 1);
eq('a real zero survives', alignIndicator('x', [0], bars).data.length, 1);

const aligned = alignIndicators(['rsi14', 'adx14'], { rsi14: [null, 60, 61, 62] }, bars);
eq('alignIndicators returns one entry per key', aligned.length, 2);
eq('alignIndicators keeps the requested order', aligned[0].key, 'rsi14');
eq('an absent key yields an empty series', aligned[1].data.length, 0);

eq('indicatorAt skips a null', indicatorAt([null, 5], 0), null);
eq('indicatorAt reads by index', indicatorAt([null, 5], 1), 5);
eq('indicatorAt out of range', indicatorAt([1], 9), null);
eq('indicatorAt on a non-array', indicatorAt(null, 0), null);

/* ================================ bars to series =========================== */

const candles = toCandles(bars);
eq('candle times are seconds', candles[0].time, 1);
eq('candle count matches bars', candles.length, 4);
const volumes = toVolumes(bars);
eq('volume count matches bars', volumes.length, 4);
ok('a volume bar carries a colour', typeof volumes[0].color === 'string' && volumes[0].color.length > 0);

eq('snapToBar picks the bar at or before t', snapToBar(2_500, bars), 2);
eq('snapToBar on an exact bar', snapToBar(3_000, bars), 3);
eq('snapToBar before the window is null', snapToBar(500, bars), null);

const markers = toMarkers(
  [
    { t: 2_500, kind: 'PUMP', severity: 'ALERT', score: 70 },
    { t: 2_900, kind: 'DUMP', severity: 'CRITICAL', score: 90 },
    { t: 10_000, kind: 'PUMP', severity: 'WATCH' },
    { t: 3_500, kind: 'REVERSAL_PUMP', severity: 'ALERT', score: 40 },
  ],
  bars,
);
eq('out-of-window marks are dropped', markers.length, 4, JSON.stringify(markers.map((m) => m.text)));
// A bar may carry several letters (the server dedupes per bar *and* kind), but
// never two of the same letter.
eq('marks sharing a bar and a letter collapse', markers.filter((x) => x.time === 2).length, 2);
eq('the same letter is never duplicated on one bar',
  new Set(markers.map((m) => `${m.time}:${m.text}`)).size, markers.length);
// Colours come from the shared palette (CSS tokens), not from values re-authored
// in chartData. In Node there is no stylesheet, so the documented fallbacks apply.
const PALETTE = resolvePalette();
eq('a CRITICAL mark uses the critical severity token', markers.find((x) => x.time === 2).color, PALETTE.severity.critical);
eq('a PUMP points up', toMarkers([{ t: 2_000, letter: 'P', kind: 'PUMP' }], bars)[0].shape, 'arrowUp');
eq('a PUMP renders the letter P', toMarkers([{ t: 2_000, letter: 'P', kind: 'PUMP' }], bars)[0].text, 'P');
eq('an explicitly lettered mark keeps its letter', markers.find((x) => x.time === 3).text, 'R');
eq('a DUMP points down', toMarkers([{ t: 2_000, kind: 'DUMP' }], bars)[0].shape, 'arrowDown');
eq('a DUMP renders the letter D', toMarkers([{ t: 2_000, kind: 'DUMP' }], bars)[0].text, 'D');
eq('a reversal uses the canon reversal colour', toMarkers([{ t: 4_000, kind: 'REVERSAL_DUMP' }], bars)[0].color, PALETTE.reversal);
eq('a reversal renders the letter R', toMarkers([{ t: 4_000, kind: 'REVERSAL_DUMP' }], bars)[0].text, 'R');
eq('BREAKOUT_SHORT points down', toMarkers([{ t: 2_000, kind: 'BREAKOUT_SHORT' }], bars)[0].shape, 'arrowDown');
eq('BREAKOUT_LONG points up', toMarkers([{ t: 2_000, kind: 'BREAKOUT_LONG' }], bars)[0].shape, 'arrowUp');
eq('a breakout renders the letter B', toMarkers([{ t: 2_000, kind: 'BREAKOUT_LONG' }], bars)[0].text, 'B');
// The server's own letter and direction are authoritative when present.
eq('an explicit letter is used verbatim', toMarkers([{ t: 2_000, letter: 'R', kind: 'DUMP' }], bars)[0].text, 'R');
eq('an explicit direction overrides the kind', toMarkers([{ t: 2_000, letter: 'P', kind: 'PUMP', direction: -1 }], bars)[0].position, 'aboveBar');
eq('severity CRITICAL takes the critical token', toMarkers([{ t: 2_000, letter: 'P', kind: 'PUMP', severity: 'CRITICAL' }], bars)[0].color, PALETTE.severity.critical);
// Direction and severity are separate axes: a WATCH pump is still green, while
// its severity badge (asked for separately) is grey. Collapsing them would make a
// critical dump and a critical pump indistinguishable.
eq('a WATCH pump stays green', toMarkers([{ t: 2_000, letter: 'P', kind: 'PUMP', severity: 'WATCH' }], bars)[0].color, PALETTE.up);
eq('its severity colour is grey', severityColor('WATCH'), CANON_FALLBACK.severity.WATCH);
eq('a pump uses the canon up colour', toMarkers([{ t: 2_000, letter: 'P', kind: 'PUMP' }], bars)[0].color, PALETTE.up);
eq('a dump uses the canon down colour', toMarkers([{ t: 2_000, letter: 'D', kind: 'DUMP' }], bars)[0].color, PALETTE.down);
eq('a breakout uses the canon neutral colour', toMarkers([{ t: 2_000, letter: 'B', kind: 'BREAKOUT_LONG' }], bars)[0].color, PALETTE.breakout);
// The backend sends an explicit colour per mark and stays authoritative.
eq('a server-supplied colour wins', toMarkers([{ t: 2_000, letter: 'P', kind: 'PUMP', color: '#123456' }], bars)[0].color, '#123456');
eq('markColor prefers the server value', markColor({ serverColor: '#abcdef', severity: 'CRITICAL', kind: 'DUMP' }), '#abcdef');
eq('markColor ignores a blank server value', markColor({ serverColor: '  ', severity: 'CRITICAL', kind: 'DUMP' }), PALETTE.severity.critical);
ok('the palette exposes every token', Object.keys(PALETTE).length >= 20, Object.keys(PALETTE).join(','));
ok('the palette has no empty values', Object.entries(PALETTE).every(([k, v]) => (k === 'severity' ? true : typeof v === 'string' && v.trim() !== '')));
eq('the label is carried for the tooltip', toMarkers([{ t: 2_000, letter: 'D', label: 'Dump detected' }], bars)[0].label, 'Dump detected');
eq('the label falls back to a readable title', toMarkers([{ t: 2_000, letter: 'D' }], bars)[0].label, 'Dump detected');
eq('score and flow ride along for the tooltip', toMarkers([{ t: 2_000, letter: 'D', score: 86, flow_quote: 16600, burst_multiple: 47 }], bars)[0].score, 86);
ok('an unknown kind still gets a visible glyph',
  toMarkers([{ t: 2_000, kind: 'SQUEEZE_RELEASE' }], bars)[0].text.length === 1);
ok('marker text is never UNKNOWN', toMarkers([{ t: 2_000, kind: 'WEIRD_THING' }], bars)[0].text !== 'UNKNOWN');
ok('every marker glyph is a single character',
  toMarkers([{ t: 2_000, kind: 'WEIRD_THING' }, { t: 3_000, letter: 'R' }], bars).every((m) => m.text.length === 1));
ok('markers are time-sorted', markers.every((m, i) => i === 0 || markers[i - 1].time <= m.time));
ok('MARK_LETTERS explains all four glyphs', MARK_LETTERS.length === 4 && MARK_LETTERS.map((l) => l.letter).join('') === 'PDRB');
eq('markTitle names the letters', [markTitle('P'), markTitle('D'), markTitle('R'), markTitle('B')].join(' '), 'Pump Dump Reversal Breakout');
eq('toMarkers tolerates a non-array', toMarkers('nope', bars).length, 0);
eq('toMarkers tolerates zero bars', toMarkers([{ t: 1 }], []).length, 0);

/* ================================ price scale ============================= */

const pfBig = priceFormatFor(parseBars([bar(1, 82_490)]));
eq('BTC-style precision', pfBig.precision, 2);
eq('BTC-style minMove', pfBig.minMove, 0.01);
const pfSmall = priceFormatFor(parseBars([bar(1, 0.0477)]));
eq('sub-dollar prices use their own tick size', pfSmall.precision, 5, JSON.stringify(pfSmall));
const pfMicro = priceFormatFor(parseBars([bar(1, 0.00001234)]));
ok('micro-cap prices get more decimals', pfMicro.precision >= 8, JSON.stringify(pfMicro));
ok('minMove matches precision', Math.abs(pfSmall.minMove - 10 ** -pfSmall.precision) < 1e-12);
eq('empty bars still yield a usable format', priceFormatFor([]).precision, 2);

const latest = summarizeLatest({ trend: 'up', volume_ratio: 1.5 }, bars);
eq('latest.close comes from the last bar', latest.close, bars[3].c);
eq('latest.trend is passed through', latest.trend, 'up');
ok('trend is derived when absent', summarizeLatest(null, bars).trend !== null);

/* =============================== indicator defs =========================== */

const defs = INDICATOR_DEFS;
ok('there are eight indicator chips', defs.length === 8, String(defs.length));
const ids = defs.map((d) => d.id);
for (const required of ['donchian20', 'donchian55', 'ema20', 'ema50', 'sma200', 'atr20', 'adx14', 'rsi14']) {
  ok(`chip present: ${required}`, ids.includes(required));
}
for (const def of defs) {
  ok(`chip ${def.id} plots something`, def.series.length > 0);
  ok(`chip ${def.id} has a glossary key`, def.toolKeys.length > 0);
  ok(`chip ${def.id} has fallback copy`, typeof def.fallback.title === 'string' && def.fallback.title.length > 0);
  ok(`chip ${def.id} maps to a known pane`, ['price', 'atr', 'adx', 'rsi', 'volume'].includes(def.pane));
  for (const [series, glossary] of Object.entries(def.glossaryKey)) {
    const spec = resolveTooltip([glossary, series], null, series);
    ok(`chart series ${series} resolves via ${glossary}`, spec.source !== 'key' && hasDetail(spec), spec.source);
  }
  const chipSpec = resolveTooltip(def.toolKeys, null, def.label);
  ok(`chip ${def.id} tooltip resolves`, chipSpec.source !== 'key' && hasDetail(chipSpec), chipSpec.source);
}
const defaults = defaultIndicatorState();
ok('default state covers every chip', ids.every((id) => typeof defaults[id] === 'boolean'));

/* ================================== tooltips ============================== */

const apiTools = {
  type: 'tools',
  indicators: { atr20: { title: 'ATR from API', formula: 'f', meaning: 'm', reading: 'r', units: 'price' } },
  metrics: { burst_multiple: { title: 'Burst from API', meaning: 'm2' } },
};

const fromApi = resolveTooltip(['atr20'], apiTools, 'ATR');
eq('the API wins when present', fromApi.title, 'ATR from API');
eq('the API source is reported', fromApi.source, 'api');
ok('API detail is parsed', fromApi.formula === 'f' && fromApi.meaning === 'm' && fromApi.reading === 'r');

eq('local copy is used when the API lacks a key', resolveTooltip(['coverage'], apiTools).source, 'local');
ok('local copy has a title', resolveTooltip(['coverage'], apiTools).title.length > 0);
eq('the first resolvable key wins', resolveTooltip(['not_a_real_key', 'atr20'], apiTools).title, 'ATR from API');
eq('an unknown key falls back to the raw key', resolveTooltip(['totally_unknown_key'], null).title, 'totally_unknown_key');
eq('an unknown key reports its source', resolveTooltip(['totally_unknown_key'], null).source, 'key');
eq('an explicit fallback title is honoured', resolveTooltip(['totally_unknown_key'], null, 'My label').title, 'My label');
eq('an empty key list still yields a title', resolveTooltip([], null).title, 'no description');
eq('a null glossary falls back to local', resolveTooltip(['atr20'], null).source, 'local');
eq('a glossary missing the key falls back to local', resolveTooltip(['atr20'], { indicators: {}, metrics: {} }).source, 'local');
ok(
  'empty strings degrade rather than crash',
  resolveTooltip(['atr20'], { indicators: { atr20: { title: '' } } }).title.length > 0,
);

const UI_KEYS = [
  'donchian_channel', 'ema', 'sma', 'atr20', 'atr_pct', 'volume_median_20',
  'adx14', 'plus_di', 'minus_di', 'rsi14',
  'price', 'volume_ratio', 'trend', 'symbol', 'kind', 'score', 'severity', 'interval',
  'heat', 'layout', 'tools',
  'window_sec', 'flow_quote', 'burst_multiple', 'burst_z', 'change_10s', 'change_30s', 'change_60s',
  'sigma_multiple', 'coverage', 'trades_10s', 'trades_multiple', 'acceleration', 'range_position',
  'spread_pct', 'book_depth_usd', 'quote_volume_24h', 'latency_ms', 'penalty', 'agg_confirmed',
  'agg_buy_quote', 'agg_sell_quote', 'agg_buy_ratio', 'agg_trades', 'agg_largest_trade_usd',
  'taker_buy_ratio', 'ret_5m', 'ret_15m', 'ret_60m', 'ret_24h', 'vol_ratio', 'vol_z',
  'recent_quote_volume', 'baseline_quote_volume', 'trades_ratio', 'close_position', 'range_pct',
  'from_24h_high', 'from_24h_low', 'bars_since_extreme', 'extension_up_atr',
  'pumps', 'dumps', 'reversals', 'critical', 'universe_volume_24h', 'scans',
  'units', 'total_notional', 'total_risk', 'positions', 'rejected', 'entry', 'stop',
  'unit_notional', 'pyramid_levels', 'exit_level', 'notional_capped', 'accepted', 'group',
  'exit_long', 'exit_short', 'exit_low_10',
  'detector', 'batch', 'snapshot_ms', 'summary', 'dispatched_ms', 'reasons',
  'flow', 'speed', 'aggression', 'quality',
];
const unresolved = [];
const noDetail = [];
for (const key of UI_KEYS) {
  const spec = resolveTooltip([key], null, key);
  if (spec.source === 'key') unresolved.push(key);
  if (!hasDetail(spec)) noDetail.push(key);
}
ok('every key the UI asks about resolves to real copy', unresolved.length === 0, unresolved.join(', '));
ok('every key has more than a bare title', noDetail.length === 0, noDetail.join(', '));

for (const [key, entry] of Object.entries(LOCAL_TOOLS)) {
  ok(`local copy ${key} has a title`, typeof entry.title === 'string' && entry.title.length > 0);
  ok(`local copy ${key} has meaning or reading`, Boolean(entry.meaning ?? entry.reading));
  ok(`local copy ${key} has no nullish fields`, Object.values(entry).every((v) => v !== undefined && v !== null));
}
ok('both copy maps are populated', Object.keys(LOCAL_INDICATORS).length > 5 && Object.keys(LOCAL_METRICS).length > 20);

/* =================================== heat ================================= */

eq('heat below the threshold is invisible', heatStrength(24.9), 0);
eq('heat at the threshold maps to 0', heatStrength(25), 0);
eq('heat 100 maps to 1', heatStrength(100), 1);
near('heat 62.5 maps to 0.5', heatStrength(62.5), 0.5);
eq('zero heat is invisible', heatStrength(0), 0);
eq('negative heat is invisible', heatStrength(-10), 0);
eq('NaN heat is invisible', heatStrength(Number.NaN), 0);
eq('missing heat is invisible', heatStrength(undefined), 0);
ok('heat is monotonic', heatStrength(30) < heatStrength(60) && heatStrength(60) < heatStrength(90));
eq('no style is emitted below the threshold', heatStyle(10), undefined);
ok('a style is emitted above the threshold', heatStyle(50)?.['--heat'] !== undefined);
eq('the emitted custom property is --heat', Object.keys(heatStyle(50) ?? {})[0], '--heat');
eq('the hottest ticker maps to the top of the range', heatStyle(100)?.['--heat'], '1.000');
eq('heatKey trims and uppercases', heatKey(' ethusdt '), 'ETHUSDT');
eq('heatKey of null is empty', heatKey(null), '');

const parsedHeat = parseHeatRows({
  rows: [
    {
      symbol: 'osmousdt',
      heat: 82,
      components: { flow: 0.9, move: 0.7, trend: 0.5, bogus: 'x' },
      reasons: ['16.1x flow burst', 42, '+4.6%/5m'],
    },
    { symbol: '', heat: 10 },
    { symbol: 'BADUSDT', heat: 'not-a-number' },
    { symbol: 'OVERUSDT', heat: 500 },
    { symbol: 'NEGUSDT', heat: -20 },
    { symbol: 'NOSYM', heat: 5 },
    { nope: true },
  ],
});
eq('heat rows are parsed', parsedHeat.length, 5, JSON.stringify(parsedHeat));
eq('heat symbols are normalised', parsedHeat[0].symbol, 'OSMOUSDT');
eq('non-numeric components are dropped', Object.keys(parsedHeat[0].components).join(','), 'flow,move,trend');
eq('non-string reasons are dropped', parsedHeat[0].reasons.length, 2);
eq('a non-numeric heat is treated as 0', parsedHeat[1].heat, 0);
eq('heat is clamped to 100', parsedHeat[2].heat, 100);
eq('heat is clamped to 0', parsedHeat[3].heat, 0);
eq('a payload with no rows yields nothing', parseHeatRows({}).length, 0);
eq('a non-object payload yields nothing', parseHeatRows(null).length, 0);

const store = createHeatStore();
let notifications = 0;
const unsubscribe = store.store.subscribe('OSMOUSDT', () => {
  notifications += 1;
});
store.ingest({ rows: [{ symbol: 'OSMOUSDT', heat: 80, reasons: ['x'], components: { flow: 1 } }] });
eq('ingest makes a symbol readable', store.store.get('osmousdt')?.heat, 80);
eq('ingest notifies its subscriber', notifications, 1);
ok('a lit symbol counts as active', store.store.activeCount() === 1);
eq('an unrelated symbol reads as null', store.store.get('NOPEUSDT'), null);
eq('an empty symbol reads as null', store.store.get(''), null);

store.ingest({ rows: [{ symbol: 'OSMOUSDT', heat: 80, reasons: ['x'], components: { flow: 1 } }] });
eq('an unchanged value does not notify again', notifications, 1);

store.ingest({ rows: [{ symbol: 'OTHERUSDT', heat: 90, reasons: [], components: {} }] });
ok('an absent symbol keeps its value while it decays', store.store.get('OSMOUSDT') !== null);
const beforeDecay = store.store.get('OSMOUSDT').heat;
store.tick();
const afterOneTick = store.store.get('OSMOUSDT')?.heat ?? 0;
ok('a stale symbol eases down one step', afterOneTick < beforeDecay, `${afterOneTick} !< ${beforeDecay}`);
ok('one tick is only a small step (no snapping)', beforeDecay - afterOneTick < beforeDecay * 0.2);
ok('a lit-but-stale symbol still glows for a while', afterOneTick >= HEAT_MIN);
for (let i = 0; i < 200; i += 1) store.tick();
ok('a fully decayed symbol is dropped', store.store.get('OSMOUSDT') === null);

const store2 = createHeatStore();
store2.ingest({ rows: [{ symbol: 'AAAUSDT', heat: 70, reasons: [], components: {} }] });
store2.ingest({ nonsense: true });
store2.ingest(null);
eq('a malformed payload leaves the board intact', store2.store.get('AAAUSDT')?.heat, 70);
eq('no payload ever means no heat at all', createHeatStore().store.lastUpdateMs(), null);
ok('an empty store has nothing active', createHeatStore().store.activeCount() === 0);
unsubscribe();
const beforeUnsubscribedIngest = notifications;
store.ingest({ rows: [{ symbol: 'OSMOUSDT', heat: 95, reasons: [], components: {} }] });
for (let i = 0; i < 20; i += 1) store.tick();
eq('unsubscribe stops notifications', notifications, beforeUnsubscribedIngest);
ok('HEAT_MIN is 25 as documented', HEAT_MIN === 25);

/* ============================= tooltip placement ========================== */

const viewport = { width: 1280, height: 800 };
const trigger = { top: 400, left: 600, width: 80, height: 20 };
const tip = { width: 320, height: 180 };

let placed = computePlacement({ trigger, tip, viewport, preferred: 'bottom' });
eq('the requested side is used when it fits', placed.placement, 'bottom');
ok('no overflow on the right', placed.left + tip.width <= viewport.width);
ok('no overflow at the top', placed.top >= 0);

placed = computePlacement({
  trigger: { top: 780, left: 1200, width: 60, height: 18 },
  tip,
  viewport,
  preferred: 'bottom',
});
eq('it flips above when there is no room below', placed.placement, 'top');
ok('the flipped tooltip stays on screen (right)', placed.left + tip.width <= viewport.width, JSON.stringify(placed));
ok('the flipped tooltip stays on screen (bottom)', placed.top + tip.height <= viewport.height, JSON.stringify(placed));
ok('the flipped tooltip stays on screen (left)', placed.left >= 0, JSON.stringify(placed));

placed = computePlacement({ trigger: { top: 10, left: 10, width: 20, height: 10 }, tip, viewport, preferred: 'bottom' });
ok('the top-left corner stays inside', placed.left >= 0 && placed.top >= 0);

placed = computePlacement({ trigger: { top: 5, left: 640, width: 40, height: 10 }, tip, viewport, preferred: 'top' });
eq('it flips below when there is no room above', placed.placement, 'bottom');
ok('the flipped-down tooltip stays inside', placed.top + tip.height <= viewport.height);

placed = computePlacement({
  trigger: { top: 100, left: 100, width: 10, height: 10 },
  tip: { width: 500, height: 400 },
  viewport: { width: 360, height: 300 },
  preferred: 'bottom',
});
ok('an oversized tooltip is clamped, not off-screen', placed.left >= 8 && placed.top >= 8, JSON.stringify(placed));

placed = computePlacement({ trigger, tip, viewport, preferred: 'bottom' });
ok('the arrow stays inside the tooltip', placed.arrowLeft >= 12 && placed.arrowLeft <= tip.width - 12);
ok(
  'the arrow tracks the trigger centre',
  Math.abs(placed.arrowLeft - (trigger.left + trigger.width / 2 - placed.left)) < 0.001,
);

/* ======================== container-reactive columns ====================== */

const widths = [0, 200, 330, 400, 460, 700, 900, 1200, 1600];
let previous = -1;
for (const w of widths) {
  const cols = visibleColumns(w);
  ok(`screen columns non-empty at ${w}px`, cols.length >= 2, String(cols.length));
  ok(`screen columns never shrink at ${w}px`, cols.length >= previous, `${cols.length} < ${previous}`);
  const keys = cols.map((c) => c.key);
  ok(`symbol is always visible at ${w}px`, keys.includes('symbol'));
  ok(`score is always visible at ${w}px`, keys.includes('score'));
  previous = cols.length;
}
eq('every screen column is shown when wide', visibleColumns(2000).length, SCREEN_COLUMNS.length);

previous = -1;
for (const w of widths) {
  const cols = visibleFlowColumns(w);
  ok(`flow columns non-empty at ${w}px`, cols.length >= 2, String(cols.length));
  ok(`flow columns never shrink at ${w}px`, cols.length >= previous, `${cols.length} < ${previous}`);
  ok(`flow keeps the symbol column at ${w}px`, cols.map((c) => c.key).includes('symbol'));
  ok(`flow keeps the burst bar at ${w}px`, cols.map((c) => c.key).includes('burst'));
  previous = cols.length;
}
eq('every flow column is shown when wide', visibleFlowColumns(2000).length, FLOW_COLUMNS.length);

/* ============================ enum prettifying ============================= */

eq('BREAKOUT_LONG becomes words', prettifyEnum('BREAKOUT_LONG'), 'Breakout Long');
eq('BREAKOUT_SHORT becomes words', prettifyEnum('BREAKOUT_SHORT'), 'Breakout Short');
eq('REVERSAL_DUMP becomes words', prettifyEnum('REVERSAL_DUMP'), 'Reversal Dump');
eq('a hyphenated detector becomes words', prettifyEnum('flow-onset'), 'Flow Onset');
eq('a lowercase value is title-cased', prettifyEnum('pump'), 'Pump');
eq('an empty value gets the fallback', prettifyEnum(''), 'Unclassified');
eq('null gets the fallback', prettifyEnum(null), 'Unclassified');
eq('undefined gets the fallback', prettifyEnum(undefined), 'Unclassified');
eq('whitespace gets the fallback', prettifyEnum('   '), 'Unclassified');
eq('separators-only gets the fallback', prettifyEnum('___'), 'Unclassified');
eq('a custom fallback is honoured', prettifyEnum(null, 'Unrated'), 'Unrated');
ok('nothing ever prettifies to UNKNOWN', prettifyEnum('UNKNOWN') !== 'UNKNOWN');
ok('no value ever yields the literal string undefined', prettifyEnum(undefined) !== 'undefined');
ok('no value ever yields the literal string null', prettifyEnum(null) !== 'null');

eq('enumWord uppercases', enumWord('breakout_long'), 'BREAKOUT_LONG');
eq('enumWord tolerates hyphens', enumWord('breakout-long'), 'BREAKOUT_LONG');
eq('enumWord of null is empty', enumWord(null), '');

eq('PUMP is its own style', kindStyle('PUMP'), 'pump');
eq('DUMP is its own style', kindStyle('DUMP'), 'dump');
eq('REVERSAL_PUMP is its own style', kindStyle('REVERSAL_PUMP'), 'reversal-pump');
eq('REVERSAL_DUMP is its own style', kindStyle('REVERSAL_DUMP'), 'reversal-dump');
eq('BREAKOUT_LONG is its own style', kindStyle('BREAKOUT_LONG'), 'breakout-long');
eq('BREAKOUT_SHORT is its own style', kindStyle('BREAKOUT_SHORT'), 'breakout-short');
eq('the older bare BREAKOUT is tolerated', kindStyle('BREAKOUT'), 'breakout');
eq('a lower-case breakout still maps', kindStyle('breakout_long'), 'breakout-long');
eq('a future BREAKOUT_UP maps to long', kindStyle('BREAKOUT_UP'), 'breakout-long');
eq('a future BREAKOUT_DOWN maps to short', kindStyle('BREAKOUT_DOWN'), 'breakout-short');
eq('an unseen kind gets the neutral bucket', kindStyle('SOMETHING_NEW'), 'other');
eq('a missing kind gets the neutral bucket', kindStyle(undefined), 'other');
eq('a numeric kind does not throw', kindStyle(7), 'other');
eq('WATCH severity', severityStyle('WATCH'), 'watch');
eq('ALERT severity', severityStyle('ALERT'), 'alert');
eq('CRITICAL severity', severityStyle('CRITICAL'), 'critical');
eq('an unseen severity is neutral', severityStyle('EXTREME'), 'other');
eq('a missing severity is neutral', severityStyle(null), 'other');
eq('detectorLabel prettifies', detectorLabel('turtle-breakout'), 'Turtle Breakout');
eq('detectorLabel handles flow-onset', detectorLabel('flow-onset'), 'Flow Onset');
eq('detectorLabel handles pump-screen', detectorLabel('pump-screen'), 'Pump Screen');
eq('detectorLabel never says undefined', detectorLabel(undefined), 'Unknown detector');

/* ============================ the colour canon ============================= */

// `GET /api/colors` — the live body captured from the running backend.
const LIVE_CANON = {
  direction: { down: '#ff1744', neutral: '#40c4ff', reversal: '#ffab00', up: '#00e676' },
  severity: { ALERT: '#ffab00', CRITICAL: '#ff1744', WATCH: '#78909c' },
  letters: { BREAKOUT: 'B', BREAKOUT_LONG: 'B', BREAKOUT_SHORT: 'B', DUMP: 'D', PUMP: 'P', REVERSAL_DUMP: 'R', REVERSAL_PUMP: 'R' },
  glyphs: { BREAKOUT: 'arrowUp', BREAKOUT_LONG: 'arrowUp', BREAKOUT_SHORT: 'arrowDown', DUMP: 'arrowDown', PUMP: 'arrowUp', REVERSAL_DUMP: 'arrowDown', REVERSAL_PUMP: 'arrowUp' },
};
const canon = parseCanon(LIVE_CANON);
ok('the live canon parses', canon !== null);
eq('a missing payload falls back rather than failing', parseCanon(null), null);
eq('a partial payload is merged with the fallback', parseCanon({ direction: { up: '#111111' } }).direction.up, '#111111');
eq('an absent section keeps the fallback', parseCanon({ direction: {} }).severity.CRITICAL, CANON_FALLBACK.severity.CRITICAL);

const ident = (kind, extra = {}) => eventIdentity({ kind, canon, ...extra });
// Letters and arrows come from the server's own maps.
eq('PUMP is P', ident('PUMP').letter, 'P');
eq('DUMP is D', ident('DUMP').letter, 'D');
eq('REVERSAL_PUMP is R', ident('REVERSAL_PUMP').letter, 'R');
eq('REVERSAL_DUMP is R', ident('REVERSAL_DUMP').letter, 'R');
eq('BREAKOUT_LONG is B', ident('BREAKOUT_LONG').letter, 'B');
eq('BREAKOUT_SHORT is B', ident('BREAKOUT_SHORT').letter, 'B');
eq('PUMP points up', ident('PUMP').glyph, 'arrowUp');
eq('DUMP points down', ident('DUMP').glyph, 'arrowDown');
eq('BREAKOUT_LONG points up', ident('BREAKOUT_LONG').glyph, 'arrowUp');
eq('BREAKOUT_SHORT points down', ident('BREAKOUT_SHORT').glyph, 'arrowDown');
eq('REVERSAL_DUMP points down', ident('REVERSAL_DUMP').glyph, 'arrowDown');
eq('the glyph decides the direction', ident('DUMP').direction, -1);
// Colours are the canon's, never a client constant.
eq('PUMP uses the canon up colour', ident('PUMP').color, '#00e676');
eq('DUMP uses the canon down colour', ident('DUMP').color, '#ff1744');
eq('a reversal uses the canon reversal colour', ident('REVERSAL_PUMP').color, '#ffab00');
eq('a breakout uses the canon neutral colour', ident('BREAKOUT_LONG').color, '#40c4ff');
eq('a CRITICAL pump keeps the critical colour', ident('PUMP', { severity: 'CRITICAL' }).color, '#ff1744');
// The payload's own colour wins over any mapping.
eq('the payload colour wins for a pump', ident('PUMP', { color: '#abcdef' }).color, '#abcdef');
eq('the payload colour wins for a dump', ident('DUMP', { color: '#123456' }).color, '#123456');
eq('a blank payload colour is ignored', ident('DUMP', { color: '   ' }).color, '#ff1744');
eq('the payload letter wins', ident('PUMP', { letter: 'Z' }).letter, 'Z');
eq('the payload direction wins over the glyph', ident('PUMP', { direction: -1 }).direction, -1);
// Direction and severity are separate axes.
eq('a WATCH pump stays green', ident('PUMP', { severity: 'WATCH' }).color, '#00e676');
eq('its severity colour is grey', severityColor('WATCH', canon), '#78909c');
eq('a CRITICAL pump is its severity colour', ident('PUMP', { severity: 'CRITICAL' }).color, '#ff1744');
eq('a CRITICAL dump is the same severity colour', ident('DUMP', { severity: 'CRITICAL' }).color, '#ff1744');
eq('ALERT severity colour', severityColor('ALERT', canon), '#ffab00');
eq('an unseen severity degrades to WATCH', severityColor('EXTREME', canon), '#78909c');
eq('an unseen kind still gets a glyph', ident('SQUEEZE_RELEASE').letter.length, 1);
eq('an unseen kind is family other', ident('SQUEEZE_RELEASE').family, 'other');
eq('familyOf classifies reversals', familyOf('REVERSAL_DUMP'), 'reversal');
eq('familyOf classifies breakouts', familyOf('BREAKOUT_SHORT'), 'breakout');
// One event, one identity: the same inputs must produce the same object shape.
const a1 = ident('DUMP', { severity: 'CRITICAL', color: '#ff1744' });
const a2 = ident('DUMP', { severity: 'CRITICAL', color: '#ff1744' });
ok('identity is deterministic', JSON.stringify(a1) === JSON.stringify(a2));

const summary = fail === 0 ? `${pass}/${pass + fail} checks passed` : `${pass}/${pass + fail} checks passed, ${fail} FAILED`;
console.log(`verify: ${summary}`);
if (fail > 0) {
  for (const f of failures) console.log(`  FAIL ${f}`);
  process.exitCode = 1;
}
