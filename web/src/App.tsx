import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MAX_ALERTS, useEventStream } from './api';
import { AlertFeed } from './components/AlertFeed';
import { ChartPopup } from './components/chart/ChartPopup';
import type { ChartZoomControls } from './components/chart/ChartPopup';
import { ChartOverlay } from './components/ChartOverlay';
import { SplitPane } from './components/Panel';
import { FlowLeaderboard } from './components/FlowLeaderboard';
import { PumpRadar } from './components/PumpRadar';
import { BusPanel } from './components/BusPanel';
import { FlowSplit } from './components/FlowSplit';
import { Header } from './components/Header';
import { ScreenTable } from './components/ScreenTable';
import { SettingsPanel } from './components/SettingsPanel';
import { StatStrip } from './components/StatStrip';
import { SurveyPanel } from './components/SurveyPanel';
import { TooltipViewer } from './components/TooltipViewer';
import { defaultIndicatorState } from './chartData';
import { SymbolDrilldown } from './components/SymbolDrilldown';
import { TurtlePanel } from './components/TurtlePanel';
import { MoversPanel } from './components/MoversPanel';
import { ModelsPanel } from './components/ModelsPanel';
import { toggleFilter, type FeedFilter, type FilterKey } from './filters';
import { HeatProvider, useHeatContext } from './heatContext';
import { bumpLayoutGeneration, resetLayout, saveChipState } from './layout';
import { SelectionProvider, readUrlState, useSelection, writeUrlState } from './selection';
import { Tooltip } from './components/Tooltip';
import { useTicker } from './hooks/useTicker';
import { TICKER_SEED_URL } from './hooks/useKlines';
import { useColorCanon, useTools, useZoomPlan } from './useTools';
import { presetAction, type ZoomPreset } from './zoom';
import { isInterval, type AlertMsg, type FlowRow, type ScreenRow, type Setup, type SetupPlan, type SymbolDetailMsg, type ScreenMsg, type SurveyMsg } from './types';
import './styles.css';

/** First usable symbol, so the chart has something to show before any click. */
function firstSymbol(screen: ScreenMsg | null, survey: SurveyMsg | null): string | null {
  const fromScreen = screen?.rows?.[0]?.symbol;
  if (typeof fromScreen === 'string' && fromScreen !== '') return fromScreen;
  const fromSurvey = survey?.setups?.[0]?.symbol;
  if (typeof fromSurvey === 'string' && fromSurvey !== '') return fromSurvey;
  return null;
}

/**
 * The dashboard shell.
 *
 * `{ symbol, interval }` lives in `SelectionProvider`, so every symbol rendered
 * anywhere (alerts, leaderboard, screen, survey, chart header) loads the same
 * chart. Heat lives in `HeatProvider`: it is pushed for the whole universe every
 * ~2s, so it is kept in a store that rows subscribe to individually instead of
 * in state, which would re-render the whole feed on every tick.
 */
function Dashboard({ onSeed }: { onSeed: (symbol: string) => void }) {
  const heat = useHeatContext();
  const selection = useSelection();
  const [activeHeat, setActiveHeat] = useState(0);
  // Top-level surface switch: the full overview, or the dedicated pump/dump
  // dashboard (owner ruling 2026-10-21: pump and dump is a first-class view).
  // Hash-addressable so the view is linkable and screenshot-able: #pumpdump.
  const [view, setViewState] = useState<'main' | 'pumpdump' | 'bus' | 'settings'>(() => {
    if (typeof window === 'undefined') return 'main';
    const h = window.location.hash;
    return h === '#pumpdump' ? 'pumpdump' : h === '#bus' ? 'bus' : h === '#settings' ? 'settings' : 'main';
  });
  const setView = useCallback((v: 'main' | 'pumpdump' | 'bus' | 'settings') => {
    setViewState(v);
    if (typeof window !== 'undefined') {
      window.location.hash = v === 'main' ? '' : v;
    }
  }, []);

  /**
   * The drill-down subject and the feed filter.
   *
   * Both are initialised from the URL hash so a shared link (or a reload) lands
   * on the same view, and both are written back whenever they change.
   */
  const initial = useRef(readUrlState());
  const [drillSymbol, setDrillSymbol] = useState<string | null>(initial.current.symbol);
  const [drillView, setDrillView] = useState<string>(initial.current.view ?? 'trade');
  /**
   * What the caller already knew about the symbol it drilled into.
   *
   * Seeding the panel means the trade card (or the reason there is none) is
   * populated on the first frame instead of after a round trip.
   */
  const [drillSeed, setDrillSeed] = useState<Partial<SymbolDetailMsg> | null>(null);

  /**
   * The selected chart event and the chart's zoom controls.
   *
   * Both now live with the popup itself (the event list is inside it); only
   * the zoom controls are published up, because the preset buttons sit in the
   * popup header while App owns the interval-switch logic they can trigger.
   */
  const [zoomControls, setZoomControls] = useState<ChartZoomControls | null>(null);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [presetPending, setPresetPending] = useState(false);
  /** The default interval a "reset" returns to. */
  const defaultInterval = useRef(selection.interval);
  /** A preset waiting for its interval to load; applied once the bars arrive. */
  const pendingPreset = useRef<ZoomPreset | null>(null);
  /** The preset waiting for the series to be repainted before it is applied. */
  const appliedFor = useRef<ZoomPreset | null>(null);

  /**
   * Apply a preset — from the series in memory when possible, by switching
   * interval when the plan says otherwise.
   *
   * A 5-second window cannot be drawn from one-minute candles, so the fine
   * presets name `1s` candles and selecting one *is* a fetch. Presenting that as
   * a free zoom (or, worse, silently showing a 5s window of 1m candles) is the
   * bug this replaces.
   */
  const handleZoomPreset = useCallback(
    (preset: ZoomPreset) => {
      const action = presetAction(preset, selection.interval);
      if (action === 'fit') {
        zoomControlsRef.current?.reset();
        setActivePreset(preset.preset);
        setPresetPending(false);
        return;
      }
      if (action === 'zoom') {
        zoomControlsRef.current?.applyPreset(preset);
        setActivePreset(preset.preset);
        setPresetPending(false);
        return;
      }
      // Interval switch: remember the preset, change the interval, and apply it
      // once that interval's bars are loaded (the effect below).
      pendingPreset.current = preset;
      setActivePreset(preset.preset);
      setPresetPending(true);
      if (preset.interval !== undefined && isInterval(preset.interval)) {
        selection.selectInterval(preset.interval);
      }
    },
    [selection],
  );

  /**
   * Finish a pending preset once its interval has loaded.
   *
   * Keyed on the interval rather than on the bars, so it runs exactly when the
   * switch completes; `applyPreset` then acts on the freshly loaded series.
   */
  useEffect(() => {
    const preset = pendingPreset.current;
    if (preset === null) return;
    if (preset.interval === undefined || preset.interval !== selection.interval) return;
    pendingPreset.current = null;

    // Wait for the newly loaded series to be *painted* — this effect runs before
    // the chart repaints, so applying immediately would set the range against the
    // previous interval's bars and leave the chart showing everything.
    appliedFor.current = null;
    appliedFor.current = preset;
  }, [selection.interval]);

  /**
   * Apply the queued preset once the chart reports its series are on screen.
   *
   * `readySeq` changes on exactly the commit that painted the new payload, which
   * is the ordering guarantee a timeout can only approximate.
   */
  useEffect(() => {
    const preset = appliedFor.current;
    if (preset === null) return;
    appliedFor.current = null;
    zoomControlsRef.current?.applyPreset(preset);
    setPresetPending(false);
  }, [zoomControls?.readySeq]);

  /**
   * Reset returns to the view the chart *opens* with: the default interval and
   * the full history. If a preset switched the interval, reset switches it back —
   * otherwise "reset" would leave the user somewhere they have never been.
   */
  const handleResetZoom = useCallback(() => {
    pendingPreset.current = null;
    setPresetPending(false);
    if (selection.interval !== defaultInterval.current) {
      selection.selectInterval(defaultInterval.current);
      // The interval change reloads the data; the new series opens fitted.
    } else {
      zoomControlsRef.current?.reset();
    }
    setActivePreset(null);
  }, [selection]);

  const [filter, setFilter] = useState<FeedFilter>(() => ({
    severity: initial.current.severity,
    detector: initial.current.detector,
    kind: initial.current.kind,
  }));

  const drillInto = useCallback(
    (
      symbol: string,
      seed?: {
        screen?: ScreenRow;
        setup?: Setup;
        trade?: SetupPlan | null;
        flow?: FlowRow;
        alerts?: AlertMsg[];
      },
    ) => {
      const clean = symbol.trim().toUpperCase();
      if (clean === '') return;
      setDrillSeed(
        seed === undefined
          ? null
          : {
              type: 'symbol',
              symbol: clean,
              screen: seed.screen ?? null,
              setup: seed.setup ?? null,
              trade: seed.trade ?? null,
              flow: seed.flow ?? null,
              alerts: seed.alerts ?? null,
              // A seeded setup with no plan and no reason still explains itself.
              why_no_trade:
                seed.setup !== undefined && seed.setup !== null && seed.trade == null
                  ? (seed.setup.reject_reason ?? 'no trade plan in this survey pass')
                  : null,
            },
      );
      setDrillSymbol(clean);
      setDrillView('trade');
      // Drilling also charts the symbol, so the overlay and the panel agree.
      selection.selectSymbol(clean);
    },
    [selection],
  );

  const handleFilterToggle = useCallback((key: FilterKey, value: string) => {
    setFilter((prev) => toggleFilter(prev, key, value));
  }, []);

  /**
   * The per-tick price push (~4 Hz) for the charted symbol.
   *
   * `ticker.ingest` is handed to the SSE handler, so frames never enter React
   * state; `ticker.tick` is the charted symbol's latest value, which the chart
   * applies to the forming candle.
   */
  const ticker = useTicker(selection.symbol);

  const {
    connection,
    serverTimeMs,
    lastEventMs,
    config,
    status,
    flow,
    screen,
    survey,
    alerts,
    alertStats,
    flashIds,
    paused,
    pendingAlerts,
    setPaused,
    refreshScreen,
    refreshSurvey,
    clearAlerts,
  } = useEventStream({
    onHeat: (payload) => heat?.ingest(payload),
    onTicker: ticker.ingest,
  });

  const { tools, fallback: toolsFallback, reload: reloadTools } = useTools();
  /**
   * The canonical colour + glyph language, fetched once from `/api/colors` and
   * published as CSS custom properties. Every surface below draws from it, so the
   * chart marker, the legend, the event row, the detail panel and the feed badge
   * render one event one way.
   */
  const canon = useColorCanon();
  /** The zoom presets, straight from the server's plan. */
  const zoomPlan = useZoomPlan();

  // A one-shot seed so the first candle update does not wait for a frame. The
  // SSE `ticker` channel remains the primary source after this.
  useEffect(() => {
    if (selection.symbol === null) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const res = await fetch(`${TICKER_SEED_URL}?symbol=${encodeURIComponent(selection.symbol as string)}`, {
          headers: { accept: 'application/json' },
          signal: controller.signal,
        });
        if (!res.ok) return;
        if (!(res.headers.get('content-type') ?? '').toLowerCase().includes('json')) return;
        ticker.ingest(await res.json());
      } catch {
        // The push will arrive shortly; a failed seed is not an error.
      }
    })();
    return () => controller.abort();
    // `ticker.ingest` is stable, and re-seeding is only needed on symbol change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.symbol]);
  const [layoutNonce, setLayoutNonce] = useState(0);

  const togglePause = useCallback(() => setPaused(!paused), [paused, setPaused]);

  // `status.alerts` is the backend's own counter; the buffer tally is the
  // fallback before the first status frame arrives.
  /**
   * The alerts that arrived inside the klines payload when the charted symbol was
   * selected.
   *
   * They are what makes the feed open already populated — the live stream only
   * carries events from the moment this page connected, so without these a symbol
   * with recent history looks untouched. Held in a ref and mirrored into state so
   * the merge below does not depend on the payload's identity.
   */
  const [persistedAlerts, setPersistedAlerts] = useState<readonly AlertMsg[]>([]);
  const handlePersistedAlerts = useCallback((next: readonly AlertMsg[]) => {
    setPersistedAlerts(next);
  }, []);

  /** Persisted history plus the live stream, newest first, de-duplicated. */
  const feedAlerts = useMemo<AlertMsg[]>(() => {
    const seen = new Set<string>();
    const out: AlertMsg[] = [];
    for (const alert of [...alerts, ...persistedAlerts]) {
      const key =
        typeof alert?.event_id === 'string' && alert.event_id !== ''
          ? alert.event_id
          : `${String(alert?.symbol ?? '')}|${String(alert?.kind ?? '')}|${String(alert?.ts_ms ?? '')}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(alert);
    }
    // Newest first, which is the order the feed expects.
    out.sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0));
    return out.slice(0, MAX_ALERTS);
  }, [alerts, persistedAlerts]);

  const alertCount = status?.alerts ?? alertStats.total;

  // Offer a symbol for adoption once the slow-cadence panels have data. The
  // provider ignores it if the user has already clicked something.
  const candidate = firstSymbol(screen, survey);
  useEffect(() => {
    if (candidate !== null) onSeed(candidate);
  }, [candidate, onSeed]);

  /**
   * The newest SSE price for the charted symbol.
   *
   * The `flow` leaderboard already arrives every second, so re-using it gives the
   * forming candle sub-second movement at no extra request cost. Only the
   * charted symbol's row is forwarded, and only while a chart is open (the
   * overlay is unmounted otherwise), so an idle dashboard pays nothing.
   */
  const priceTick = useMemo(() => {
    if (selection.symbol === null) return null;
    // The ticker is the primary path; the once-a-second flow leaderboard is the
    // fallback when the ticker channel has not delivered for this symbol.
    if (ticker.tick !== null && ticker.tick.symbol === selection.symbol) {
      return { symbol: ticker.tick.symbol, price: ticker.tick.price, atMs: ticker.tick.atMs };
    }
    const rows = Array.isArray(flow?.rows) ? flow.rows : [];
    for (const row of rows) {
      if (typeof row.symbol !== 'string' || row.symbol !== selection.symbol) continue;
      const price = typeof row.last_price === 'number' && Number.isFinite(row.last_price) ? row.last_price : null;
      if (price === null) return null;
      const atMs = typeof row.updated_ms === 'number' ? row.updated_ms : typeof flow?.ts_ms === 'number' ? flow.ts_ms : Date.now();
      return { symbol: row.symbol, price, atMs };
    }
    return null;
  }, [flow, selection.symbol, ticker.tick]);

  // Reflect the drillable state in the URL. `replaceState` keeps the back
  // button useful instead of adding one entry per click.
  useEffect(() => {
    writeUrlState({
      symbol: drillSymbol,
      interval: selection.interval,
      view: drillSymbol === null ? null : drillView,
      severity: filter.severity,
      detector: filter.detector,
      kind: filter.kind,
    });
  }, [drillSymbol, drillView, filter, selection.interval]);

  // Keep a ref so the zoom callbacks stay stable while the controls come and go
  // with the chart's mount state.
  const zoomControlsRef = useRef<ChartZoomControls | null>(null);
  zoomControlsRef.current = zoomControls;

  useEffect(() => {
    // A new symbol or interval starts from the default view: reopening the
    // chart must not restore an old zoom.
    setActivePreset(null);
    setPresetPending(false);
    pendingPreset.current = null;
  }, [selection.symbol, selection.interval]);

  const resetEverything = useCallback(() => {
    resetLayout();
    saveChipState(defaultIndicatorState());
    bumpLayoutGeneration();
    setLayoutNonce((n) => n + 1);
  }, []);

  // The legend's count is coarse and low-frequency: polling a counter every 2s
  // is far cheaper than re-rendering rows on every heat payload.
  useEffect(() => {
    const id = window.setInterval(() => {
      const next = heat?.store.activeCount() ?? 0;
      setActiveHeat((prev) => (prev === next ? prev : next));
    }, 2000);
    return () => window.clearInterval(id);
  }, [heat]);

  return (
    <div className="app" data-layout-nonce={layoutNonce}>
      <TooltipViewer>
        <Header
          connection={connection}
          status={status}
          config={config}
          alertCount={alertCount}
          lastEventMs={lastEventMs}
          serverTimeMs={serverTimeMs}
          paused={paused}
          tools={tools}
          heatCount={activeHeat}
          onResetLayout={resetEverything}
          onReloadTools={reloadTools}
          toolsFallback={toolsFallback}
        />

        <StatStrip stats={alertStats} status={status} tools={tools} />

        <nav className="view-bar" aria-label="dashboard view">
          <button
            type="button"
            className={`view-btn${view === 'main' ? ' active' : ''}`}
            aria-pressed={view === 'main'}
            onClick={() => setView('main')}
          >
            overview
          </button>
          <button
            type="button"
            className={`view-btn${view === 'pumpdump' ? ' active' : ''}`}
            aria-pressed={view === 'pumpdump'}
            onClick={() => setView('pumpdump')}
          >
            pump &amp; dump
          </button>
          <button
            type="button"
            className={`view-btn${view === 'bus' ? ' active' : ''}`}
            aria-pressed={view === 'bus'}
            onClick={() => setView('bus')}
          >
            message bus
          </button>
          <button
            type="button"
            className={`view-btn${view === 'settings' ? ' active' : ''}`}
            aria-pressed={view === 'settings'}
            onClick={() => setView('settings')}
          >
            settings
          </button>
        </nav>

        {view === 'settings' ? (
          <div className="settings-view">
            <SettingsPanel />
          </div>
        ) : view === 'bus' ? (
          <div className="bus-grid">
            <BusPanel status={status} />
          </div>
        ) : view === 'pumpdump' ? (
          <div className="pd-grid">
            <AlertFeed
              alerts={feedAlerts}
              flashIds={flashIds}
              paused={paused}
              pending={pendingAlerts}
              onTogglePause={togglePause}
              onClear={clearAlerts}
              maxAlerts={MAX_ALERTS}
              tools={tools}
              canon={canon}
              filter={filter}
              onFilterChange={setFilter}
              onFilterToggle={handleFilterToggle}
              onDrill={drillInto}
            />
            <div className="pd-side">
              <FlowLeaderboard flow={flow} tools={tools} />
              <FlowSplit flow={flow} tools={tools} onDrill={drillInto} />
            </div>
            <PumpRadar flow={flow} />
          </div>
        ) : (
        <>
        <div className="main-grid">
          <SplitPane
            id="main.columns"
            side="first"
            defaultSize={620}
            min={280}
            max={1200}
            firstClassName="col-alerts"
            secondClassName="col-flow"
            first={
              <AlertFeed
                alerts={feedAlerts}
                flashIds={flashIds}
                paused={paused}
                pending={pendingAlerts}
                onTogglePause={togglePause}
                onClear={clearAlerts}
                maxAlerts={MAX_ALERTS}
                tools={tools}
                canon={canon}
                filter={filter}
                onFilterChange={setFilter}
                onFilterToggle={handleFilterToggle}
                onDrill={drillInto}
              />
            }
            second={
              <div className="flow-column">
                <FlowLeaderboard flow={flow} tools={tools} />
                {/* The two-panel split: the strongest bursts up and down, with an
                    empty state built from the backend's own drop counters. */}
                <FlowSplit flow={flow} tools={tools} onDrill={drillInto} />
              </div>
            }
          />
        </div>

        <ScreenTable
          key={`screen-${layoutNonce}`}
          screen={screen}
          onRefresh={refreshScreen}
          tools={tools}
          onDrill={drillInto}
        />

        <SurveyPanel survey={survey} onRefresh={refreshSurvey} tools={tools} onDrill={drillInto} />

        <MoversPanel tools={tools} />

        <ModelsPanel tools={tools} />

        <TurtlePanel tools={tools} />
        </>
        )}

        {/*
          The chart is a floating overlay, not a grid cell: opening it must not
          reflow the feed, the leaderboard, the table or the survey behind it.
          It is only mounted while a symbol is selected, so closing it unmounts
          (and disposes) the lightweight-charts instance, and picking a symbol
          again builds a fresh one.
        */}
        {selection.symbol !== null && (
          <ChartOverlay
            id="chart"
            title={`chart — ${selection.symbol} ${selection.interval}`}
            onClose={selection.clearSymbol}
          >
            <ChartPopup
              symbol={selection.symbol}
              interval={selection.interval}
              tools={tools}
              onIntervalChange={selection.selectInterval}
              priceTick={priceTick}
              tickerTick={ticker.tick}
              tickerAtMs={ticker.lastAtMs}
              canon={canon}
              presets={zoomPlan.presets}
              onZoomPreset={handleZoomPreset}
              onResetZoom={handleResetZoom}
              activePreset={activePreset}
              presetPending={presetPending}
              onZoomControls={setZoomControls}
              onPersistedAlerts={handlePersistedAlerts}
              onOpenDrill={() => drillInto(selection.symbol ?? '')}
              onClose={selection.clearSymbol}
            />
          </ChartOverlay>
        )}

        {/*
          The drill-down shares the pop-out shell with the chart: same resizable,
          draggable, dismissible panel, different subject, its own persisted
          geometry. `trade` is the destination and `why_no_trade` is shown just
          as prominently when there is none, so the panel is never empty.
        */}
        {drillSymbol !== null && (
          <ChartOverlay
            id="drill"
            title={`${drillSymbol} › ${drillView}`}
            onClose={() => setDrillSymbol(null)}
            defaultWidth={760}
            defaultHeight={680}
            header={
              <Tooltip
                keys={['close']}
                tools={tools}
                fallbackTitle="Close drill-down"
                label="Dismisses the drill-down. Escape does the same, as does clicking the backdrop."
                tone="stat"
              >
                <button
                  type="button"
                  className="overlay-close"
                  aria-label="Close drill-down"
                  onClick={() => setDrillSymbol(null)}
                >
                  ×
                </button>
              </Tooltip>
            }
          >
            <SymbolDrilldown
              symbol={drillSymbol}
              tools={tools}
              trail={['alerts', drillSymbol, drillView]}
              onTrailStep={(step) => {
                if (step === 'alerts') setDrillSymbol(null);
                else setDrillView(step);
              }}
              onInspect={drillInto}
              initialDetail={drillSeed !== null && drillSeed.symbol === drillSymbol ? (drillSeed as SymbolDetailMsg) : null}
            />
          </ChartOverlay>
        )}
      </TooltipViewer>
    </div>
  );
}

export default function App() {
  const [seed, setSeed] = useState<string | null>(null);
  const onSeed = useCallback((symbol: string) => setSeed((prev) => prev ?? symbol), []);

  return (
    <HeatProvider>
      <SelectionProvider seedSymbol={seed}>
        <Dashboard onSeed={onSeed} />
      </SelectionProvider>
    </HeatProvider>
  );
}
