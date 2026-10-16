import { useEffect, useMemo, useRef, useState } from 'react';
import {
  alignIndicators,
  parseBars,
  summarizeLatest,
  toMarkers,
} from '../../chartData';
import { type ColorCanon } from '../../colors';
import { int } from '../../format';
import { useKlines } from '../../hooks/useKlines';
import { useOutlook } from '../../hooks/useOutlook';
import type { AlertMsg, Interval, ToolsMsg } from '../../types';
import { INTERVALS } from '../../types';
import { presetAction, type ZoomPreset } from '../../zoom';
import { Tooltip } from '../Tooltip';
import { EventGlyph } from '../EventGlyph';
import { BottomPanel } from './BottomPanel';
import { DataColumn } from './DataColumn';
import { EventsColumn } from './EventsColumn';
import { OVERLAY_KEYS, PriceChart, type ChartZoomControls } from './PriceChart';

export type { ChartZoomControls };

/* ------------------------------------------------------------- marker legend */

/**
 * One sample per marker family, so the popover shows exactly the letters,
 * colours and arrows the chart draws — resolved through the canonical palette
 * rather than re-typed here. (Same table ChartPanel's legend used.)
 */
const MARK_SAMPLES: readonly { kind: string; title: string; blurb: string; toolKeys: string[] }[] = [
  { kind: 'PUMP', title: 'Pump', blurb: 'An upward onset detected on this bar.', toolKeys: ['mark_p'] },
  { kind: 'DUMP', title: 'Dump', blurb: 'A downward onset detected on this bar.', toolKeys: ['mark_d'] },
  { kind: 'REVERSAL_DUMP', title: 'Reversal', blurb: 'An exhaustion turn, either direction.', toolKeys: ['mark_r'] },
  { kind: 'BREAKOUT_LONG', title: 'Breakout', blurb: 'A channel breakout from the Turtle detector.', toolKeys: ['mark_b'] },
];

/** The overlay series keys the DATA column reads beyond the chart's own. */
const DATA_EXTRA_KEYS = ['atr20', 'atr_pct', 'adx14', 'plus_di', 'minus_di', 'rsi14'] as const;

export interface ChartPopupProps {
  symbol: string;
  interval: Interval;
  tools: ToolsMsg | null;
  /** Called when the user picks a different timeframe chip. */
  onIntervalChange: (interval: Interval) => void;
  priceTick?: { symbol: string; price: number; atMs: number } | null;
  tickerTick?: { symbol: string; price: number; quoteVol24h: number | null; atMs: number } | null;
  tickerAtMs?: number | null;
  canon?: ColorCanon | null;
  /** The zoom presets, straight from the server's plan. */
  presets: readonly ZoomPreset[];
  /** Apply a preset (pure zoom, or an interval switch — the caller decides). */
  onZoomPreset: (preset: ZoomPreset) => void;
  /** Restore the default view — including the default interval. */
  onResetZoom: () => void;
  activePreset: string | null;
  presetPending: boolean;
  /**
   * Publishes the chart's zoom controls upward — the preset buttons live in
   * this header, but only the chart can move the time scale.
   */
  onZoomControls?: (controls: ChartZoomControls | null) => void;
  /** The persisted alerts that arrived with the bars, for the live feed. */
  onPersistedAlerts?: (alerts: readonly AlertMsg[]) => void;
  /** Dismiss the popup. */
  onClose: () => void;
}

/**
 * The chart pop-out, rebuilt from scratch: one header row, ONE candle chart
 * taking all growth, an 8px divider, and a resizable two-column bottom panel
 * (events + data rows). No pane allocator, no duplicated controls.
 *
 * Rendered as a child of ChartOverlay, exactly as ChartPanel was.
 */
export function ChartPopup({
  symbol,
  interval,
  tools,
  onIntervalChange,
  priceTick = null,
  tickerTick = null,
  canon = null,
  presets,
  onZoomPreset,
  onResetZoom,
  activePreset,
  presetPending,
  onZoomControls,
  onPersistedAlerts,
  onClose,
}: ChartPopupProps) {
  const klines = useKlines(symbol, interval, { active: true });
  const { data, live } = klines;
  const outlook = useOutlook(symbol);

  const bars = useMemo(() => parseBars(data?.bars), [data]);

  /**
   * Marks from the history payload plus any the fast poll has delivered since,
   * merged by `time:letter` — newest wins — and never appended to a stale set.
   */
  const marks = useMemo<unknown[]>(() => {
    const merged = new Map<string, unknown>();
    const add = (raw: unknown): void => {
      if (typeof raw !== 'object' || raw === null) return;
      const mark = raw as { t?: unknown; letter?: unknown; kind?: unknown };
      const key = `${String(mark.t ?? '')}:${String(mark.letter ?? mark.kind ?? '')}`;
      merged.set(key, raw);
    };
    for (const mark of Array.isArray(data?.marks) ? data.marks : []) add(mark);
    for (const mark of live?.marks ?? []) add(mark);
    return [...merged.values()];
  }, [data, live]);

  /** Every mark for this symbol as a drawable marker, oldest first. */
  const markerList = useMemo(() => toMarkers(marks, bars, canon), [marks, bars, canon]);
  /** Newest first, for the event list and the collapsed badge. */
  const chartEvents = useMemo(() => markerList.slice().reverse(), [markerList]);
  const latest = useMemo(() => summarizeLatest(data?.latest, bars), [data, bars]);
  const overlaySeries = useMemo(
    () => alignIndicators([...OVERLAY_KEYS, ...DATA_EXTRA_KEYS], data?.indicators, bars),
    [data, bars],
  );

  /* -- chip state: exactly VOL / FLOW / DON / EMA ----------------------------- */
  const [showVol, setShowVol] = useState(true);
  const [showDon, setShowDon] = useState(true);
  const [showEma, setShowEma] = useState(true);

  /* -- the shared event selection (markers + list) ---------------------------- */
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  useEffect(() => {
    // A new symbol or interval starts with nothing selected.
    setSelectedEvent(null);
  }, [symbol, interval]);

  /* -- the marker legend popover ---------------------------------------------- */
  const [legendOpen, setLegendOpen] = useState(false);
  const legendRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!legendOpen) return;
    const onDown = (event: MouseEvent): void => {
      if (legendRef.current !== null && event.target instanceof Node && !legendRef.current.contains(event.target)) {
        setLegendOpen(false);
      }
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [legendOpen]);

  /* -- persisted alerts ride up to the feed ------------------------------------ */
  const persistedSigRef = useRef('');
  useEffect(() => {
    const persisted = Array.isArray(data?.alerts) ? data.alerts : [];
    const signature = `${persisted.length}|${persisted.map((a) => String(a?.event_id ?? a?.ts_ms ?? '')).join(',')}`;
    if (signature === persistedSigRef.current) return;
    persistedSigRef.current = signature;
    onPersistedAlerts?.(persisted);
  }, [data, onPersistedAlerts]);

  const eventCount = chartEvents.length;

  return (
    <div className="chart-popup">
      {/* ---------------------------------------------------------- header -- */}
      <div className="cp-header">
        <span className="cp-title num" title={`${symbol} · ${interval}`}>
          {symbol} · {interval}
        </span>

        <div className="interval-chips" role="group" aria-label="chart interval">
          {INTERVALS.map((iv) => (
            <Tooltip
              key={iv}
              tone="chip"
              keys={['interval']}
              tools={tools}
              fallbackTitle={`Interval ${iv}`}
              label={`Fetch klines at the ${iv} interval for the selected symbol.`}
            >
              <button
                type="button"
                className={`chip chip-toggle${interval === iv ? ' on' : ''}`}
                aria-pressed={interval === iv}
                onClick={() => onIntervalChange(iv)}
              >
                {iv}
              </button>
            </Tooltip>
          ))}
        </div>

        <div className="cp-ind-chips" role="group" aria-label="chart overlays">
          <Tooltip
            tone="chip"
            keys={['volume_median_20', 'volume_ratio']}
            tools={tools}
            fallbackTitle="Volume histogram"
            label="Per-bar base volume, coloured by the candle's direction."
            value={showVol ? 'shown' : 'hidden'}
          >
            <button
              type="button"
              className={`chip chip-toggle${showVol ? ' on' : ''}`}
              aria-pressed={showVol}
              onClick={() => setShowVol((v) => !v)}
            >
              VOL
            </button>
          </Tooltip>
          {/* FLOW is reserved for increment 3: rendered inert on purpose. */}
          <button type="button" className="chip chip-toggle" disabled title="coming">
            FLOW
          </button>
          <Tooltip
            tone="chip"
            keys={['donchian_channel']}
            tools={tools}
            fallbackTitle="Donchian 20/55"
            label="20-bar (dashed) and 55-bar (dotted) high/low channels — the Turtles' breakout channels."
            value={showDon ? 'shown' : 'hidden'}
          >
            <button
              type="button"
              className={`chip chip-toggle${showDon ? ' on' : ''}`}
              aria-pressed={showDon}
              onClick={() => setShowDon((v) => !v)}
            >
              DON
            </button>
          </Tooltip>
          <Tooltip
            tone="chip"
            keys={['ema']}
            tools={tools}
            fallbackTitle="EMA 20/50"
            label="20-bar (solid) and 50-bar exponential moving averages — fast and medium trend."
            value={showEma ? 'shown' : 'hidden'}
          >
            <button
              type="button"
              className={`chip chip-toggle${showEma ? ' on' : ''}`}
              aria-pressed={showEma}
              onClick={() => setShowEma((v) => !v)}
            >
              EMA
            </button>
          </Tooltip>
        </div>

        {/* The marker legend: a "?" button opening the glyph key popover. */}
        <div className="cp-legend" ref={legendRef}>
          <button
            type="button"
            className={`chip chip-toggle${legendOpen ? ' on' : ''}`}
            aria-expanded={legendOpen}
            aria-label="marker legend"
            onClick={() => setLegendOpen((v) => !v)}
          >
            ?
          </button>
          {legendOpen && (
            <div className="cp-legend-pop" role="group" aria-label="marker glyph legend">
              {MARK_SAMPLES.map((sample) => (
                <div key={sample.kind} className="cp-legend-row">
                  <EventGlyph kind={sample.kind} canon={canon} size="sm" />
                  <span className="cp-legend-title">{sample.title}</span>
                  <span className="cp-legend-blurb">{sample.blurb}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Zoom presets, straight from the server's plan. */}
        <div className="cp-zoom" role="group" aria-label="zoom presets">
          {presets.map((preset) => {
            const action = presetAction(preset, interval);
            const needsSwitch = action === 'switch';
            return (
              <Tooltip
                key={preset.preset}
                keys={['zoom']}
                tools={tools}
                fallbackTitle={`Zoom · ${preset.preset}`}
                label={
                  preset.mode === 'fit'
                    ? 'Fit the whole loaded history.'
                    : needsSwitch
                      ? `Show the newest ${preset.preset} (${int(preset.bars ?? 0)} bars of ${preset.interval} candles). It switches the chart to ${preset.interval} candles, which loads new data.`
                      : `Show the newest ${preset.preset} — ${int(preset.bars ?? 0)} bars already in memory, so this is a pure zoom.`
                }
                value={activePreset === preset.preset ? 'active' : null}
              >
                <button
                  type="button"
                  className={`chip chip-toggle${activePreset === preset.preset ? ' on' : ''}${needsSwitch ? ' chip-switch' : ''}`}
                  aria-pressed={activePreset === preset.preset}
                  disabled={presetPending}
                  onClick={() => onZoomPreset(preset)}
                >
                  {preset.preset}
                </button>
              </Tooltip>
            );
          })}
          <Tooltip
            keys={['zoom']}
            tools={tools}
            fallbackTitle="Reset"
            label="Restore the view the chart opens with — the default interval and the full loaded history."
          >
            <button type="button" className="btn" onClick={onResetZoom}>
              reset
            </button>
          </Tooltip>
          {presetPending && (
            <span className="events-limited" title="switching interval to satisfy the preset">
              loading…
            </span>
          )}
        </div>

        <Tooltip
          keys={['close']}
          tools={tools}
          fallbackTitle="Close chart"
          label="Dismisses the chart and clears the selected symbol. Escape does the same, as does clicking the backdrop."
          tone="stat"
        >
          <button type="button" className="overlay-close" aria-label="Close chart" onClick={onClose}>
            ×
          </button>
        </Tooltip>
      </div>

      {/* ------------------------------------------------------- chart region -- */}
      <div className="cp-chart-region">
        <PriceChart
          symbol={symbol}
          interval={interval}
          canon={canon}
          priceTick={priceTick}
          tickerTick={tickerTick}
          klines={klines}
          bars={bars}
          markerList={markerList}
          overlaySeries={overlaySeries}
          showVol={showVol}
          showDon={showDon}
          showEma={showEma}
          selectedEvent={selectedEvent}
          onSelectEvent={setSelectedEvent}
          onZoomControls={onZoomControls}
        />
      </div>

      {/* -------------------------------------------- divider + bottom panel -- */}
      <BottomPanel eventCount={eventCount}>
        <div className="cp-columns">
          <div className="cp-col-events">
            <EventsColumn
              events={chartEvents}
              selected={selectedEvent}
              onSelect={setSelectedEvent}
              tools={tools}
              canon={canon}
            />
          </div>
          <div className="cp-col-data">
            <DataColumn latest={latest} overlaySeries={overlaySeries} outlook={outlook} tools={tools} />
          </div>
        </div>
      </BottomPanel>
    </div>
  );
}
