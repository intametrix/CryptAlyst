// Package screen answers "what is pumping or dumping right now?" in a single
// pass over 1-minute klines.
//
// The live onset engine catches a move as it starts; this is the complementary
// survey. Because it works from klines it gets exact volume and taker-side
// data (the websocket ticker stream carries no taker volume), so every row is a
// self-contained verdict rather than a running estimate.
package screen

import (
	"context"
	"math"
	"sort"
	"sync"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/market"
)

// Kind classifies a candidate.
type Kind string

// Candidate kinds.
const (
	Pump         Kind = "PUMP"
	Dump         Kind = "DUMP"
	ReversalDump Kind = "REVERSAL_DUMP"
	ReversalPump Kind = "REVERSAL_PUMP"
	Quiet        Kind = "QUIET"
)

// windowBars is the "recent" measurement window, in 1m bars.
const windowBars = 5

// DefaultKlineLimit is three hours of 1-minute bars.
const DefaultKlineLimit = 180

// Thresholds are the screen's gates.
type Thresholds struct {
	MinMove5m         float64 `json:"min_move_5m"`
	MinMove15m        float64 `json:"min_move_15m"`
	MinVolRatio       float64 `json:"min_vol_ratio"`
	MinVolZ           float64 `json:"min_vol_z"`
	MinRecentUSD      float64 `json:"min_recent_usd"`
	MinQuoteVolume24h float64 `json:"min_quote_volume_24h"`
	ReversalPriorPct  float64 `json:"reversal_prior_pct"`
	ReversalFastPct   float64 `json:"reversal_fast_pct"`
	ReversalVolRatio  float64 `json:"reversal_vol_ratio"`
	WickFadeRatio     float64 `json:"wick_fade_ratio"`
}

// DefaultThresholds returns the tuned defaults.
func DefaultThresholds() Thresholds {
	return Thresholds{
		MinMove5m:         0.8,
		MinMove15m:        1.5,
		MinVolRatio:       1.8,
		MinVolZ:           1.2,
		MinRecentUSD:      5_000,
		MinQuoteVolume24h: 50_000,
		ReversalPriorPct:  4.0,
		ReversalFastPct:   2.0,
		ReversalVolRatio:  1.2,
		WickFadeRatio:     0.5,
	}
}

// Row is one symbol's pump/dump verdict.
type Row struct {
	Symbol              string   `json:"symbol"`
	Kind                Kind     `json:"kind"`
	Score               float64  `json:"score"`
	Price               float64  `json:"price"`
	Ret5m               *float64 `json:"ret_5m"`
	Ret15m              *float64 `json:"ret_15m"`
	Ret60m              *float64 `json:"ret_60m"`
	Ret24h              *float64 `json:"ret_24h"`
	VolRatio            float64  `json:"vol_ratio"`
	VolZ                float64  `json:"vol_z"`
	RecentQuoteVolume   float64  `json:"recent_quote_volume"`
	BaselineQuoteVolume float64  `json:"baseline_quote_volume"`
	QuoteVolume24h      float64  `json:"quote_volume_24h"`
	TradesRatio         float64  `json:"trades_ratio"`
	TakerBuyRatio       float64  `json:"taker_buy_ratio"`
	ClosePosition       float64  `json:"close_position"`
	RangePct            float64  `json:"range_pct"`
	ATRPct              float64  `json:"atr_pct"`
	From24hHigh         *float64 `json:"from_24h_high"`
	From24hLow          *float64 `json:"from_24h_low"`
	BarsSinceExtreme    *int     `json:"bars_since_extreme"`
	Reasons             []string `json:"reasons"`
}

// Direction returns +1 for an upward move, -1 otherwise.
func (r Row) Direction() int {
	if r.Kind == Pump || r.Kind == ReversalPump {
		return 1
	}
	return -1
}

// Screener fetches and analyses the market.
type Screener struct {
	client     *binance.Client
	universe   *market.Universe
	thresholds Thresholds
	topN       int
	klineLimit int
	interval   string
	workers    int

	mu       sync.RWMutex
	lastRows []Row
	lastMS   int64
	scanned  int
}

// New creates a screener.
func New(client *binance.Client, universe *market.Universe, thresholds Thresholds, topN, klineLimit, workers int, interval string) *Screener {
	if klineLimit <= 0 {
		klineLimit = DefaultKlineLimit
	}
	if workers < 1 {
		workers = 8
	}
	if interval == "" {
		interval = "1m"
	}
	return &Screener{
		client:     client,
		universe:   universe,
		thresholds: thresholds,
		topN:       topN,
		klineLimit: klineLimit,
		interval:   interval,
		workers:    workers,
	}
}

// Thresholds returns the active thresholds.
func (s *Screener) Thresholds() Thresholds {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.thresholds
}

// SetThresholds updates the detection thresholds live.
func (s *Screener) SetThresholds(th Thresholds) {
	s.mu.Lock()
	s.thresholds = th
	s.mu.Unlock()
}

// SetTopN updates how many symbols the screen scans, live.
func (s *Screener) SetTopN(n int) {
	s.mu.Lock()
	if n > 0 {
		s.topN = n
	}
	s.mu.Unlock()
}

func (s *Screener) snapshot() (Thresholds, int) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.thresholds, s.topN
}

// Cached returns the most recent result set.
func (s *Screener) Cached() ([]Row, int64, int) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Row, len(s.lastRows))
	copy(out, s.lastRows)
	return out, s.lastMS, s.scanned
}

// Run performs one screening pass over the top-N liquid symbols.
func (s *Screener) Run(ctx context.Context) ([]Row, int, error) {
	th, topN := s.snapshot()
	symbols := s.universe.Tracked()
	if topN > 0 && len(symbols) > topN {
		symbols = symbols[:topN]
	}
	if len(symbols) == 0 {
		return nil, 0, nil
	}

	type result struct {
		row Row
		ok  bool
	}
	results := make([]result, len(symbols))
	pool := binance.NewPool(s.workers)

	for i, symbol := range symbols {
		i, symbol := i, symbol
		pool.Go(func() {
			klines, err := s.client.Klines(ctx, symbol, s.interval, s.klineLimit)
			if err != nil || len(klines) == 0 {
				return
			}
			ctx24, _ := s.universe.Get(symbol)
			row, ok := Analyse(symbol, klines, th, ctx24.QuoteVolume24h, ctx24.PriceChangePct/100)
			if ok {
				results[i] = result{row: row, ok: true}
			}
		})
	}
	pool.Wait()

	rows := make([]Row, 0, len(results))
	for _, r := range results {
		if r.ok {
			rows = append(rows, r.row)
		}
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Score > rows[j].Score })

	s.mu.Lock()
	s.lastRows = rows
	s.lastMS = nowMS()
	s.scanned = len(symbols)
	s.mu.Unlock()
	return rows, len(symbols), nil
}

// Analyse computes the pump/dump verdict for one symbol.
//
// Returns ok=false when the symbol does not clear the gates.
func Analyse(symbol string, bars []binance.Kline, th Thresholds, quoteVolume24h, ret24h float64) (Row, bool) {
	// Need history for returns, the baseline, and a 24h high/low scan.
	if len(bars) < 65 {
		return Row{}, false
	}
	// Drop the in-progress minute so the window is made of completed bars.
	completed := bars[:len(bars)-1]
	if len(completed) < 60 {
		return Row{}, false
	}
	last := bars[len(bars)-1]
	price := last.Close
	if price <= 0 {
		return Row{}, false
	}

	row := Row{Symbol: symbol, Kind: Quiet, Price: price}
	if quoteVolume24h <= 0 {
		quoteVolume24h = sumQuote(bars)
	}
	row.QuoteVolume24h = quoteVolume24h
	if ret24h != 0 {
		v := ret24h
		row.Ret24h = &v
	}

	r5 := pctChange(price, completed[len(completed)-5].Close)
	r15 := pctChange(price, completed[len(completed)-15].Close)
	r60 := pctChange(price, completed[len(completed)-60].Close)
	row.Ret5m, row.Ret15m, row.Ret60m = &r5, &r15, &r60

	// --- volume vs the symbol's own median 5-minute volume ----------------
	recentBars := completed[len(completed)-windowBars:]
	for _, b := range recentBars {
		row.RecentQuoteVolume += b.QuoteVolume
	}
	older := completed[:len(completed)-windowBars]
	chunks := make([]float64, 0, len(older)/windowBars+1)
	for i := 0; i+windowBars <= len(older); i += windowBars {
		var sum float64
		for _, b := range older[i : i+windowBars] {
			sum += b.QuoteVolume
		}
		chunks = append(chunks, sum)
	}
	baseline := median(chunks)
	spread := stdev(chunks)
	// Floor from the 24h average 5-minute volume: a symbol that was quiet all
	// day must not produce a 500x ratio off a near-zero divisor.
	floor := (row.QuoteVolume24h / 288.0) * 0.25
	reference := math.Max(baseline, floor)
	row.BaselineQuoteVolume = reference
	if reference > 0 {
		row.VolRatio = row.RecentQuoteVolume / reference
	}
	if spread > 0 && len(chunks) > 0 {
		var sum float64
		for _, c := range chunks {
			sum += c
		}
		row.VolZ = (row.RecentQuoteVolume - sum/float64(len(chunks))) / spread
	} else {
		row.VolZ = row.VolRatio
	}

	// --- taker aggression and bar structure -------------------------------
	var totalQuote, takerBuy float64
	for _, b := range recentBars {
		totalQuote += b.QuoteVolume
		takerBuy += b.TakerBuyQuote
	}
	row.TakerBuyRatio = 0.5
	if totalQuote > 0 {
		row.TakerBuyRatio = takerBuy / totalQuote
	}

	window := append(append([]binance.Kline{}, recentBars...), last)
	high, low := window[0].High, window[0].Low
	for _, b := range window {
		high = math.Max(high, b.High)
		low = math.Min(low, b.Low)
	}
	if low > 0 {
		row.RangePct = (high - low) / low
	}
	if high > low {
		row.ClosePosition = (price - low) / (high - low)
	} else {
		row.ClosePosition = 0.5
	}

	var recentTrades int64
	for _, b := range recentBars {
		recentTrades += b.Trades
	}
	tradeChunks := make([]float64, 0, len(older)/windowBars+1)
	for i := 0; i+windowBars <= len(older); i += windowBars {
		var sum float64
		for _, b := range older[i : i+windowBars] {
			sum += float64(b.Trades)
		}
		tradeChunks = append(tradeChunks, sum)
	}
	if med := median(tradeChunks); med > 0 {
		row.TradesRatio = float64(recentTrades) / med
	}

	// --- ATR% as a volatility sanity check --------------------------------
	ranges := make([]float64, 0, len(bars)-1)
	for i := 1; i < len(bars); i++ {
		ranges = append(ranges, math.Max(
			bars[i].High-bars[i].Low,
			math.Max(
				math.Abs(bars[i].High-bars[i-1].Close),
				math.Abs(bars[i].Low-bars[i-1].Close),
			),
		))
	}
	if len(ranges) > 0 {
		n := 20
		if len(ranges) < n {
			n = len(ranges)
		}
		var sum float64
		for _, r := range ranges[len(ranges)-n:] {
			sum += r
		}
		if price > 0 {
			row.ATRPct = (sum / float64(n)) / price
		}
	}

	// --- 24h location ------------------------------------------------------
	lookback := bars
	if len(bars) > 1440 {
		lookback = bars[len(bars)-1440:]
	}
	high24, low24 := lookback[0].High, lookback[0].Low
	extremeIndex := 0
	for i, b := range lookback {
		if b.High > high24 {
			high24 = b.High
			extremeIndex = i
		}
		if b.Low < low24 {
			low24 = b.Low
		}
	}
	if high24 > 0 {
		v := pctChange(price, high24)
		row.From24hHigh = &v
	}
	if low24 > 0 {
		v := pctChange(price, low24)
		row.From24hLow = &v
	}
	since := len(lookback) - 1 - extremeIndex
	row.BarsSinceExtreme = &since

	if !classify(&row, th) {
		return Row{}, false
	}
	return row, true
}

// classify assigns a kind and score. Returns false if the row fails a gate.
//
// The kind is computed in a local variable and only committed once every gate
// has passed: assigning row.Kind up front means an early return from a gate
// leaves the row labelled PUMP with score 0, which then leaks out as a "hit".
func classify(row *Row, th Thresholds) bool {
	reasons := make([]string, 0, 6)
	components := make(map[string]float64, 5)

	r5, r15, r60 := *row.Ret5m, *row.Ret15m, *row.Ret60m

	if row.QuoteVolume24h < th.MinQuoteVolume24h {
		return false
	}
	if row.RecentQuoteVolume < th.MinRecentUSD {
		return false
	}

	kind := Quiet

	// --- reversal legs ----------------------------------------------------
	if r60 > th.ReversalPriorPct/100 && r5 < -th.ReversalFastPct/100 {
		kind = ReversalDump
		reasons = append(reasons, "gave back "+signedPct(r5)+" in 5m after running "+signedPct(r60)+" over 1h")
	} else if r60 < -th.ReversalPriorPct/100 && r5 > th.ReversalFastPct/100 {
		kind = ReversalPump
		reasons = append(reasons, "bounced "+signedPct(r5)+" in 5m after falling "+signedPct(r60)+" over 1h")
	}

	if kind == Quiet {
		// --- fresh pump / dump --------------------------------------------
		switch {
		case r5 >= th.MinMove5m/100 || r15 >= th.MinMove15m/100:
			kind = Pump
		case r5 <= -th.MinMove5m/100 || r15 <= -th.MinMove15m/100:
			kind = Dump
		default:
			return false
		}
		// A directional move must be backed by abnormal volume.
		if row.VolRatio < th.MinVolRatio {
			return false
		}
		if row.VolZ < th.MinVolZ && row.VolRatio < 2*th.MinVolRatio {
			return false
		}
	} else if row.VolRatio < th.ReversalVolRatio {
		return false
	}

	row.Kind = kind
	direction := row.Direction()

	// --- scoring ----------------------------------------------------------
	moveMagnitude := math.Max(math.Abs(r5), math.Abs(r15)/1.5)
	moveGate := math.Max(th.MinMove5m, 0.01) / 100
	components["move"] = 30 * math.Min(1, moveMagnitude/(moveGate*3))

	components["volume"] = 30 * math.Min(1, row.VolRatio/(th.MinVolRatio*4))

	alignedTaker := row.TakerBuyRatio
	if direction < 0 {
		alignedTaker = 1 - row.TakerBuyRatio
	}
	components["aggression"] = 20 * clampUnit((alignedTaker-0.5)/0.25)

	alignedClose := row.ClosePosition
	if direction < 0 {
		alignedClose = 1 - row.ClosePosition
	}
	components["structure"] = 12 * clampUnit((alignedClose-0.4)/0.5)
	components["liquidity"] = 8 * clampUnit((math.Log10(math.Max(row.QuoteVolume24h, 1))-6)/1.5)

	reasons = append(reasons, "5m "+signedPct(r5)+" / 15m "+signedPct(r15)+" / 1h "+signedPct(r60))
	reasons = append(reasons, "volume "+f2(row.VolRatio)+"x its 5m median ($"+human(row.RecentQuoteVolume)+" in 5m, "+f1(row.VolZ)+"sigma)")
	side := "buy"
	if direction < 0 {
		side = "sell"
	}
	reasons = append(reasons, "taker "+side+" "+f0(alignedTaker*100)+"% of volume")
	reasons = append(reasons, "closed at "+f0(row.ClosePosition*100)+"% of the 5m range, ATR "+f2(row.ATRPct*100)+"%")
	if row.BarsSinceExtreme != nil {
		reasons = append(reasons, "24h extreme was "+itoa(*row.BarsSinceExtreme)+" min ago (high "+
			signedPct(deref(row.From24hHigh))+" low "+signedPct(deref(row.From24hLow))+")")
	}

	// A pump that closed on its low is being sold into.
	adverseWick := 1 - row.ClosePosition
	if direction < 0 {
		adverseWick = row.ClosePosition
	}
	if adverseWick >= th.WickFadeRatio {
		components["structure"] *= 0.4
		sideName := "high"
		if direction < 0 {
			sideName = "low"
		}
		reasons = append(reasons, "fading: closed "+f0(adverseWick*100)+"% away from the "+sideName)
	}

	var score float64
	for _, v := range components {
		score += v
	}
	row.Score = math.Max(0, math.Min(100, score))
	row.Reasons = reasons
	return true
}
