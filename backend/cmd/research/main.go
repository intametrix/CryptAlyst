// Command research builds an offline horizon-outcome model for the
// rally/continuation research system.
//
// It is deliberately separate from the live ingestion path (same discipline
// as cmd/learner): it downloads historical klines, forms ONE training example
// per volatility-scaled breakout, labels each example with ATR-scaled
// upside/downside/neither outcomes over a fixed horizon, trains two binary
// logistic models (P(upside first), P(downside first)) using the existing
// ledger.FitLogistic, evaluates them with a chronological walk-forward split,
// and writes a calibration file plus a text report.
//
// It never trades, never touches the event bus, and never claims a probability
// is a certainty. Every output carries its label rule and its sample count so
// a number can be reproduced and audited.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/ledger"
)

// Config holds the runtime knobs for one research run.
type Config struct {
	Interval    string  // kline interval, e.g. "1h"
	Limit       int     // klines fetched per symbol (max 1000 per Binance request)
	HorizonBars int     // outcome horizon in bars
	UpATR       float64 // upside barrier in multiples of ATR
	DownATR     float64 // downside barrier in multiples of ATR
	ATRPeriod   int     // ATR lookback
	RangeBars   int     // recent-range lookback for the breakout trigger
	BreakoutATR float64 // breakout when close is beyond the range by this many ATRs
	TrainFrac   float64 // chronological train fraction (the rest is holdout)
	MinEvents   int     // refuse to fit below this many events
}

// DefaultConfig returns the starting values (hourly, 24h horizon, 2 ATR
// barriers).
func DefaultConfig() Config {
	return Config{
		Interval:    "1h",
		Limit:       1000,
		HorizonBars: 24,
		UpATR:       2.0,
		DownATR:     2.0,
		ATRPeriod:   14,
		RangeBars:   20,
		BreakoutATR: 0.5,
		TrainFrac:   0.7,
		MinEvents:   100,
	}
}

// Event is one training example: a breakout bar with features captured up to
// that bar (never after) and a label decided only from bars after it.
type Event struct {
	Symbol    string
	TSMS      int64
	Direction int // +1 up-break, -1 down-break
	Features  map[string]float64
	UpFirst   bool
	DownFirst bool
	Neither   bool
}

func main() {
	coinsFlag := flag.String("coins", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,DOTUSDT",
		"comma-separated coin universe")
	interval := flag.String("interval", "1h", "kline interval")
	limit := flag.Int("limit", 1000, "klines per symbol")
	horizon := flag.Int("horizon", 24, "outcome horizon in bars")
	upATR := flag.Float64("up-atr", 2.0, "upside barrier in ATR multiples")
	downATR := flag.Float64("down-atr", 2.0, "downside barrier in ATR multiples")
	atrPeriod := flag.Int("atr-period", 14, "ATR lookback")
	trainFrac := flag.Float64("train-frac", 0.7, "chronological train fraction")
	minEvents := flag.Int("min-events", 100, "refuse to fit below this many events")
	outDir := flag.String("out", "research_out", "output directory")
	flag.Parse()

	cfg := DefaultConfig()
	cfg.Interval = *interval
	cfg.Limit = *limit
	cfg.HorizonBars = *horizon
	cfg.UpATR = *upATR
	cfg.DownATR = *downATR
	cfg.ATRPeriod = *atrPeriod
	cfg.TrainFrac = *trainFrac
	cfg.MinEvents = *minEvents

	coins := splitCoins(*coinsFlag)
	if len(coins) == 0 {
		fmt.Fprintln(os.Stderr, "-coins is empty")
		os.Exit(1)
	}

	client := binance.NewClient("https://api.binance.com")
	ctx := context.Background()

	// Fetch all symbols. BTC is fetched first (its trend is a cross-coin
	// feature), then the rest.
	histories := map[string][]binance.Kline{}
	for _, sym := range coins {
		bars, err := client.Klines(ctx, sym, cfg.Interval, cfg.Limit)
		if err != nil {
			fmt.Fprintf(os.Stderr, "research: %s: %v (skipping)\n", sym, err)
			continue
		}
		histories[sym] = bars
	}
	btc := histories["BTCUSDT"]

	// Build the event set.
	var events []Event
	for _, sym := range coins {
		bars := histories[sym]
		if len(bars) < cfg.RangeBars+cfg.HorizonBars+cfg.ATRPeriod+2 {
			continue
		}
		events = append(events, buildEvents(sym, bars, btc, cfg)...)
	}

	fmt.Printf("research: %d symbols fetched, %d breakout events\n", len(histories), len(events))
	if len(events) < cfg.MinEvents {
		fmt.Printf("insufficient sample: %d events, need %d\n", len(events), cfg.MinEvents)
		return
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "research: mkdir: %v\n", err)
		os.Exit(1)
	}

	walkForward(events, cfg, *outDir)
}

// splitCoins parses a comma-separated symbol list, trimming and uppercasing.
func splitCoins(s string) []string {
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.ToUpper(strings.TrimSpace(p))
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// buildEvents scans one symbol's bars and emits one Event per volatility-scaled
// breakout. Features are computed only from bars up to the event bar; the
// label is computed only from bars after it, so nothing leaks.
func buildEvents(symbol string, bars, btc []binance.Kline, cfg Config) []Event {
	events := make([]Event, 0)
	warm := cfg.RangeBars + cfg.ATRPeriod + 2
	for i := warm; i+cfg.HorizonBars < len(bars); i++ {
		closeP := bars[i].Close
		atrPct := atrPctAt(bars, i, cfg.ATRPeriod)
		if atrPct <= 0 {
			continue
		}
		rangeHigh, rangeLow := recentRange(bars, i, cfg.RangeBars)

		dir := 0
		if closeP > rangeHigh+cfg.BreakoutATR*atrPct*closeP {
			dir = 1
		} else if closeP < rangeLow-cfg.BreakoutATR*atrPct*closeP {
			dir = -1
		}
		if dir == 0 {
			continue
		}

		up, down, neither := labelAt(bars, i, dir, cfg)
		events = append(events, Event{
			Symbol:    symbol,
			TSMS:      bars[i].OpenTime,
			Direction: dir,
			Features:  featuresAt(bars, btc, i, cfg),
			UpFirst:   up,
			DownFirst: down,
			Neither:   neither,
		})
	}
	return events
}

// labelAt decides the outcome over cfg.HorizonBars after bar i using
// ATR-scaled barriers. The barriers are a multiple of the event-bar ATR, so a
// volatile coin does not share the same arbitrary percentage as a calm one.
func labelAt(bars []binance.Kline, i, dir int, cfg Config) (up, down, neither bool) {
	entry := bars[i].Close
	atrPct := atrPctAt(bars, i, cfg.ATRPeriod)
	if atrPct <= 0 {
		return false, false, true
	}
	upBarrier := entry * (1 + cfg.UpATR*atrPct)
	downBarrier := entry * (1 - cfg.DownATR*atrPct)

	for j := i + 1; j < len(bars) && j <= i+cfg.HorizonBars; j++ {
		hitUp := bars[j].High >= upBarrier
		hitDown := bars[j].Low <= downBarrier
		if hitUp && hitDown {
			// Both touched in one bar: resolve by the bar's direction.
			if bars[j].Close >= entry {
				return true, false, false
			}
			return false, true, false
		}
		if hitUp {
			return true, false, false
		}
		if hitDown {
			return false, true, false
		}
	}
	return false, false, true
}

// featuresAt computes the flat feature map at bar i from bars up to i only.
// Missing inputs (e.g. no BTC history) are simply absent, and the trainer
// imputes them at the train-set mean exactly as the live scorer does.
func featuresAt(bars, btc []binance.Kline, i int, cfg Config) map[string]float64 {
	f := map[string]float64{}
	closeP := bars[i].Close
	if closeP <= 0 {
		return f
	}

	f["ret_1"] = retAt(bars, i, 1)
	f["ret_4"] = retAt(bars, i, 4)
	f["ret_24"] = retAt(bars, i, 24)
	f["ema20_slope"] = emaSlopeAt(bars, i, 20)
	f["ema50_slope"] = emaSlopeAt(bars, i, 50)
	f["rsi14"] = rsiAt(bars, i, 14)
	f["atr_pct"] = atrPctAt(bars, i, cfg.ATRPeriod)
	f["vol_accel"] = volAccelAt(bars, i)
	f["taker_buy_ratio"] = takerBuyRatioAt(bars, i)
	f["range_pos"] = rangePosAt(bars, i, cfg.RangeBars)

	if len(btc) > 24 {
		f["btc_ret_24"] = retAt(btc, len(btc)-1, 24)
	}
	return f
}

// retAt is the price return over the last n bars, as a fraction.
func retAt(bars []binance.Kline, i, n int) float64 {
	if i-n < 0 {
		return 0
	}
	base := bars[i-n].Close
	if base <= 0 {
		return 0
	}
	return (bars[i].Close - base) / base
}

// emaSlopeAt is the current EMA slope normalised by price (a fractional slope
// per bar).
func emaSlopeAt(bars []binance.Kline, i, period int) float64 {
	if i < 2 {
		return 0
	}
	now := emaAt(bars, i, period)
	prev := emaAt(bars, i-1, period)
	if now <= 0 || prev <= 0 {
		return 0
	}
	return (now - prev) / prev
}

// emaAt returns the exponential moving average at bar i.
func emaAt(bars []binance.Kline, i, period int) float64 {
	if period <= 0 || i < 0 || i >= len(bars) {
		return 0
	}
	k := 2.0 / float64(period+1)
	// Seed with the SMA of the first `period` closes before i.
	start := i - period + 1
	if start < 0 {
		start = 0
	}
	ema := bars[start].Close
	for j := start + 1; j <= i; j++ {
		ema = bars[j].Close*k + ema*(1-k)
	}
	return ema
}

// rsiAt is Wilder's RSI over `period` bars.
func rsiAt(bars []binance.Kline, i, period int) float64 {
	if i < period {
		return 50
	}
	var gain, loss float64
	for j := i - period + 1; j <= i; j++ {
		d := bars[j].Close - bars[j-1].Close
		if d >= 0 {
			gain += d
		} else {
			loss -= d
		}
	}
	avgGain := gain / float64(period)
	avgLoss := loss / float64(period)
	if avgLoss == 0 {
		return 100
	}
	rs := avgGain / avgLoss
	return 100 - 100/(1+rs)
}

// atrPctAt is ATR as a fraction of price at bar i.
func atrPctAt(bars []binance.Kline, i, period int) float64 {
	if i < 1 {
		return 0
	}
	closeP := bars[i].Close
	if closeP <= 0 {
		return 0
	}
	return atrAt(bars, i, period) / closeP
}

// atrAt is the median true range over the trailing `period` bars (the median
// is used for the same reason as the forecast package: it ignores one bad
// print).
func atrAt(bars []binance.Kline, i, period int) float64 {
	if i < 1 {
		return 0
	}
	n := period
	if i+1 < n {
		n = i + 1
	}
	trs := make([]float64, 0, n)
	for j := i - n + 1; j <= i; j++ {
		if j < 1 {
			continue
		}
		prev := bars[j-1].Close
		tr := math.Max(bars[j].High-bars[j].Low,
			math.Max(math.Abs(bars[j].High-prev), math.Abs(bars[j].Low-prev)))
		trs = append(trs, tr)
	}
	if len(trs) == 0 {
		return 0
	}
	sort.Float64s(trs)
	return trs[len(trs)/2]
}

// volAccelAt is recent volume versus its own trailing baseline.
func volAccelAt(bars []binance.Kline, i int) float64 {
	if i < 5 {
		return 1
	}
	recent := avgVolume(bars, i-4, i)
	base := avgVolume(bars, i-19, i-5)
	if base <= 0 {
		return 1
	}
	return recent / base
}

// avgVolume is the mean base-asset volume over [a,b] inclusive.
func avgVolume(bars []binance.Kline, a, b int) float64 {
	if a < 0 || b < a || b >= len(bars) {
		return 0
	}
	var sum float64
	for j := a; j <= b; j++ {
		sum += bars[j].Volume
	}
	return sum / float64(b-a+1)
}

// takerBuyRatioAt is the taker-buy share of base volume over the last 5 bars.
func takerBuyRatioAt(bars []binance.Kline, i int) float64 {
	var buy, vol float64
	for j := i - 4; j <= i; j++ {
		if j < 0 {
			continue
		}
		buy += bars[j].TakerBuyBase
		vol += bars[j].Volume
	}
	if vol <= 0 {
		return 0.5
	}
	return buy / vol
}

// rangePosAt is where close sits in the recent [low, high] range (0 at the
// low, 1 at the high).
func rangePosAt(bars []binance.Kline, i, n int) float64 {
	high, low := recentRange(bars, i, n)
	if high <= low {
		return 0.5
	}
	return (bars[i].Close - low) / (high - low)
}

// recentRange is the [high, low] of the n bars immediately BEFORE bar i. The
// breakout bar itself is excluded so its own high cannot define the range it
// is supposed to be breaking out of.
func recentRange(bars []binance.Kline, i, n int) (high, low float64) {
	low = math.Inf(1)
	for j := i - n; j <= i-1; j++ {
		if j < 0 {
			continue
		}
		if bars[j].High > high {
			high = bars[j].High
		}
		if bars[j].Low < low {
			low = bars[j].Low
		}
	}
	if low == math.Inf(1) {
		low = 0
	}
	return high, low
}

// walkForward trains and evaluates with a chronological split. A gap of one
// horizon between the train and holdout periods prevents a train label (which
// looks cfg.HorizonBars into the future) from reaching into the holdout.
func walkForward(events []Event, cfg Config, outDir string) {
	sort.Slice(events, func(a, b int) bool { return events[a].TSMS < events[b].TSMS })

	split := int(float64(len(events)) * cfg.TrainFrac)
	if split < cfg.MinEvents || len(events)-split < cfg.MinEvents {
		fmt.Printf("insufficient sample for split: train=%d holdout=%d need >=%d each\n",
			split, len(events)-split, cfg.MinEvents)
		return
	}
	// Gap: the holdout starts at least one horizon after the last train event.
	gapMS := int64(cfg.HorizonBars) * intervalMS(cfg.Interval)
	lastTrainTS := events[split-1].TSMS
	for split < len(events) && events[split].TSMS < lastTrainTS+gapMS {
		split++
	}
	if split >= len(events) {
		fmt.Println("insufficient holdout after the horizon gap")
		return
	}

	train := events[:split]
	holdout := events[split:]

	upModel, ok := fit(train, func(e Event) int {
		if e.UpFirst {
			return 1
		}
		return 0
	})
	downModel, ok2 := fit(train, func(e Event) int {
		if e.DownFirst {
			return 1
		}
		return 0
	})
	if !ok || !ok2 {
		fmt.Println("fit refused: insufficient samples for one of the models")
		return
	}

	printReport(train, holdout, upModel, downModel, cfg)

	writeCalibration(outDir, cfg, upModel, downModel, len(train), len(holdout))
}

// fit wraps ledger.FitLogistic for a binary labeler.
func fit(events []Event, labeler func(Event) int) (ledger.Model, bool) {
	rows := make([]ledger.FeatureRow, 0, len(events))
	labels := make([]int, 0, len(events))
	for _, e := range events {
		rows = append(rows, ledger.FeatureRow{ID: e.Symbol + "|" + itoa(e.TSMS), Features: e.Features})
		labels = append(labels, labeler(e))
	}
	m, err := ledger.FitLogistic(rows, labels)
	if err != nil {
		fmt.Fprintf(os.Stderr, "research: fit: %v\n", err)
		return m, false
	}
	return m, true
}

// printReport prints the probability-quality report: base rates, in-sample
// fit quality, and holdout log-loss / Brier / precision / recall / calibration.
func printReport(train, holdout []Event, up, down ledger.Model, cfg Config) {
	fmt.Printf("\nlabel rule: horizon=%d bars (%s)  up=+%.1f ATR  down=-%.1f ATR  atr=%d  range=%d\n",
		cfg.HorizonBars, cfg.Interval, cfg.UpATR, cfg.DownATR, cfg.ATRPeriod, cfg.RangeBars)
	fmt.Printf("events: train=%d  holdout=%d  (chronological, horizon-gapped)\n\n", len(train), len(holdout))

	bu, bd, bn := baseRates(holdout)
	fmt.Printf("base rates (holdout): P(up)=%.3f  P(down)=%.3f  P(neither)=%.3f\n", bu, bd, bn)

	fmt.Printf("\nin-sample (train):\n")
	fmt.Printf("  up model:   brier=%.4f log_loss=%.4f base_rate=%.3f\n", up.Brier, up.LogLoss, up.BaseRate)
	fmt.Printf("  down model: brier=%.4f log_loss=%.4f base_rate=%.3f\n", down.Brier, down.LogLoss, down.BaseRate)

	fmt.Printf("\nholdout:\n")
	hlUp := brierLogLoss(holdout, up, func(e Event) int {
		if e.UpFirst {
			return 1
		}
		return 0
	})
	hlDown := brierLogLoss(holdout, down, func(e Event) int {
		if e.DownFirst {
			return 1
		}
		return 0
	})
	fmt.Printf("  up model:   brier=%.4f log_loss=%.4f\n", hlUp.brier, hlUp.logLoss)
	fmt.Printf("  down model: brier=%.4f log_loss=%.4f\n", hlDown.brier, hlDown.logLoss)

	pr := precisionRecall(holdout, up)
	fmt.Printf("\nrally alerts (up model P>=0.50): precision=%.3f recall=%.3f  (tp=%d fp=%d fn=%d)\n",
		pr.precision, pr.recall, pr.tp, pr.fp, pr.fn)

	fmt.Printf("\ncalibration (up model, 10 bands, holdout):\n")
	fmt.Printf("  %6s %6s %10s %10s %6s\n", "lo", "hi", "pred", "obs", "n")
	for _, b := range calibrationBands(holdout, up) {
		fmt.Printf("  %.2f  %.2f  %10.3f %10.3f %6d\n", b.lo, b.hi, b.predicted, b.observed, b.count)
	}
}

type hlPair struct{ brier, logLoss float64 }

// brierLogLoss evaluates a model on a holdout against a binary labeler.
func brierLogLoss(events []Event, m ledger.Model, labeler func(Event) int) hlPair {
	var brier, ll float64
	for _, e := range events {
		p, ok := m.Predict(e.Features)
		if !ok {
			p = m.BaseRate
		}
		y := labeler(e)
		brier += (p - float64(y)) * (p - float64(y))
		p = clamp(p, 1e-6, 1-1e-6)
		if y == 1 {
			ll -= math.Log(p)
		} else {
			ll -= math.Log(1 - p)
		}
	}
	n := float64(len(events))
	if n == 0 {
		return hlPair{}
	}
	return hlPair{brier: brier / n, logLoss: ll / n}
}

type prResult struct {
	precision, recall float64
	tp, fp, fn        int
}

// precisionRecall treats "rally" as P(up)>=0.5 against the up-first label.
func precisionRecall(events []Event, m ledger.Model) prResult {
	var tp, fp, fn int
	for _, e := range events {
		p, ok := m.Predict(e.Features)
		if !ok {
			p = m.BaseRate
		}
		predicted := p >= 0.5
		actual := e.UpFirst
		switch {
		case predicted && actual:
			tp++
		case predicted && !actual:
			fp++
		case !predicted && actual:
			fn++
		}
	}
	den := tp + fp
	prec := 0.0
	if den > 0 {
		prec = float64(tp) / float64(den)
	}
	rec := 0.0
	if tp+fn > 0 {
		rec = float64(tp) / float64(tp+fn)
	}
	return prResult{precision: prec, recall: rec, tp: tp, fp: fp, fn: fn}
}

type calBand struct {
	lo, hi, predicted, observed float64
	count                       int
}

// calibrationBands buckets holdout predictions by predicted P(up) and compares
// the mean prediction with the observed up-first rate.
func calibrationBands(events []Event, m ledger.Model) []calBand {
	bins := make([]calBand, 10)
	for i := range bins {
		bins[i].lo = float64(i) / 10
		bins[i].hi = float64(i+1) / 10
	}
	for _, e := range events {
		p, ok := m.Predict(e.Features)
		if !ok {
			p = m.BaseRate
		}
		idx := int(p * 10)
		if idx < 0 {
			idx = 0
		}
		if idx > 9 {
			idx = 9
		}
		bins[idx].predicted += p
		if e.UpFirst {
			bins[idx].observed++
		}
		bins[idx].count++
	}
	for i := range bins {
		if bins[i].count > 0 {
			bins[i].predicted /= float64(bins[i].count)
			bins[i].observed /= float64(bins[i].count)
		}
	}
	return bins
}

func baseRates(events []Event) (up, down, neither float64) {
	if len(events) == 0 {
		return 0, 0, 0
	}
	var u, d, n int
	for _, e := range events {
		switch {
		case e.UpFirst:
			u++
		case e.DownFirst:
			d++
		default:
			n++
		}
	}
	t := float64(len(events))
	return float64(u) / t, float64(d) / t, float64(n) / t
}

// horizonModel is the serialised output file: two binary logistic models that
// together give P(up), P(down) and P(neither) = 1 - P(up) - P(down).
type horizonModel struct {
	Type        string       `json:"type"`
	Version     int          `json:"version"`
	FittedMS    int64        `json:"fitted_ms"`
	Interval    string       `json:"interval"`
	HorizonBars int          `json:"horizon_bars"`
	UpATR       float64      `json:"up_atr"`
	DownATR     float64      `json:"down_atr"`
	ATRPeriod   int          `json:"atr_period"`
	LabelRule   string       `json:"label_rule"`
	TrainN      int          `json:"train_n"`
	HoldoutN    int          `json:"holdout_n"`
	Up          ledger.Model `json:"up"`
	Down        ledger.Model `json:"down"`
}

// writeCalibration writes the model file and the report, atomically.
func writeCalibration(dir string, cfg Config, up, down ledger.Model, trainN, holdoutN int) {
	hm := horizonModel{
		Type:        "horizon_calibration",
		Version:     1,
		FittedMS:    time.Now().UnixMilli(),
		Interval:    cfg.Interval,
		HorizonBars: cfg.HorizonBars,
		UpATR:       cfg.UpATR,
		DownATR:     cfg.DownATR,
		ATRPeriod:   cfg.ATRPeriod,
		LabelRule: fmt.Sprintf("up=+%.1f*ATR down=-%.1f*ATR over %d bars; UP/DOWN = barrier hit first, NEITHER otherwise",
			cfg.UpATR, cfg.DownATR, cfg.HorizonBars),
		TrainN:   trainN,
		HoldoutN: holdoutN,
		Up:       up,
		Down:     down,
	}
	path := filepath.Join(dir, fmt.Sprintf("calibration_horizon_%s_%db.json", cfg.Interval, cfg.HorizonBars))
	tmp := path + ".tmp"
	data, err := json.MarshalIndent(hm, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "research: marshal: %v\n", err)
		return
	}
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "research: write: %v\n", err)
		return
	}
	if err := os.Rename(tmp, path); err != nil {
		fmt.Fprintf(os.Stderr, "research: rename: %v\n", err)
		return
	}
	fmt.Printf("\nmodel written: %s\n", path)
}

// intervalMS maps a Binance interval string to its bar length in milliseconds.
func intervalMS(iv string) int64 {
	switch iv {
	case "1m":
		return 60_000
	case "5m":
		return 5 * 60_000
	case "15m":
		return 15 * 60_000
	case "30m":
		return 30 * 60_000
	case "1h":
		return 60 * 60_000
	case "4h":
		return 4 * 60 * 60_000
	case "1d":
		return 24 * 60 * 60_000
	default:
		return 60 * 60_000
	}
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func itoa(n int64) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
