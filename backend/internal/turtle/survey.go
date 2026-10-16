package turtle

import (
	"context"
	"fmt"
	"log"
	"math"
	"sort"
	"sync"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/market"
)

// DefaultTimeframes is the survey horizon set. Turtle was a daily system; the
// shorter frames are used for cross-timeframe alignment rather than as
// independent entry signals.
var DefaultTimeframes = []string{"1d", "4h", "1h"}

// Snapshot is one symbol on one timeframe.
type Snapshot struct {
	Symbol    string  `json:"symbol"`
	Timeframe string  `json:"timeframe"`
	Close     float64 `json:"close"`
	ATR       float64 `json:"atr"`
	ATRPct    float64 `json:"atr_pct"`
	ATRRank   float64 `json:"atr_rank"`

	Upper20 float64 `json:"upper_20"`
	Upper55 float64 `json:"upper_55"`
	Lower20 float64 `json:"lower_20"`
	Lower55 float64 `json:"lower_55"`

	Breakout20Up   bool `json:"breakout_20_up"`
	Breakout55Up   bool `json:"breakout_55_up"`
	Breakout20Down bool `json:"breakout_20_down"`
	Breakout55Down bool `json:"breakout_55_down"`
	ExitLong       bool `json:"exit_long"`
	ExitShort      bool `json:"exit_short"`

	ExitLow10  float64 `json:"exit_low_10"`
	ExitHigh10 float64 `json:"exit_high_10"`

	ADX     float64 `json:"adx"`
	PlusDI  float64 `json:"plus_di"`
	MinusDI float64 `json:"minus_di"`
	RSI     float64 `json:"rsi"`

	ExtensionUpATR   float64 `json:"extension_up_atr"`
	ExtensionDownATR float64 `json:"extension_down_atr"`

	// Volume context for the breakout bar. VolumeRatio is the breakout bar's
	// quote volume over the median of the preceding VolumeMedianWindow bars.
	BreakoutVolume  float64 `json:"breakout_volume"`
	MedianVolume    float64 `json:"median_volume"`
	VolumeRatio     float64 `json:"volume_ratio"`
	VolumeConfirmed bool    `json:"volume_confirmed"`

	QuoteVolume24h float64 `json:"quote_volume_24h"`
	Bars           int     `json:"bars"`
}

// Direction returns +1 for a long signal, -1 for a short signal, 0 for none.
func (s Snapshot) Direction() int {
	if s.Breakout55Up || s.Breakout20Up {
		return 1
	}
	if s.Breakout55Down || s.Breakout20Down {
		return -1
	}
	return 0
}

// IsSystem2 reports whether this is a 55-bar (System 2) breakout.
func (s Snapshot) IsSystem2() bool { return s.Breakout55Up || s.Breakout55Down }

// ComputeSnapshot evaluates the final bar of `bars`.
func ComputeSnapshot(symbol, timeframe string, bars []binance.Kline, p Params, quoteVolume24h float64) (Snapshot, bool) {
	// Completed bars only: drop the still-forming candle before any indicator
	// or gate sees it.
	bars = dropFormingBar(bars)
	if len(bars) < p.MinimumBars() {
		return Snapshot{}, false
	}
	last := bars[len(bars)-1]
	if last.Close <= 0 {
		return Snapshot{}, false
	}
	snap := Snapshot{
		Symbol:         symbol,
		Timeframe:      timeframe,
		Close:          last.Close,
		Bars:           len(bars),
		QuoteVolume24h: quoteVolume24h,
		ADX:            math.NaN(),
		PlusDI:         math.NaN(),
		MinusDI:        math.NaN(),
		RSI:            math.NaN(),
	}

	atrSeries := ATR(bars, p.ATRPeriod)
	if len(atrSeries) == 0 || math.IsNaN(atrSeries[len(atrSeries)-1]) {
		return Snapshot{}, false
	}
	snap.ATR = atrSeries[len(atrSeries)-1]
	if snap.ATR <= 0 {
		return Snapshot{}, false
	}
	snap.ATRPct = snap.ATR / last.Close

	atrPcts := make([]float64, 0, len(atrSeries))
	for i, v := range atrSeries {
		if !math.IsNaN(v) && bars[i].Close > 0 {
			atrPcts = append(atrPcts, v/bars[i].Close)
		}
	}
	snap.ATRRank = PercentileRank(atrPcts, snap.ATRPct)

	fast := DonchianChannel(bars, p.EntryPeriod)
	slow := DonchianChannel(bars, p.EntryPeriodSlow)
	exitFast := DonchianChannel(bars, p.ExitPeriod)

	i := len(bars) - 1
	snap.Upper20 = fast.Upper[i]
	snap.Lower20 = fast.Lower[i]
	snap.Upper55 = slow.Upper[i]
	snap.Lower55 = slow.Lower[i]
	snap.ExitLow10 = exitFast.Lower[i]
	snap.ExitHigh10 = exitFast.Upper[i]

	level20Up := fast.BreakoutUpper[i]
	level20Down := fast.BreakoutLower[i]
	level55Up := slow.BreakoutUpper[i]
	level55Down := slow.BreakoutLower[i]

	snap.Breakout20Up = !math.IsNaN(level20Up) && last.Close > level20Up
	snap.Breakout20Down = !math.IsNaN(level20Down) && last.Close < level20Down
	snap.Breakout55Up = !math.IsNaN(level55Up) && last.Close > level55Up
	snap.Breakout55Down = !math.IsNaN(level55Down) && last.Close < level55Down
	snap.ExitLong = !math.IsNaN(snap.ExitLow10) && last.Close < snap.ExitLow10
	snap.ExitShort = !math.IsNaN(snap.ExitHigh10) && last.Close > snap.ExitHigh10

	adxResult := ADX(bars, p.ADXPeriod)
	snap.ADX = adxResult.ADX[i]
	snap.PlusDI = adxResult.PlusDI[i]
	snap.MinusDI = adxResult.MinusDI[i]
	if rsi := RSI(bars, 14); len(rsi) > i {
		snap.RSI = rsi[i]
	}

	if extension := (last.Close - level20Up) / snap.ATR; !math.IsNaN(extension) {
		snap.ExtensionUpATR = extension
	}
	if extension := (level20Down - last.Close) / snap.ATR; !math.IsNaN(extension) {
		snap.ExtensionDownATR = extension
	}

	// --- volume confirmation ---------------------------------------------
	// The breakout bar versus the median of the bars before it. Median beats
	// mean here because one prior spike would otherwise raise the bar for every
	// subsequent breakout.
	snap.BreakoutVolume = last.QuoteVolume
	window := p.VolumeMedianWindow
	if window < 2 {
		window = 20
	}
	start := len(bars) - 1 - window
	if start < 0 {
		start = 0
	}
	// Include zero-volume bars. A bar with no trades is a real observation of
	// zero volume, not missing data, and dropping it biases the median upward
	// and shrinks the sample -- on a symbol with one active bar in 20 you would
	// be taking a "median" of one. The absolute money floor is what protects
	// against a symbol whose median is legitimately zero.
	prior := make([]float64, 0, window)
	for _, bar := range bars[start : len(bars)-1] {
		prior = append(prior, bar.QuoteVolume)
	}
	snap.MedianVolume = medianOf(prior)
	if snap.MedianVolume > 0 {
		snap.VolumeRatio = snap.BreakoutVolume / snap.MedianVolume
	} else {
		snap.VolumeRatio = 0
	}
	// Floor the DIVISOR (not the numerator), then apply an absolute money
	// floor as well. Both matter: flooring the divisor stops a near-zero
	// median from making any ratio look large, and the absolute floor stops a
	// dust-volume bar from confirming off a dust-volume history. The gate
	// therefore FAILS CLOSED -- a quiet symbol's breakout is rejected rather
	// than passed, which is exactly the wick-hunt this gate exists to kill.
	divisor := p.VolumeMedianFloorUSD
	if snap.MedianVolume > divisor {
		divisor = snap.MedianVolume
	}
	if divisor <= 0 {
		divisor = 1
	}
	snap.VolumeConfirmed = snap.BreakoutVolume >= p.VolumeConfirmMult*divisor &&
		snap.BreakoutVolume >= p.VolumeAbsoluteFloorUSD
	return snap, true
}

// SetupScore ranks a setup 0-100.
type SetupScore struct {
	Score      float64            `json:"score"`
	Components map[string]float64 `json:"components"`
	Reasons    []string           `json:"reasons"`
	Rejected   string             `json:"rejected,omitempty"`
}

// ScoreSetup scores a breakout, optionally against other timeframes.
func ScoreSetup(s Snapshot, p Params, alignment []Snapshot) SetupScore {
	reasons := make([]string, 0, 6)
	components := make(map[string]float64, 5)

	direction := s.Direction()
	if direction == 0 {
		return SetupScore{Rejected: "no breakout"}
	}

	// ADX is deliberately NOT a veto. It is smoothed roughly three times over,
	// so it lags precisely the fresh breakouts this system wants to catch, and
	// it reads high during blow-off tops -- the worst entries. It feeds the
	// trend score below instead. The volatility band and the volume
	// confirmation are what actually filter.

	// Volume confirmation. The single highest-value precision lever: an
	// unconfirmed breakout is skipped entirely, because with precision
	// preferred over recall a missed move costs nothing while a false one
	// costs a 2N stop.
	if !s.VolumeConfirmed {
		if s.MedianVolume > 0 {
			return SetupScore{Rejected: fmt.Sprintf(
				"breakout volume %.2fx the %d-bar median, below the %.2fx gate",
				s.VolumeRatio, p.VolumeMedianWindow, p.VolumeConfirmMult)}
		}
		return SetupScore{Rejected: fmt.Sprintf(
			"breakout volume $%.0f below the $%.0f floor (no usable median)",
			s.BreakoutVolume, p.VolumeMedianFloorUSD)}
	}

	// Volatility band: dead below, coin-flip above.
	if p.MinATRPct > 0 && s.ATRPct < p.MinATRPct {
		return SetupScore{Rejected: fmt.Sprintf("ATR %.2f%% below the %.2f%% floor (dead capital)",
			s.ATRPct*100, p.MinATRPct*100)}
	}
	if p.MaxATRPct > 0 && s.ATRPct > p.MaxATRPct {
		return SetupScore{Rejected: fmt.Sprintf("ATR %.2f%% above the %.2f%% ceiling (2N stop is a coin flip)",
			s.ATRPct*100, p.MaxATRPct*100)}
	}

	extension := s.ExtensionUpATR
	if direction < 0 {
		extension = s.ExtensionDownATR
	}
	if extension > p.MaxExtensionATR {
		return SetupScore{Rejected: fmt.Sprintf("already %.2fN beyond the level: chasing", extension)}
	}

	// Breakout quality (max 30): decisive but not run away.
	breakout := 5.0
	if extension > 0 {
		breakout = 30 * clamp01(extension/0.75)
	}
	if s.IsSystem2() {
		breakout += 8
	}
	components["breakout"] = math.Min(breakout, 30)
	systemName := "System 1 (20-bar)"
	if s.IsSystem2() {
		systemName = "System 2 (55-bar)"
	}
	reasons = append(reasons, fmt.Sprintf("%s breakout, %.2fN beyond the level", systemName, extension))
	reasons = append(reasons, fmt.Sprintf("volume %.2fx the %d-bar median (confirmed)",
		s.VolumeRatio, p.VolumeMedianWindow))

	// Trend strength (max 25), soft: ADX and DMI direction both feed it, but
	// neither can reject a confirmed, in-band, high-volume breakout.
	adxPoints := 0.0
	if !math.IsNaN(s.ADX) {
		adxPoints = 25 * clamp01((s.ADX-p.ADXMin)/25)
		if p.ADXMin > 0 && s.ADX < p.ADXMin {
			reasons = append(reasons, fmt.Sprintf(
				"ADX %.1f below %.1f -- scored as range, not vetoed", s.ADX, p.ADXMin))
		}
	}
	diPoints := 0.0
	if !math.IsNaN(s.PlusDI) && !math.IsNaN(s.MinusDI) {
		spread := s.PlusDI - s.MinusDI
		if direction < 0 {
			spread = -spread
		}
		diPoints = 25 * clamp01(spread/30)
	}
	components["trend"] = math.Max(adxPoints, diPoints)
	if !math.IsNaN(s.ADX) {
		reasons = append(reasons, fmt.Sprintf("ADX %.0f, +DI %.0f / -DI %.0f", s.ADX, s.PlusDI, s.MinusDI))
	}

	// Multi-timeframe alignment (max 20).
	aligned, total := 0, 0
	for _, other := range alignment {
		if other.Timeframe == s.Timeframe {
			continue
		}
		total++
		switch d := other.Direction(); {
		case d == direction:
			aligned++
		case d == -direction:
			aligned--
		}
	}
	if total > 0 {
		ratio := math.Max(-1, math.Min(1, float64(aligned)/float64(total)))
		components["alignment"] = ((ratio + 1) / 2) * 20
		reasons = append(reasons, fmt.Sprintf("%d/%d other timeframes agree", aligned, total))
	} else {
		components["alignment"] = 10
	}

	// Volatility quality (max 15): prefer the middle of the symbol's own range,
	// because a breakout on historically dead volatility is usually noise and
	// one on a volatility spike is usually a blow-off.
	components["volatility"] = math.Max(0, 15*(1-math.Abs(s.ATRRank-0.6)/0.6))
	reasons = append(reasons, fmt.Sprintf("ATR %.2f%% of price (rank %.0fth pct)", s.ATRPct*100, s.ATRRank*100))

	// Liquidity (max 10).
	components["liquidity"] = 10 * clamp01((math.Log10(math.Max(s.QuoteVolume24h, 1))-5)/2)
	reasons = append(reasons, fmt.Sprintf("24h volume $%.1fM", s.QuoteVolume24h/1e6))

	var score float64
	for _, v := range components {
		score += v
	}
	return SetupScore{
		Score:      math.Max(0, math.Min(100, score)),
		Components: components,
		Reasons:    reasons,
	}
}

// PositionPlan is a sized Turtle position.
type PositionPlan struct {
	Entry          float64   `json:"entry"`
	Stop           float64   `json:"stop"`
	ATR            float64   `json:"atr"`
	UnitSize       float64   `json:"unit_size"`
	UnitNotional   float64   `json:"unit_notional"`
	Units          int       `json:"units"`
	TotalNotional  float64   `json:"total_notional"`
	TotalRisk      float64   `json:"total_risk"`
	PyramidLevels  []float64 `json:"pyramid_levels"`
	ExitLevel      float64   `json:"exit_level"`
	NotionalCapped bool      `json:"notional_capped"`
}

// BuildPlan sizes a position: Unit = (risk% x equity) / N.
//
// The defining property of Turtle sizing is that a 1N adverse move costs the
// same dollar amount regardless of market, which is what makes positions
// comparable across instruments of wildly different volatility.
func BuildPlan(s Snapshot, p Params, equity float64, units int, exitLevel float64) *PositionPlan {
	if s.ATR <= 0 || equity <= 0 || s.Direction() == 0 {
		return nil
	}
	direction := s.Direction()
	entry := s.Close
	dollarRisk := equity * p.RiskPerUnit
	unitSize := dollarRisk / s.ATR
	unitNotional := unitSize * entry

	capped := false
	if cap := equity * p.MaxNotionalPct; cap > 0 && unitNotional > cap {
		unitSize = cap / entry
		unitNotional = cap
		capped = true
	}

	stop := entry - p.StopATR*s.ATR
	if direction < 0 {
		stop = entry + p.StopATR*s.ATR
	}
	if stop <= 0 {
		stop = entry * 0.5
	}

	ladder := make([]float64, 0, p.PyramidMaxUnits-1)
	for step := 1; step < p.PyramidMaxUnits; step++ {
		offset := float64(step) * p.PyramidStepATR * s.ATR
		if direction > 0 {
			ladder = append(ladder, entry+offset)
		} else {
			ladder = append(ladder, entry-offset)
		}
	}
	if units < 1 {
		units = 1
	}
	return &PositionPlan{
		Entry:          entry,
		Stop:           stop,
		ATR:            s.ATR,
		UnitSize:       unitSize,
		UnitNotional:   unitNotional,
		Units:          units,
		TotalNotional:  unitNotional * float64(units),
		TotalRisk:      unitSize * s.ATR * float64(units),
		PyramidLevels:  ladder,
		ExitLevel:      exitLevel,
		NotionalCapped: capped,
	}
}

// Budget enforces the Turtle portfolio limits.
type Budget struct {
	params Params
	units  map[string]int
	groups map[string]int
	long   int
	short  int
	total  int

	Notional float64
	Risk     float64
	Accepted int
	Rejected int
}

// NewBudget creates an empty budget.
func NewBudget(p Params) *Budget {
	return &Budget{
		params: p,
		units:  make(map[string]int),
		groups: make(map[string]int),
	}
}

// Check returns a rejection reason, or "" if the trade fits.
func (b *Budget) Check(symbol, group string, direction, units int) string {
	if b.units[symbol]+units > b.params.MaxUnitsPerMarket {
		return fmt.Sprintf("market limit: %d/%d units already in %s", b.units[symbol], b.params.MaxUnitsPerMarket, symbol)
	}
	if b.groups[group]+units > b.params.MaxUnitsCorrelated {
		return fmt.Sprintf("correlated limit: group %q at %d/%d units", group, b.groups[group], b.params.MaxUnitsCorrelated)
	}
	dirUnits, dirName := b.long, "long"
	if direction < 0 {
		dirUnits, dirName = b.short, "short"
	}
	if dirUnits+units > b.params.MaxUnitsDirection {
		return fmt.Sprintf("direction limit: %d/%d units already %s", dirUnits, b.params.MaxUnitsDirection, dirName)
	}
	if b.total+units > b.params.MaxUnitsTotal {
		return fmt.Sprintf("total limit: %d/%d units", b.total, b.params.MaxUnitsTotal)
	}
	return ""
}

// Accept records an allocation.
func (b *Budget) Accept(symbol, group string, direction, units int, notional, risk float64) {
	b.units[symbol] += units
	b.groups[group] += units
	if direction > 0 {
		b.long += units
	} else {
		b.short += units
	}
	b.total += units
	b.Notional += notional
	b.Risk += risk
	b.Accepted++
}

// Summary renders the budget for the API.
func (b *Budget) Summary() map[string]any {
	return map[string]any{
		"total_units":    b.total,
		"long_units":     b.long,
		"short_units":    b.short,
		"total_notional": b.Notional,
		"total_risk":     b.Risk,
		"positions":      b.Accepted,
		"rejected":       b.Rejected,
	}
}

// --------------------------------------------------------------------------
// Survey
// --------------------------------------------------------------------------

// Setup is one ranked survey row.
type Setup struct {
	Snapshot     Snapshot      `json:"-"`
	Symbol       string        `json:"symbol"`
	Timeframe    string        `json:"timeframe"`
	Direction    int           `json:"direction"`
	Score        float64       `json:"score"`
	Close        float64       `json:"close"`
	ATR          float64       `json:"atr"`
	ATRPct       float64       `json:"atr_pct"`
	ADX          float64       `json:"adx"`
	RSI          float64       `json:"rsi"`
	ExtensionATR float64       `json:"extension_up_atr"`
	Group        string        `json:"group"`
	Accepted     bool          `json:"accepted"`
	RejectReason string        `json:"reject_reason"`
	Reasons      []string      `json:"reasons"`
	Plan         *PositionPlan `json:"plan"`
}

// Survey runs the Turtle survey and caches the result.
type Survey struct {
	client     *binance.Client
	universe   *market.Universe
	params     Params
	timeframes []string
	equity     float64
	topN       int
	klineLimit int
	workers    int
	longOnly   bool

	mu      sync.RWMutex
	payload map[string]any
	lastMS  int64
	running bool

	// btcRiskOff latches the BTC regime gate. Entering risk-off is a downside
	// Donchian break; leaving it requires an UPSIDE break of the same period.
	// The two levels differ deliberately, so the gate cannot flap while price
	// sits on the boundary.
	btcRiskOff bool
	btcLatched bool
}

// NewSurvey creates a survey runner.
func NewSurvey(client *binance.Client, universe *market.Universe, params Params, timeframes []string, equity float64, topN, klineLimit, workers int, longOnly bool) *Survey {
	if len(timeframes) == 0 {
		timeframes = DefaultTimeframes
	}
	if klineLimit <= 0 {
		klineLimit = 250
	}
	if workers < 1 {
		workers = 8
	}
	return &Survey{
		client:     client,
		universe:   universe,
		params:     params,
		timeframes: timeframes,
		equity:     equity,
		topN:       topN,
		klineLimit: klineLimit,
		workers:    workers,
		longOnly:   longOnly,
		payload:    map[string]any{"type": "survey", "setups": []any{}, "exit_signals": []any{}},
	}
}

// Params returns the survey's parameter set, for the detector registry and the
// API's parameter display.
func (s *Survey) Params() Params { return s.params }

// Current returns the cached payload.
func (s *Survey) Current() any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.payload
}

// LastMS returns when the survey last completed.
func (s *Survey) LastMS() int64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.lastMS
}

// Refresh runs one survey pass. Concurrent calls are coalesced.
func (s *Survey) Refresh() error {
	s.mu.Lock()
	if s.running {
		s.mu.Unlock()
		return nil
	}
	s.running = true
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.running = false
		s.mu.Unlock()
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	symbols := s.universe.Tracked()
	if s.topN > 0 && len(symbols) > s.topN {
		symbols = symbols[:s.topN]
	}
	if len(symbols) == 0 {
		return nil
	}

	// Fetch every (symbol, timeframe) series concurrently.
	type key struct{ symbol, tf string }
	type series struct {
		bars []binance.Kline
		ok   bool
	}
	results := make(map[key]*series, len(symbols)*len(s.timeframes))
	var resMu sync.Mutex
	pool := binance.NewPool(s.workers)
	for _, symbol := range symbols {
		for _, tf := range s.timeframes {
			symbol, tf := symbol, tf
			pool.Go(func() {
				bars, err := s.client.Klines(ctx, symbol, tf, s.klineLimit)
				resMu.Lock()
				defer resMu.Unlock()
				if err != nil || len(bars) == 0 {
					results[key{symbol, tf}] = &series{}
					return
				}
				results[key{symbol, tf}] = &series{bars: bars, ok: true}
			})
		}
	}
	pool.Wait()

	// Build snapshots per symbol.
	type perSymbol map[string]Snapshot
	snaps := make(map[string]perSymbol, len(symbols))
	returnsBySymbol := make(map[string][]float64)
	primary := s.timeframes[0]
	for symbol, ctx24 := range func() map[string]market.Context {
		m := make(map[string]market.Context, len(symbols))
		for _, sym := range symbols {
			if c, ok := s.universe.Get(sym); ok {
				m[sym] = c
			}
		}
		return m
	}() {
		for _, tf := range s.timeframes {
			entry := results[key{symbol, tf}]
			if entry == nil || !entry.ok {
				continue
			}
			snap, ok := ComputeSnapshot(symbol, tf, entry.bars, s.params, ctx24.QuoteVolume24h)
			if !ok {
				continue
			}
			if snaps[symbol] == nil {
				snaps[symbol] = make(perSymbol)
			}
			snaps[symbol][tf] = snap
		}
		if entry := results[key{symbol, primary}]; entry != nil && entry.ok && len(entry.bars) >= 12 {
			returnsBySymbol[symbol] = Returns(entry.bars)
		}
	}

	clusters := ClusterByCorrelation(returnsBySymbol, symbols, 0.7)

	// Rank.
	setups := make([]Setup, 0, len(snaps))
	rejected := make([]map[string]any, 0, 16)
	exitSignals := make([]map[string]any, 0, 16)
	for _, symbol := range symbols {
		perTF := snaps[symbol]
		if perTF == nil {
			continue
		}
		primarySnap, ok := perTF[primary]
		if !ok {
			continue
		}
		if primarySnap.ExitLong || primarySnap.ExitShort {
			exitSignals = append(exitSignals, map[string]any{
				"symbol":      symbol,
				"close":       primarySnap.Close,
				"exit_long":   primarySnap.ExitLong,
				"exit_short":  primarySnap.ExitShort,
				"exit_low_10": primarySnap.ExitLow10,
				"adx":         primarySnap.ADX,
			})
		}
		direction := primarySnap.Direction()
		if direction == 0 {
			continue
		}
		if s.longOnly && direction < 0 {
			// Spot cannot short: a downside breakout is an exit, not a trade.
			continue
		}
		alignment := make([]Snapshot, 0, len(perTF))
		for tf, snap := range perTF {
			if tf != primary {
				alignment = append(alignment, snap)
			}
		}
		score := ScoreSetup(primarySnap, s.params, alignment)
		if score.Rejected != "" {
			// Keep the reject reason: "why is there no trade on this symbol?"
			// is exactly the question a drill-down has to answer, and dropping
			// the setup entirely made that unanswerable.
			rejected = append(rejected, map[string]any{
				"symbol":    symbol,
				"timeframe": primary,
				"direction": direction,
				"reason":    score.Rejected,
				"close":     primarySnap.Close,
				"atr_pct":   primarySnap.ATRPct,
				"adx":       primarySnap.ADX,
			})
			continue
		}
		extension := primarySnap.ExtensionUpATR
		if direction < 0 {
			extension = primarySnap.ExtensionDownATR
		}
		setups = append(setups, Setup{
			Snapshot:     primarySnap,
			Symbol:       symbol,
			Timeframe:    primary,
			Direction:    direction,
			Score:        score.Score,
			Close:        primarySnap.Close,
			ATR:          primarySnap.ATR,
			ATRPct:       primarySnap.ATRPct,
			ADX:          primarySnap.ADX,
			RSI:          primarySnap.RSI,
			ExtensionATR: extension,
			Group:        clusters[symbol],
			Reasons:      score.Reasons,
		})
	}
	sort.Slice(setups, func(i, j int) bool { return setups[i].Score > setups[j].Score })

	// Allocate in descending score order so the limit binds on the weakest
	// setup rather than on whichever happened to be evaluated last.
	budget := NewBudget(s.params)

	// BTC regime gate. Alt trend quality is dominated by BTC beta, so when BTC
	// itself is breaking down the alt breakouts are mostly beta, not signal.
	// Setups are still reported (they are information) but no units are
	// allocated while the gate is shut.
	btcRegime := s.btcRegime(ctx)
	gateShut := false
	if flag, ok := btcRegime["risk_off"].(bool); ok {
		gateShut = flag
	}

	for i := range setups {
		entry := &setups[i]
		group := entry.Group
		if group == "" {
			group = "unclustered"
		}
		if gateShut {
			entry.RejectReason = fmt.Sprintf(
				"BTC regime gate: %s below its %d-bar low",
				s.params.BTCSymbol, s.params.BTCGatePeriod)
			budget.Rejected++
			continue
		}
		// One unit per setup. Pyramiding is NOT implemented -- see
		// TRADING_PLAN.md section 7. The previous version probed with
		// MaxUnitsPerMarket first, which reads as "reject unless four slots are
		// free" and overstates how tight the risk model actually is.
		reason := budget.Check(entry.Symbol, group, entry.Direction, 1)
		if reason != "" {
			entry.RejectReason = reason
			budget.Rejected++
			continue
		}
		plan := BuildPlan(entry.Snapshot, s.params, s.equity, 1, entry.Snapshot.ExitLow10)
		if plan == nil {
			entry.RejectReason = "could not size the position"
			budget.Rejected++
			continue
		}
		entry.Plan = plan
		entry.Accepted = true
		budget.Accept(entry.Symbol, group, entry.Direction, 1, plan.TotalNotional, plan.TotalRisk)
	}

	setupJSON := make([]map[string]any, 0, len(setups))
	for _, e := range setups {
		setupJSON = append(setupJSON, map[string]any{
			"symbol":           e.Symbol,
			"timeframe":        e.Timeframe,
			"direction":        e.Direction,
			"score":            e.Score,
			"close":            e.Close,
			"atr":              e.ATR,
			"atr_pct":          e.ATRPct,
			"adx":              e.ADX,
			"rsi":              e.RSI,
			"extension_up_atr": e.ExtensionATR,
			"group":            e.Group,
			"accepted":         e.Accepted,
			"reject_reason":    e.RejectReason,
			"reasons":          e.Reasons,
			"plan":             e.Plan,
		})
	}

	payload := map[string]any{
		"type":              "survey",
		"ts_ms":             time.Now().UnixMilli(),
		"equity":            s.equity,
		"timeframes":        s.timeframes,
		"symbols_scanned":   len(symbols),
		"symbols_with_data": len(snaps),
		"budget":            budget.Summary(),
		"setups":            setupJSON,
		"rejected":          rejected,
		"exit_signals":      exitSignals,
		"btc_regime":        btcRegime,
		"params":            s.params,
	}

	s.mu.Lock()
	s.payload = payload
	s.lastMS = time.Now().UnixMilli()
	s.mu.Unlock()
	log.Printf("survey: %d setups from %d symbols (%d allocated, btc_gate_risk_off=%v)",
		len(setups), len(snaps), budget.Accepted, gateShut)
	return nil
}

func clamp01(v float64) float64 { return math.Max(0, math.Min(1, v)) }
