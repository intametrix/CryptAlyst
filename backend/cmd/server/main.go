// Command server runs the pumpdump backend: a Binance spot flow scanner, a
// market-wide pump/dump screener, a Turtle survey, and the HTTP/SSE API that
// feeds the React dashboard.
//
// Wiring, in one picture:
//
//	Binance @ticker  -> flow.Engine ----------\
//	Binance @aggTrade-> flow.Engine ----------> scan loop (1/s)
//	                                            |-> onset scoring -> alerts
//	                                            |-> SSE broadcast
//	REST klines      -> screen.Screener -------/  (every 60s)
//	REST klines      -> turtle.Survey ---------/  (every 5 min)
//	                           HTTP + SSE -> React dashboard
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"pumpdump/backend/internal/api"
	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/detect"
	"pumpdump/backend/internal/events"
	"pumpdump/backend/internal/fastsample"
	"pumpdump/backend/internal/flow"
	"pumpdump/backend/internal/graph"
	"pumpdump/backend/internal/heat"
	"pumpdump/backend/internal/ledger"
	"pumpdump/backend/internal/market"
	"pumpdump/backend/internal/microburst"
	"pumpdump/backend/internal/models"
	"pumpdump/backend/internal/outlook"
	"pumpdump/backend/internal/perf"
	"pumpdump/backend/internal/regime"
	"pumpdump/backend/internal/screen"
	"pumpdump/backend/internal/turtle"
	"pumpdump/backend/web"
)

type options struct {
	addr                 string
	minLiquidity         float64
	maxSymbols           int
	extraSymbols         string
	windowSec            float64
	minBurstMult         float64
	minBurstZ            float64
	minMovePct           float64
	minSigmaMult         float64
	minCoverage          float64
	minTrades            int64
	minFlowUSD           float64
	cooldownSec          float64
	turtleLog            string
	severityMin          string
	bootstrapTopN        int
	screenTopN           int
	screenMinMove        float64
	screenMinVolRatio    float64
	screenMinVolZ        float64
	screenMinRecentUSD   float64
	screenMinQuoteVolume float64
	surveyTopN           int
	equity               float64
	timeframes           string
	adxMin               float64
	longOnly             bool
	disableScreen        bool
	disableSurvey        bool
	dataDir              string
	alertHistory         string
	ledgerPath           string
	ledgerHorizon        time.Duration
	calibrationPath      string
	microburstDir        string
}

func main() {
	log.SetFlags(log.Ltime)
	opt := parseFlags()

	// Anchor the durable stores. Explicit per-store flags win; everything left
	// at its default resolves under -data-dir, so alerts, the ledger, the
	// turtle session log and the calibration model always land in one known
	// place regardless of where the binary is launched from. Legacy files from
	// the pre-data-dir era are imported once (copied, never moved, never
	// overwritten) so no existing data is stranded.
	explicit := map[string]bool{}
	flag.Visit(func(f *flag.Flag) { explicit[f.Name] = true })
	paths := resolveStorePaths(opt.dataDir, []storeSpec{
		{name: "alerts", flagName: "alert-history", defaultVal: "alerts.jsonl", current: opt.alertHistory},
		{name: "ledger", flagName: "ledger", defaultVal: "ledger.jsonl", current: opt.ledgerPath},
		{name: "turtle", flagName: "turtle-log", defaultVal: "turtle_sessions.jsonl", current: opt.turtleLog},
		{name: "calibration", flagName: "calibration", defaultVal: "calibration.json", current: opt.calibrationPath},
	}, explicit)
	opt.alertHistory, opt.ledgerPath = paths["alerts"], paths["ledger"]
	opt.turtleLog, opt.calibrationPath = paths["turtle"], paths["calibration"]

	if opt.dataDir != "" {
		if err := os.MkdirAll(opt.dataDir, 0o755); err != nil {
			log.Printf("data: creating %s failed: %v", opt.dataDir, err)
		}
	}
	for _, s := range []struct{ name, legacy string }{
		{"alerts", "alerts.jsonl"},
		{"ledger", "ledger.jsonl"},
		{"turtle", "turtle_sessions.jsonl"},
		{"calibration", "calibration.json"},
	} {
		p := paths[s.name]
		if p == "" {
			continue
		}
		if ok, err := importLegacyStore(p, s.legacy); err != nil {
			log.Printf("data: importing legacy %s failed: %v", s.legacy, err)
		} else if ok {
			log.Printf("data: imported legacy %s -> %s (original left in place)", s.legacy, p)
		}
		if abs, err := filepath.Abs(p); err == nil {
			p = abs
		}
		log.Printf("data: %s -> %s", s.name, p)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	client := binance.NewClient(binance.SpotBase)
	universe := market.New(market.Config{
		QuoteAssets:       []string{"USDT"},
		MinQuoteVolume24h: opt.minLiquidity,
		MaxSymbols:        opt.maxSymbols,
		ExtraSymbols:      strings.Split(opt.extraSymbols, ","),
		ExcludePatterns:   []string{"UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"},
		// Stablecoins and fiat pairs trade enormous volume with almost no
		// price movement, so their flow "bursts" are peg noise. Left in, they
		// permanently top the heat table and the glow stops meaning anything.
		ExcludeSymbols: []string{
			"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "USDPUSDT",
			"DAIUSDT", "USD1USDT", "USDEUSDT", "AEURUSDT", "EURUSDT",
			"GBPUSDT", "TRYUSDT", "BRLUSDT", "ARSUSDT", "JPYUSDT",
		},
	})
	engine := flow.NewEngine(flow.Config{
		Market:           "spot",
		WindowSec:        opt.windowSec,
		MinBurstMultiple: opt.minBurstMult,
		MinBurstZ:        opt.minBurstZ,
		MinAbsFlow:       opt.minFlowUSD,
		MinMovePct:       opt.minMovePct,
		MinSigmaMultiple: opt.minSigmaMult,
		MinCoverage:      opt.minCoverage,
		MaxSpreadPct:     0.004,
		MinBookDepthUSD:  500,
		MaxLatePct60s:    6,
		Max24hMovePct:    60,
		// Acceleration is what separates an onset from a tail, so it stays on
		// even in the sensitive WATCH configuration.
		RequireAcceleration:   true,
		MinTrades:             opt.minTrades,
		BaselineFloorFraction: 0.25,
		TrackCapacitySeconds:  600,
	})

	hub := events.NewHub()
	alerts := api.NewAlertBuffer(2000)
	// Durable alert history so chart markers show what a symbol did EARLIER,
	// not merely since this process started.
	alerts.SetHistoryPath(opt.alertHistory)

	// The turtle trading session. Idle until the dashboard's start button, and
	// given a 300 USDT wallet by default -- see turtle.DefaultConfig.
	turtleEngine := turtle.New(turtle.DefaultConfig())

	// The durable session log. "Never forget any data" is the human instruction,
	// so a failure to open it is LOUD rather than silent -- a session that
	// silently stopped recording would look identical to one with nothing to
	// record.
	turtleStorePath := opt.turtleLog
	turtleStore, terr := turtle.NewStore(turtleStorePath)
	if terr != nil {
		log.Printf("turtle session log DISABLED (%v); session history will not be durable", terr)
		turtleStorePath = ""
		turtleStore, _ = turtle.NewStore("")
	} else if turtleStorePath != "" {
		log.Printf("turtle session log: %s", turtleStorePath)
	}
	turtleEngine.SetStore(turtleStore)

	// The asynchronous model runner. Every model gets its own goroutine, so a slow
	// or panicking model can no longer delay or kill the pipeline that feeds the
	// others -- which is exactly what happened when aggTrade ingestion lived inside
	// a single model and its absence silently emptied the taker buffer.
	modelRunner := models.NewRunner(&models.FlowSource{Engine: engine})
	for _, m := range models.All() {
		modelRunner.Add(m)
	}
	modelRunner.Start(ctx)

	cfg := engine.Config()

	// The detector registry. Created before the stream so the stream's handler
	// can route into it without a nil race; models are registered below, once
	// the components they wrap exist.
	registry := detect.NewRegistry()

	// --- startup: metadata + universe + baseline seeding -------------------
	log.Printf("pumpdump backend starting")
	if err := client.Ping(ctx); err != nil {
		log.Fatalf("binance unreachable: %v", err)
	}
	if n, err := universe.LoadMetadata(ctx, client); err != nil {
		log.Fatalf("exchangeInfo failed: %v", err)
	} else {
		log.Printf("universe: %d USDT symbols from exchangeInfo", n)
	}
	if _, err := universe.Refresh(ctx, client); err != nil {
		log.Fatalf("24h ticker failed: %v", err)
	}
	log.Printf("universe: %s", universe.Summary())

	tracked := universe.Tracked()
	engine.SetUniverse(tracked)
	seedBaselines(ctx, client, universe, engine, opt.bootstrapTopN)

	// Ticker pump: coalesces per-symbol ticks for the live chart push. Created
	// before the stream so the handler can forward into it.
	tickerPump := api.NewTickerPump(hub)

	stream := binance.NewStream(binance.SpotStreamBase, func(name string, data json.RawMessage) {
		// Route by stream name; the registry fans out to every enabled model.
		switch {
		case strings.HasSuffix(name, "@ticker"):
			registry.Observe(detect.Event{Type: detect.EventTicker, Raw: data})
			tickerPump.Observe(data)
		case strings.HasSuffix(name, "@aggTrade"):
			// INGESTION IS NOT A MODEL'S JOB, and this line is the fix for a bug
			// that silently zeroed three features at once.
			//
			// Taker prints used to be recorded only from inside a detector model
			// (detect/models.go called engine.IngestAggTrade). If that model was not
			// enabled -- or not scheduled -- NO trades were recorded at all, and the
			// money-flow sampler, the impulse read and the order-size signal all
			// read confident zeroes with no error anywhere. The symptom looked like
			// three broken features rather than one missing subscription.
			//
			// Recording happens here, unconditionally, at the point the exchange
			// data arrives. Models observe; they do not decide whether the system
			// remembers what it was told.
			engine.IngestAggTrade(data)
			registry.Observe(detect.Event{Type: detect.EventAggTrade, Raw: data})
		}
	}, func(connected bool, count int) {
		hub.Broadcast(map[string]any{
			"type":      "status",
			"ts_ms":     time.Now().UnixMilli(),
			"stream_up": connected,
			"streams":   count,
		})
	})
	setStreamUniverse(stream, tracked)
	go stream.Run(ctx)

	// --- collaborators -----------------------------------------------------
	th := screen.DefaultThresholds()
	th.MinMove5m = opt.screenMinMove
	th.MinVolRatio = opt.screenMinVolRatio
	th.MinVolZ = opt.screenMinVolZ
	th.MinRecentUSD = opt.screenMinRecentUSD
	th.MinQuoteVolume24h = opt.screenMinQuoteVolume
	screener := screen.New(client, universe, th,
		opt.screenTopN, screen.DefaultKlineLimit, 8, "1m")

	var survey *turtle.Survey
	if !opt.disableSurvey {
		survey = turtle.NewSurvey(client, universe, turtle.DefaultParams(),
			splitCSV(opt.timeframes), opt.equity, opt.surveyTopN, 250, 8, opt.longOnly)
	}

	// --- heat: how interesting each symbol is, for the UI glow -------------
	// Enrichment accessors are closures over the cached screen and survey
	// results, so heat stays a pure consumer and this package does not have to
	// know about either.
	heatSvc := heat.New(engine, heat.DefaultParams(),
		func() map[string]heat.ScreenFacts {
			rows, _, _ := screener.Cached()
			out := make(map[string]heat.ScreenFacts, len(rows))
			for _, row := range rows {
				facts := heat.ScreenFacts{
					ATRPct:        row.ATRPct,
					ClosePosition: row.ClosePosition,
					VolRatio:      row.VolRatio,
				}
				if row.From24hHigh != nil {
					facts.From24hHigh = *row.From24hHigh
				}
				if row.From24hLow != nil {
					facts.From24hLow = *row.From24hLow
				}
				out[row.Symbol] = facts
			}
			return out
		},
		func() map[string]heat.TurtleFacts {
			if survey == nil {
				return nil
			}
			payload, ok := survey.Current().(map[string]any)
			if !ok {
				return nil
			}
			setups, ok := payload["setups"].([]map[string]any)
			if !ok {
				return nil
			}
			out := make(map[string]heat.TurtleFacts, len(setups))
			for _, setup := range setups {
				symbol, _ := setup["symbol"].(string)
				if symbol == "" {
					continue
				}
				out[symbol] = heat.TurtleFacts{
					ADX:          toFloat(setup["adx"]),
					RSI:          toFloat(setup["rsi"]),
					ExtensionATR: toFloat(setup["extension_up_atr"]),
					ATRPct:       toFloat(setup["atr_pct"]),
				}
			}
			return out
		})

	// --- pluggable detection models ----------------------------------------
	// Each detection strategy registers itself here. Adding a model means
	// implementing detect.Detector and adding one line; nothing else in the
	// scanner changes.
	flowDetector := detect.NewFlowOnset(engine, time.Duration(opt.cooldownSec)*time.Second, 180*time.Second, 12)
	registry.Register(flowDetector)
	registry.Register(detect.NewPumpScreen(screener))
	registry.Register(detect.NewTurtleBreakout(survey))
	registry.Register(detect.NewBullsAndBears(engine, 60))
	graphCatalog := graph.Catalog(registry)
	log.Printf("detectors registered: %s", strings.Join(registry.Names(), ", "))

	// --- event outlook: state, ETA to reversal, short-horizon distribution ---
	outlookSvc := outlook.New(outlook.DefaultParams(), opt.calibrationPath)
	microburstSvc := microburst.New(opt.microburstDir)
	regimeCache := regime.NewCache()
	// Per-symbol track record, so confidence can learn which instruments
	// actually follow through on their detections.
	perfSvc := perf.New(opt.ledgerPath, perf.DefaultParams())

	// --- prediction ledger -------------------------------------------------
	// Every emitted signal is recorded with its feature vector and then tracked
	// along its price path until a fixed horizon, so the thresholds in this
	// system can be measured rather than argued. Tracking samples the engine's
	// own last price, so it costs no REST weight.
	var recorder ledger.Recorder
	if opt.ledgerPath != "" {
		rec, err := ledger.New(opt.ledgerPath, enginePrice{engine: engine}, ledger.Config{
			Path:    opt.ledgerPath,
			Horizon: opt.ledgerHorizon,
		})
		if err != nil {
			log.Printf("ledger disabled: %v", err)
		} else {
			recorder = rec
			go rec.Track(ctx)
			log.Printf("prediction ledger: %s (horizon %s)", opt.ledgerPath, opt.ledgerHorizon)
		}
	}

	// Live chart push: flush coalesced ticks on a short interval.
	go tickerPump.Run(ctx)

	// 1s path sampling for the first minute after each detection, so the
	// ten-second question is settled by measurement rather than by prior.
	if opt.ledgerPath != "" {
		sampler := fastsample.New(fastsample.Config{Path: opt.ledgerPath},
			enginePrice{engine: engine})
		go sampler.Run(ctx)
	}

	// --- background loops --------------------------------------------------
	var wg sync.WaitGroup
	if !opt.disableScreen {
		wg.Add(1)
		go func() {
			defer wg.Done()
			screenLoop(ctx, screener, hub, 60*time.Second)
			go regimeLoop(ctx, universe, regimeCache, hub, 60*time.Second)
		}()
	}
	if survey != nil {
		wg.Add(1)
		go func() {
			defer wg.Done()
			surveyLoop(ctx, survey, hub, 5*time.Minute)
		}()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		universeLoop(ctx, client, universe, engine, stream, 60*time.Second)
	}()
	wg.Add(1)
	go func() {
		defer wg.Done()
		scanLoop(ctx, engine, registry, flowDetector, hub, alerts, stream, heatSvc, recorder, outlookSvc, microburstSvc, universe, opt, turtleEngine)
	}()
	wg.Add(1)
	go func() {
		defer wg.Done()
		housekeepingLoop(ctx, engine, hub, screener, survey, stream)
	}()

	// --- HTTP --------------------------------------------------------------
	startedAt := time.Now()
	summary := configSummary(opt, cfg)
	server := api.New(api.Deps{
		Hub:           hub,
		Turtle:        turtleEngine,
		TurtleLogPath: turtleStorePath,
		Models:        modelRunner,
		Engine:        engine,
		Universe:      universe,
		Screener:      screener,
		Alerts:        alerts,
		Survey:        surveyProvider(survey),
		Detectors:     registry,
		Graph:         graphCatalog,
		Heat:          heatSvc,
		KlinesCache:   api.NewKlinesCache(),
		TickerPump:    tickerPump,
		Outlook:       outlookSvc,
		Perf:          perfSvc,
		Market:        client,
		Static:        web.Dist(),
		ConfigSummary: summary,
		StartedAt:     startedAt,
		StreamUp:      stream.IsConnected,
	})

	// Watchlist rotation. Started once here, after the server exists, because the
	// loop needs the API's own selector; it decides for itself whether a session is
	// live rather than being tied to the start/stop button.
	go server.StartTurtleRotation(ctx)

	// MARK ON A CLOCK. Without this, a position's price only moved when a NEW ALERT
	// fired for it, so PnL sat frozen between alerts -- and because the 2N stop is
	// checked inside Mark, a position could pass straight through its stop and never
	// be closed. Stop losses were luck rather than rules.
	// NOT awaited. RunMarks returns a channel that closes only on shutdown, so
	// `<-` here would block before the HTTP server ever started listening.
	go turtleEngine.RunMarks(func() map[string]float64 {
		rows := engine.Snapshot(0, time.Now().UnixMilli())
		out := make(map[string]float64, len(rows))
		for i := range rows {
			if rows[i].Price > 0 {
				out[rows[i].Symbol] = rows[i].Price
			}
		}
		return out
	}, time.Second)

	httpServer := &http.Server{
		Addr:              opt.addr,
		Handler:           server.Routes(),
		ReadHeaderTimeout: 10 * time.Second,
		// No WriteTimeout: the SSE stream is long-lived by design.
	}
	go func() {
		log.Printf("dashboard: http://127.0.0.1%s", displayAddr(opt.addr))
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http server failed: %v", err)
		}
	}()

	<-ctx.Done()
	// Cancelling ctx (via the signal handler) is what stops the stream and the
	// background loops; there is nothing else to tear down explicitly.
	log.Printf("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(shutdownCtx)
	wg.Wait()
}

func parseFlags() options {
	var o options
	flag.StringVar(&o.addr, "addr", "127.0.0.1:8787", "HTTP listen address")
	// $5M, not the $750k first drafted: below roughly this level, expected
	// slippage on a 2N stop exit runs 0.3-1% and thin books manufacture false
	// flow bursts. This is the precision-over-recall lever.
	flag.Float64Var(&o.minLiquidity, "min-liq", 5_000_000, "minimum 24h quote volume to track a symbol")
	flag.IntVar(&o.maxSymbols, "max-symbols", 400, "max symbols tracked (by 24h volume)")
	flag.StringVar(&o.extraSymbols, "extra", "", "extra symbols to always track (comma-separated)")
	flag.Float64Var(&o.windowSec, "window", 10, "seconds of flow to measure")
	flag.Float64Var(&o.minBurstMult, "min-burst-mult", 8, "flow multiple vs the symbol's own baseline")
	flag.Float64Var(&o.minBurstZ, "min-burst-z", 4, "flow z-score vs the symbol's own baseline")
	flag.Float64Var(&o.minMovePct, "min-move-pct", 0.4, "minimum move % within the window")
	flag.Float64Var(&o.minSigmaMult, "min-sigma-mult", 3, "minimum move as a multiple of window volatility")
	flag.Float64Var(&o.minCoverage, "min-coverage", 0.7,
		"required fraction of the flow window covered by received ticks; lower it when "+
			"the universe includes symbols that tick less than once a second")
	flag.Int64Var(&o.minTrades, "min-trades", 8, "minimum trades inside the window")
	flag.Float64Var(&o.minFlowUSD, "min-flow-usd", 5000, "absolute minimum flow in the window (USD)")
	flag.Float64Var(&o.cooldownSec, "cooldown", 900, "seconds before re-alerting the same symbol+direction")
	flag.StringVar(&o.severityMin, "min-severity", "WATCH", "WATCH | ALERT | CRITICAL")
	flag.IntVar(&o.bootstrapTopN, "bootstrap-top-n", 150, "symbols to seed flow baselines for at startup")
	flag.IntVar(&o.screenTopN, "screen-top", 200, "symbols to include in the pump/dump screen")
	flag.Float64Var(&o.screenMinMove, "screen-min-move", 0.8, "pump/dump screen minimum 5m move %")
	flag.Float64Var(&o.screenMinVolRatio, "screen-min-vol-ratio", 1.8, "pump/dump screen minimum volume ratio")
	flag.Float64Var(&o.screenMinVolZ, "screen-min-vol-z", 1.2, "pump/dump screen minimum volume z-score")
	flag.Float64Var(&o.screenMinRecentUSD, "screen-min-recent-usd", 5000, "pump/dump screen minimum recent USD flow")
	flag.Float64Var(&o.screenMinQuoteVolume, "screen-min-quote-volume", 50000, "pump/dump screen minimum 24h quote volume")
	flag.IntVar(&o.surveyTopN, "survey-top", 150, "symbols to include in the Turtle survey")
	flag.Float64Var(&o.equity, "equity", 10_000, "account equity for position sizing")
	flag.StringVar(&o.timeframes, "timeframes", "1d,4h,1h", "survey timeframes")
	flag.Float64Var(&o.adxMin, "adx-min", 20, "ADX trend filter (0 disables)")
	flag.BoolVar(&o.longOnly, "long-only", true, "spot cannot short; report only upside breakouts")
	flag.BoolVar(&o.disableScreen, "no-screen", false, "disable the periodic pump/dump screen")
	flag.BoolVar(&o.disableSurvey, "no-survey", false, "disable the periodic Turtle survey")
	flag.StringVar(&o.alertHistory, "alert-history", "alerts.jsonl",
		"JSONL file for durable alert history (chart markers); empty disables")
	flag.StringVar(&o.dataDir, "data-dir", "./data",
		"directory anchoring the durable stores; overridden per store by the flags below")
	flag.StringVar(&o.ledgerPath, "ledger", "ledger.jsonl",
		"JSONL prediction ledger for the offline learner; empty disables")
	flag.StringVar(&o.turtleLog, "turtle-log", "turtle_sessions.jsonl",
		"append-only turtle session history; never rotated, because the instruction is that no data is forgotten")
	flag.StringVar(&o.calibrationPath, "calibration", "calibration.json",
		"fitted probability model written by cmd/learner; absent means uncalibrated")
	flag.StringVar(&o.microburstDir, "microburst-dir", "microburst_out",
		"directory of microburst_<N>s.json horizon models written by cmd/microburst; absent means no horizon forecast")
	flag.DurationVar(&o.ledgerHorizon, "ledger-horizon", 15*time.Minute,
		"how long a prediction is tracked before it is labelled")
	flag.Parse()
	return o
}

func displayAddr(addr string) string {
	if strings.HasPrefix(addr, "127.0.0.1") || strings.HasPrefix(addr, "0.0.0.0") {
		_, port, err := splitHostPort(addr)
		if err == nil {
			return ":" + port
		}
	}
	return addr
}

// seedBaselines warms the flow baselines from 1s klines.
//
// Without this the scanner needs ~80 seconds of live ticks before any symbol
// has a usable baseline, so a restart would be blind to exactly the moves it
// exists to catch.
func seedBaselines(ctx context.Context, client *binance.Client, universe *market.Universe, engine *flow.Engine, topN int) {
	targets := universe.TopByVolume(topN)
	if len(targets) == 0 {
		return
	}
	log.Printf("seeding flow baselines from 1s klines for %d symbols...", len(targets))
	start := time.Now()
	pool := binance.NewPool(8)
	var seeded int64
	var mu sync.Mutex

	for _, item := range targets {
		item := item
		pool.Go(func() {
			klines, err := client.Klines(ctx, item.Symbol, "1s", 1000)
			if err != nil || len(klines) == 0 {
				return
			}
			series := make([]flow.Series1s, 0, len(klines))
			for _, k := range klines {
				series = append(series, flow.Series1s{
					OpenTime:    k.OpenTime,
					Close:       k.Close,
					QuoteVolume: k.QuoteVolume,
				})
			}
			// Prime the 24h volume first so the liquidity floor is correct
			// from the very first scan rather than after the first live tick.
			engine.SetQuoteVolume(item.Symbol, item.QuoteVolume24h)
			if n := engine.SeedBaseline(item.Symbol, series); n > 0 {
				mu.Lock()
				seeded++
				mu.Unlock()
			}
		})
	}
	pool.Wait()
	log.Printf("seeded %d/%d baselines in %s", seeded, len(targets), time.Since(start).Round(time.Second))
}

// setStreamUniverse subscribes the streams the engine actually consumes.
//
// THE BUG THIS FIXES, and it is the root cause of three features reading zero.
// This function subscribed ONLY ticker streams. Nothing ever called
// AggTradeStream, so @aggTrade messages never arrived at all -- which made the
// dispatch case for them dead code, kept the taker buffer permanently empty, and
// left money-flow, the impulse read and the average-order-size signal all
// reporting confident zeroes.
//
// The failure was invisible because every layer LOOKED correct in isolation: the
// stream name helper existed, the dispatch case existed, the ingest function
// existed, the ring buffer existed, and the samplers existed. The subscription was
// the one link nobody had checked, and a subscription that is missing produces no
// error -- only silence.
//
// aggTradeSTREAMS IS A SUBSET ON PURPOSE. Binance allows about 1024 streams on one
// connection and ticker already consumes one per symbol. At 400 tracked symbols,
// subscribing aggTrade for all of them would be 800 streams and would risk the
// whole connection, including the ticker feed that everything else depends on. The
// most liquid symbols are subscribed, because those are the ones with enough
// prints for an order-size average to mean anything; a thin symbol would not clear
// MinPrintsForOrderSize anyway.
func setStreamUniverse(stream *binance.Stream, symbols []string) {
	names := make([]string, 0, len(symbols)*2)
	for _, s := range symbols {
		names = append(names, binance.TickerStream(s))
	}
	for i, s := range symbols {
		if i >= aggTradeStreamLimit {
			break
		}
		names = append(names, binance.AggTradeStream(s))
	}
	log.Printf("streams: %d ticker + %d aggTrade = %d total (limit %d)",
		len(symbols), min(len(symbols), aggTradeStreamLimit), len(names), aggTradeStreamLimit)
	stream.SetStreams(names)
}

// aggTradeStreamLimit bounds the trade-level subscription. See setStreamUniverse.
const aggTradeStreamLimit = 150

// universeLoop refreshes 24h context and resizes the subscription.
func universeLoop(ctx context.Context, client *binance.Client, universe *market.Universe, engine *flow.Engine, stream *binance.Stream, every time.Duration) {
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if _, err := universe.Refresh(ctx, client); err != nil {
				log.Printf("universe refresh failed: %v", err)
				continue
			}
			tracked := universe.Tracked()
			engine.SetUniverse(tracked)
			setStreamUniverse(stream, tracked)
			engine.ApplyFloors()
			log.Printf("universe: %s", universe.Summary())
		}
	}
}

// hotSymbols tracks which symbols have been escalated to trade-level data.
// --------------------------------------------------------------------------
// scan loop
// --------------------------------------------------------------------------

// scanLoop drives the detector registry: once per second it asks every enabled
// model for signals, publishes them, and honours escalation requests.
//
// The loop deliberately contains no detection logic. Cooldowns, scoring and
// escalation decisions all live inside the detectors, so adding a model does
// not mean touching this function.
func scanLoop(
	ctx context.Context,
	engine *flow.Engine,
	registry *detect.Registry,
	flowDetector *detect.FlowOnset,
	hub *events.Hub,
	alerts *api.AlertBuffer,
	stream *binance.Stream,
	heatSvc *heat.Service,
	recorder ledger.Recorder,
	outlookSvc *outlook.Service,
	microburstSvc *microburst.Service,
	universe *market.Universe,
	opt options,
	turtleEngine *turtle.Engine,
) {
	cfg := engine.Config()

	scan := time.NewTicker(1 * time.Second)
	defer scan.Stop()
	flowTick := time.NewTicker(1 * time.Second)
	defer flowTick.Stop()

	var scans, signals int64

	for {
		select {
		case <-ctx.Done():
			return
		case <-flowTick.C:
			// The leaderboard is raw engine state, not a detection, so it is
			// read straight from the engine rather than through the registry.
			stamp := time.Now().UnixMilli()
			rows := engine.Snapshot(25, stamp)
			hub.Broadcast(map[string]any{
				"type":       "flow",
				"ts_ms":      stamp,
				"window_sec": cfg.WindowSec,
				"rows":       rows,
				"scans":      scans,
				"signals":    signals,
			})
			// Heat drives the UI's green ticker glow. Capped so the payload
			// stays small; the floor inside the service means only symbols that
			// actually earned a border are sent at all.
			hub.Broadcast(map[string]any{
				"type":  "heat",
				"ts_ms": stamp,
				"rows":  heatSvc.Compute(stamp, 200),
			})
			// Its own message type, because the top-25-by-flow set above is
			// provably NOT the top pumps and dumps set: publishing only the
			// flow message would give the split panel two disagreeing truths.
			splitPumps, splitDumps, splitStats := api.SplitFlowRows(engine, universe, stamp, 10)
			hub.Broadcast(map[string]any{
				"type":          "flow_split",
				"ts_ms":         stamp,
				"pumps":         splitPumps,
				"dumps":         splitDumps,
				"dropped_thin":  splitStats["thin"],
				"dropped_stale": splitStats["stale"],
				"dropped_quiet": splitStats["quiet"],
			})
		case <-scan.C:
			scans++
			now := time.Now().UnixMilli()

			for _, sig := range registry.Evaluate(now) {
				if severityRank(sig.Severity) < severityRank(opt.severityMin) {
					continue
				}
				signals++
				alert := api.Alert{
					Type:       "alert",
					Detector:   sig.Detector,
					Batch:      sig.Batch,
					SnapshotMS: sig.SnapshotMS,
					EventID:    eventID(),
					TSMS:       now,
					Market:     "spot",
					Symbol:     sig.Symbol,
					Kind:       sig.Kind,
					Color:      api.KindColor(sig.Kind),
					Summary:    sig.Summary,
					Severity:   sig.Severity,
					Score:      sig.Score,
					Price:      sig.Price,
					Reasons:    sig.Reasons,
					Components: sig.Components,
					Metrics:    sig.Metrics,
				}
				// Attach the calibrated probability when a fitted model
				// exists. The basis travels with the number so the UI can
				// never show a percentage without saying where it came from.
				if outlookSvc != nil {
					if prob, ok, basis := outlookSvc.Probability(signalFeatures(sig)); ok {
						value := prob
						alert.Probability = &value
						alert.ProbabilityBasis = basis
					} else if basis != "" {
						alert.ProbabilityBasis = basis
					}
				}
				// Horizon-outcome forecast (10/30/60s). Each probability is
				// written as a metric key so the existing UI renders it without
				// a new wire type; the model is optional and degrades to absent.
				if microburstSvc != nil {
					if probs := microburstSvc.Probability(signalMetrics(sig)); len(probs) > 0 {
						if alert.Metrics == nil {
							alert.Metrics = map[string]any{}
						}
						for _, h := range []int{10, 30, 60} {
							if p, ok := probs[h]; ok {
								alert.Metrics[fmt.Sprintf("microburst_up_%ds", h)] = p.PUp
								alert.Metrics[fmt.Sprintf("microburst_reversal_%ds", h)] = p.PReversal
								alert.Metrics[fmt.Sprintf("microburst_neither_%ds", h)] = p.PNeither
							}
						}
					}
				}
				alerts.Add(alert)
				// The turtle session is FED here, at the single point where an
				// alert becomes real. It decides for itself whether to act; this
				// only delivers, so the trading rules stay testable without a
				// live market.
				feedTurtle(turtleEngine, engine, alert)
				hub.Broadcast(alert)
				if recorder != nil {
					// Record before logging: the ledger is the measurement, and
					// a signal that fires but is not recorded is an unmeasured
					// claim, which is the thing this whole layer exists to stop.
					recorder.Record(sig)
				}
				log.Printf("SIGNAL [%s] %s %s %s score=%.0f",
					sig.Detector, sig.Severity, sig.Kind, sig.Symbol, sig.Score)
			}

			// Escalations: a detector asks for trade-level detail on a symbol
			// that just tripped, without knowing that websockets exist.
			for _, request := range registry.Escalations() {
				stream.Subscribe(binance.AggTradeStream(request.Symbol))
			}
			// Release escalations that have lapsed so the stream count stays
			// bounded once the burst of interest is over.
			for _, symbol := range flowDetector.ExpiredEscalations() {
				stream.Unsubscribe(binance.AggTradeStream(symbol))
			}
		}
	}
}

// regimeLoop refreshes the broad-market regime and broadcasts it.
func regimeLoop(ctx context.Context, universe *market.Universe, cache *regime.Cache, hub *events.Hub, every time.Duration) {
	run := func() {
		if cache == nil {
			return
		}
		r := cache.Update(universe.TopByVolume(400))
		hub.Broadcast(r)
	}
	run()
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			run()
		}
	}
}

// screenLoop refreshes the pump/dump screen and broadcasts the result.
func screenLoop(ctx context.Context, screener *screen.Screener, hub *events.Hub, every time.Duration) {
	run := func() {
		rows, scanned, err := screener.Run(ctx)
		if err != nil {
			log.Printf("screen failed: %v", err)
			return
		}
		hub.Broadcast(map[string]any{
			"type":            "screen",
			"ts_ms":           time.Now().UnixMilli(),
			"symbols_scanned": scanned,
			"rows":            rows,
		})
		log.Printf("screen: %d candidates from %d symbols", len(rows), scanned)
	}
	run()
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			run()
		}
	}
}

// surveyLoop refreshes the Turtle survey and broadcasts the result.
func surveyLoop(ctx context.Context, survey *turtle.Survey, hub *events.Hub, every time.Duration) {
	run := func() {
		if err := survey.Refresh(); err != nil {
			log.Printf("survey failed: %v", err)
			return
		}
		hub.Broadcast(survey.Current())
	}
	run()
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			run()
		}
	}
}

// housekeepingLoop emits the status heartbeat and bounds memory.
func housekeepingLoop(ctx context.Context, engine *flow.Engine, hub *events.Hub, screener *screen.Screener, survey *turtle.Survey, stream *binance.Stream) {
	status := time.NewTicker(2 * time.Second)
	defer status.Stop()
	prune := time.NewTicker(30 * time.Second)
	defer prune.Stop()
	started := time.Now()

	for {
		select {
		case <-ctx.Done():
			return
		case <-prune.C:
			engine.PruneTaker()
		case <-status.C:
			stats := engine.Stats()
			_, screenMS, _ := screener.Cached()
			var surveyMS int64
			if survey != nil {
				surveyMS = survey.LastMS()
			}
			hub.Broadcast(map[string]any{
				"type":                   "status",
				"ts_ms":                  time.Now().UnixMilli(),
				"uptime_sec":             int(time.Since(started).Seconds()),
				"symbols_tracked":        stats.SymbolsTracked,
				"baselines_ready":        stats.BaselinesReady,
				"messages":               stats.Messages,
				"truncated":              stats.Truncated,
				"stream_messages":        stream.Messages(),
				"stream_streams":         stream.Streams(),
				"stream_reconnects":      stream.Reconnects(),
				"stream_up":              stream.IsConnected(),
				"stream_last_msg_age_ms": stats.LastMessageMS,
				"screen_generated_ms":    screenMS,
				"survey_generated_ms":    surveyMS,
				"clients":                hub.Clients(),
			})
		}
	}
}

func configSummary(opt options, cfg flow.Config) map[string]any {
	return map[string]any{
		"market":               "spot",
		"window_sec":           cfg.WindowSec,
		"min_burst_multiple":   cfg.MinBurstMultiple,
		"min_burst_z":          cfg.MinBurstZ,
		"min_move_pct_10s":     cfg.MinMovePct,
		"min_flow_usd":         cfg.MinAbsFlow,
		"min_quote_volume_24h": opt.minLiquidity,
		"max_symbols":          opt.maxSymbols,
		"cooldown_sec":         opt.cooldownSec,
		"severity_min":         opt.severityMin,
		"equity":               opt.equity,
		"timeframes":           splitCSV(opt.timeframes),
		"adx_min":              opt.adxMin,
		"long_only":            opt.longOnly,
		"screen_enabled":       !opt.disableScreen,
		"survey_enabled":       !opt.disableSurvey,
	}
}

// surveyProvider adapts a possibly-nil survey to the api interface. Returning
// an untyped nil *turtle.Survey would satisfy the interface and then panic on
// the first call, so the nil check happens here instead.
func surveyProvider(s *turtle.Survey) api.SurveyProvider {
	if s == nil {
		return nil
	}
	return s
}

// toFloat coerces an untyped JSON number from the survey payload.
// signalFeatures extracts the model's feature vector from a signal's metrics.
//
// Missing features are simply absent: the scorer refuses to evaluate a partial
// vector rather than substituting a zero, because a zero is a real value for
// these features and would silently change the prediction.
// signalMetrics coerces the full metrics + components map to a flat float64
// feature map, for the microburst horizon models whose feature union is wider
// than the 7-name calibration contract.
func signalMetrics(sig detect.Signal) map[string]float64 {
	out := make(map[string]float64, len(sig.Metrics)+len(sig.Components))
	for k, v := range sig.Metrics {
		switch n := v.(type) {
		case float64:
			out[k] = n
		case float32:
			out[k] = float64(n)
		case int:
			out[k] = float64(n)
		case int64:
			out[k] = float64(n)
		case bool:
			if n {
				out[k] = 1
			}
		}
	}
	for k, v := range sig.Components {
		if _, ok := out[k]; !ok {
			out[k] = v
		}
	}
	return out
}

func signalFeatures(sig detect.Signal) map[string]float64 {
	names := []string{
		"burst_multiple", "burst_z", "flow_quote", "coverage",
		"change_10s", "change_60s", "taker_buy_ratio",
	}
	out := make(map[string]float64, len(names))
	for _, name := range names {
		if value, ok := sig.Metrics[name]; ok {
			switch n := value.(type) {
			case float64:
				out[name] = n
			case int:
				out[name] = float64(n)
			case int64:
				out[name] = float64(n)
			}
			continue
		}
		if value, ok := sig.Components[name]; ok {
			out[name] = value
		}
	}
	return out
}

// enginePrice adapts the in-memory flow engine to ledger.PriceFetcher.
//
// Sampling the engine rather than the REST API is deliberate: the tracker polls
// every few seconds per open prediction, and doing that over HTTP would burn
// exchange weight on data the process already holds.
type enginePrice struct{ engine *flow.Engine }

func (e enginePrice) Price(_ context.Context, symbol string) (float64, error) {
	if price, ok := e.engine.LastPrice(symbol); ok && price > 0 {
		return price, nil
	}
	return 0, fmt.Errorf("no live price for %s", symbol)
}

func toFloat(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case float32:
		return float64(n)
	case int:
		return float64(n)
	case int64:
		return float64(n)
	default:
		return 0
	}
}

func splitCSV(s string) []string {
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if trimmed := strings.TrimSpace(p); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	return out
}

// feedTurtle converts a live alert into the turtle engine's own input, carrying
// the market context the entry rules need.
//
// The conversion is explicit rather than passing the api.Alert straight through
// because the turtle rules depend on values the alert does not carry: N (ATR) for
// sizing, the Donchian bounds for the confirm, and realised volatility for the
// veto. Supplying them here keeps that dependency in one visible place instead of
// hiding it inside the engine.
func feedTurtle(te *turtle.Engine, flowEngine *flow.Engine, a api.Alert) {
	if te == nil || flowEngine == nil {
		return
	}
	st := te.Session()
	if st.State != turtle.StateRunning {
		return
	}

	now := time.Now()
	rows := flowEngine.Snapshot(0, now.UnixMilli())
	var row *flow.Candidate
	for i := range rows {
		if rows[i].Symbol == a.Symbol {
			row = &rows[i]
			break
		}
	}
	if row == nil {
		return
	}

	// Mark every open position on this symbol first, so a stop that was already
	// breached closes before a new entry is considered on the same tick.
	te.Mark(a.Symbol, row.Price, now)

	vol := realisedVolPct(row)
	te.OnAlertForSymbol(turtle.Alert{
		ID:        a.EventID,
		Symbol:    a.Symbol,
		Kind:      a.Kind,
		Severity:  a.Severity,
		Price:     row.Price,
		ChangePct: valueOr(row.Change10s, 0),
		At:        now,
		VolPct:    vol,
		ATR:       atrFromSigma(row),
	}, row.Price, 0, vol)
}

// realisedVolPct expresses the symbol's recent movement as a percentage, which
// is the unit the volatility veto is written in.
func realisedVolPct(row *flow.Candidate) float64 {
	s := row.Sigma10s
	if s <= 0 || row.Price <= 0 {
		return 0
	}
	return s / row.Price * 100
}

// atrFromSigma is N in price terms.
//
// THE BUG THIS FIXES, and it made the system untradeable. N was set to the
// TEN-SECOND dispersion. A live position was stopped out 3.0 seconds after it
// opened, because a 2N stop computed from ten seconds of volatility sits about
// 0.003% away -- inside the bid-ask spread, so it is hit by noise before the trade
// has any chance to work. The blotter showed it plainly:
//
//	GIGGLEUSDT  BUY 35.5900 -> 35.5890  hold_sec 2.9978  reason "2N stop"
//
// The book's N is a RANGE measure over a meaningful horizon. Ten seconds is not a
// horizon, it is an instant. Dispersion grows with the square root of time, so N is
// taken at NLookbackSeconds and the difference is roughly an order of magnitude.
//
// It is still an APPROXIMATION -- true ATR needs a bar history the flow engine does
// not keep -- but it is now an approximation of the right quantity rather than of
// the wrong one.
func atrFromSigma(row *flow.Candidate) float64 {
	if row.Sigma10s <= 0 {
		return 0
	}
	// sigmaWindow scales as sqrt(t). NLookbackSeconds of 300 is five minutes, which
	// on a system measuring onset at seconds is a horizon rather than an instant,
	// while still being short enough that the stop reflects the CURRENT regime.
	const measuredSec = 10.0
	const horizonSec = 300.0
	return row.Sigma10s * math.Sqrt(horizonSec/measuredSec)
}

func valueOr(v *float64, def float64) float64 {
	if v == nil {
		return def
	}
	return *v
}
