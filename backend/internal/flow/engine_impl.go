package flow

import (
	"encoding/json"
	"math"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// Tick is one reduced @ticker snapshot.
type Tick struct {
	TS       int64
	Price    float64
	QuoteVol float64
	BaseVol  float64
	Trades   int64
	Bid      float64
	Ask      float64
	BidQty   float64
	AskQty   float64

	// Differenced against the previous tick.
	DQuote  float64
	DBase   float64
	DTrades int64
	DTMs    int64
}

// Payloads are decoded with EXACT keys, not struct tags, and that is a
// deliberate correctness decision rather than a style choice.
//
// Go's encoding/json matches object keys to field tags CASE-INSENSITIVELY. A
// Binance @ticker payload contains both "e" (event type, a string) and "E"
// (event time, a number), both "q" (24h quote volume, the measurement this
// whole engine divides by) and "Q" (best-bid quantity), and both "c"/"C" and
// "l"/"L". Decoding into a tagged struct therefore produced two failures at
// once:
//
//  1. "e" collided with a field tagged `json:"E"`, so json.Unmarshal returned
//     a type error and EVERY tick was discarded -- the scanner connected,
//     received thousands of frames, and ingested none of them.
//  2. Even when the error was ignored, "Q" matched the field tagged `json:"q"`
//     and silently overwrote the 24h quote volume with the top-of-book bid
//     size, which would have corrupted every baseline and liquidity floor.
//
// Extracting exact keys removes the entire class of bug, and the per-message
// cost is irrelevant next to that.
var (
	keyEventTime  = []byte("E")
	keySymbol     = []byte("s")
	keyClose      = []byte("c")
	keyHigh       = []byte("h")
	keyLow        = []byte("l")
	keyVolume     = []byte("v")
	keyQuoteVol   = []byte("q")
	keyTrades     = []byte("n")
	keyChangePct  = []byte("P")
	keyBid        = []byte("b")
	keyBidQty     = []byte("B")
	keyAsk        = []byte("a")
	keyAskQty     = []byte("A")
	keyPrice      = []byte("p")
	keyQuantity   = []byte("q")
	keyTradeTime  = []byte("T")
	keyBuyerMaker = []byte("m")
)

// --------------------------------------------------------------------------
// Baseline
// --------------------------------------------------------------------------

type baseline struct {
	capacity    int
	capMultiple float64
	floor       float64

	order  []float64 // FIFO, for eviction
	sorted []float64 // ascending, for exact quantiles
	sum    float64
	sumsq  float64
}

func newBaseline(capacity int, capMultiple float64) *baseline {
	if capacity < 8 {
		capacity = 8
	}
	return &baseline{
		capacity:    capacity,
		capMultiple: capMultiple,
		order:       make([]float64, 0, capacity),
		sorted:      make([]float64, 0, capacity),
	}
}

// add stores a sample capped against contamination, and reports what was kept.
func (b *baseline) add(sample float64) float64 {
	if sample < 0 || math.IsNaN(sample) || math.IsInf(sample, 0) {
		return 0
	}
	stored := sample
	if limit := math.Max(b.reference()*b.capMultiple, b.floor); limit > 0 && sample > limit {
		stored = limit
	}
	b.order = append(b.order, stored)
	idx := sort.SearchFloat64s(b.sorted, stored)
	b.sorted = append(b.sorted, 0)
	copy(b.sorted[idx+1:], b.sorted[idx:])
	b.sorted[idx] = stored
	b.sum += stored
	b.sumsq += stored * stored

	for len(b.order) > b.capacity {
		old := b.order[0]
		b.order = b.order[1:]
		if pos := sort.SearchFloat64s(b.sorted, old); pos < len(b.sorted) && b.sorted[pos] == old {
			b.sorted = append(b.sorted[:pos], b.sorted[pos+1:]...)
		}
		b.sum -= old
		b.sumsq -= old * old
	}
	return stored
}

func (b *baseline) ready() bool { return len(b.order) >= 8 }

func (b *baseline) mean() float64 {
	if len(b.order) == 0 {
		return 0
	}
	return b.sum / float64(len(b.order))
}

func (b *baseline) stdev() float64 {
	n := float64(len(b.order))
	if n < 2 {
		return 0
	}
	variance := b.sumsq/n - math.Pow(b.mean(), 2)
	if variance <= 0 {
		return 0
	}
	return math.Sqrt(variance)
}

func (b *baseline) quantile(q float64) float64 {
	n := len(b.sorted)
	if n == 0 {
		return 0
	}
	idx := int(q * float64(n-1))
	if idx < 0 {
		idx = 0
	}
	if idx > n-1 {
		idx = n - 1
	}
	return b.sorted[idx]
}

// reference is the divisor a burst is measured against: the lower quartile,
// floored. Anchoring on the median would let the cap in add() ratchet the
// baseline upward by capMultiple on every round.
func (b *baseline) reference() float64 {
	return math.Max(b.quantile(0.25), b.floor)
}

func (b *baseline) multiple(value float64) float64 {
	if ref := b.reference(); ref > 0 {
		return value / ref
	}
	return 0
}

func (b *baseline) zscore(value float64) float64 {
	if sd := b.stdev(); sd > 0 {
		return (value - b.mean()) / sd
	}
	return b.multiple(value)
}

// --------------------------------------------------------------------------
// Per-symbol state
// --------------------------------------------------------------------------

const (
	secondsPerDay = 86400
	tenSeconds    = 10.0
)

type takerPrint struct {
	TS     int64
	Signed float64 // + taker buy, - taker sell
	Quote  float64
}

type symbolFlow struct {
	Symbol string

	// Ring buffer, newest at index (head+count-1)%cap.
	buf   []Tick
	head  int
	count int

	baseline *baseline

	lastTick Tick
	hasLast  bool
	lastPric float64

	QuoteVolume24h    float64
	PriceChangePct24h float64
	High24h           float64
	Low24h            float64

	sigma1s      float64
	sigmaSamples int64

	bucketStart     int64
	bucketFlow      float64
	bucketTrades    int64
	bucketCoveredMs int64

	floorFraction float64

	// tradesBaseline mirrors the flow baseline but holds per-bucket trade
	// COUNTS, so trades_multiple divides a count by a count. The previous
	// version divided trades10 by the flow baseline's median quote dollars,
	// which is a unit mismatch and produced garbage (120 trades / $7,700 =
	// 0.0156). The two baselines are fed from the same bucket close, so they
	// cannot drift apart.
	tradesBaseline *baseline

	taker []takerPrint
	// orderSizeBaseline tracks average print size, which is what separates one
	// whale order from five hundred retail orders of the same total value.
	orderSizeBaseline orderSizeBaseline

	updatedMS int64
}

func newSymbolFlow(symbol string, capacity int, floorFraction float64) *symbolFlow {
	if capacity < 60 {
		capacity = 60
	}
	return &symbolFlow{
		Symbol:         symbol,
		buf:            make([]Tick, capacity),
		baseline:       newBaseline(180, 6.0),
		tradesBaseline: newBaseline(180, 6.0),
		floorFraction:  floorFraction,
	}
}

func (s *symbolFlow) push(t Tick) {
	s.buf[(s.head+s.count)%len(s.buf)] = t
	if s.count < len(s.buf) {
		s.count++
	} else {
		s.head = (s.head + 1) % len(s.buf)
	}
}

func (s *symbolFlow) at(i int) Tick { return s.buf[(s.head+i)%len(s.buf)] }

func (s *symbolFlow) newest(i int) Tick { return s.at(s.count - 1 - i) }

func (s *symbolFlow) update(t Tick) {
	if s.hasLast {
		prev := s.lastTick
		dt := t.TS - prev.TS
		if dt <= 0 {
			// Duplicate or out-of-order second: keep the newer price only.
			s.lastPric = t.Price
			s.lastTick = t
			return
		}
		t.DTMs = dt
		if d := t.QuoteVol - prev.QuoteVol; d > 0 {
			t.DQuote = d
		}
		if d := t.BaseVol - prev.BaseVol; d > 0 {
			t.DBase = d
		}
		if d := t.Trades - prev.Trades; d > 0 {
			t.DTrades = d
		}
		s.accumulateBucket(t)

		if prev.Price > 0 && t.Price > 0 {
			change := (t.Price - prev.Price) / prev.Price
			approx := math.Abs(change) * 1.2533 // mean|.| -> stdev for a normal
			s.sigmaSamples++
			if s.sigmaSamples == 1 {
				s.sigma1s = approx
			} else {
				n := float64(s.sigmaSamples)
				s.sigma1s = (s.sigma1s*(n-1) + approx) / n
			}
		}
	}

	s.push(t)
	s.lastTick = t
	s.hasLast = true
	s.lastPric = t.Price
	s.QuoteVolume24h = t.QuoteVol
	s.updatedMS = t.TS
}

func (s *symbolFlow) accumulateBucket(t Tick) {
	bucket := t.TS / 10000 // 10-second buckets
	if s.bucketStart == 0 {
		s.bucketStart = bucket
	}
	if bucket != s.bucketStart {
		if s.bucketCoveredMs >= 9000 {
			s.baseline.add(s.bucketFlow)
			s.tradesBaseline.add(float64(s.bucketTrades))
		}
		s.bucketStart = bucket
		s.bucketFlow = 0
		s.bucketTrades = 0
		s.bucketCoveredMs = 0
	}
	s.bucketFlow += t.DQuote
	s.bucketTrades += t.DTrades
	s.bucketCoveredMs += t.DTMs
}

// windowEnding returns ticks inside the window ending at `now`, plus the
// covered span in milliseconds.
//
// Bounded on BOTH sides. The upper bound matters because flowAcceleration
// queries a window that ends in the past; without it the "previous" window
// would swallow every tick up to the present, the windows would overlap, and
// the acceleration ratio would be meaningless.
func (s *symbolFlow) windowEnding(seconds float64, now int64) ([]Tick, int64) {
	cutoff := now - int64(seconds*1000)
	out := make([]Tick, 0, 16)
	var covered int64
	for i := 0; i < s.count; i++ {
		t := s.newest(i)
		if t.TS > now {
			continue
		}
		if t.TS < cutoff {
			break
		}
		out = append(out, t)
		covered += t.DTMs
	}
	return out, covered
}

func (s *symbolFlow) flowQuote(seconds float64, now int64) (float64, float64) {
	ticks, covered := s.windowEnding(seconds, now)
	var total float64
	for _, t := range ticks {
		total += t.DQuote
	}
	return total, windowCoverage(covered, seconds)
}

func (s *symbolFlow) flowTrades(seconds float64, now int64) (int64, float64) {
	ticks, covered := s.windowEnding(seconds, now)
	var total int64
	for _, t := range ticks {
		total += t.DTrades
	}
	return total, windowCoverage(covered, seconds)
}

func windowCoverage(coveredMs int64, seconds float64) float64 {
	target := seconds * 1000
	if target <= 0 {
		return 0
	}
	return clamp(float64(coveredMs)/target, 0, 1)
}

func (s *symbolFlow) changeOver(seconds float64, now int64) (float64, bool) {
	cutoff := now - int64(seconds*1000)
	for i := 0; i < s.count; i++ {
		t := s.newest(i)
		if t.TS <= cutoff {
			if t.Price <= 0 || s.lastPric <= 0 {
				return 0, false
			}
			return (s.lastPric - t.Price) / t.Price, true
		}
	}
	return 0, false
}

func (s *symbolFlow) rangePosition(seconds float64, now int64) float64 {
	ticks, _ := s.windowEnding(seconds, now)
	if len(ticks) == 0 {
		return 0.5
	}
	low, high := s.lastPric, s.lastPric
	for _, t := range ticks {
		if t.Price < low {
			low = t.Price
		}
		if t.Price > high {
			high = t.Price
		}
	}
	if high <= low {
		return 0.5
	}
	return clamp((s.lastPric-low)/(high-low), 0, 1)
}

func (s *symbolFlow) spreadPct() float64 {
	if !s.hasLast || s.lastTick.Bid <= 0 || s.lastTick.Ask <= 0 || s.lastTick.Ask < s.lastTick.Bid {
		return 0
	}
	mid := (s.lastTick.Bid + s.lastTick.Ask) / 2
	return (s.lastTick.Ask - s.lastTick.Bid) / mid
}

func (s *symbolFlow) bookDepthUSD() float64 {
	if !s.hasLast {
		return 0
	}
	bid := s.lastTick.Bid * s.lastTick.BidQty
	ask := s.lastTick.Ask * s.lastTick.AskQty
	if bid > 0 && ask > 0 {
		return math.Min(bid, ask)
	}
	return math.Max(bid, ask)
}

func (s *symbolFlow) flowAcceleration(now int64) (float64, bool) {
	current, covNow := s.flowQuote(tenSeconds, now)
	previous, covPrev := s.flowQuote(tenSeconds, now-int64(tenSeconds*1000))
	if covNow < 0.5 || covPrev < 0.5 {
		return 0, false
	}
	if previous <= 0 {
		if current > 0 {
			return 99, true
		}
		return 0, true
	}
	return current / previous, true
}

func (s *symbolFlow) applyLiquidityFloor() {
	per10s := s.QuoteVolume24h / (secondsPerDay / tenSeconds)
	s.baseline.floor = per10s * s.floorFraction
}

func (s *symbolFlow) sigmaWindow(seconds float64) float64 {
	return s.sigma1s * math.Sqrt(math.Max(seconds, 0))
}

func (s *symbolFlow) addTaker(ts int64, quote float64, buyerMaker bool) {
	signed := quote
	if buyerMaker {
		signed = -quote
	}
	s.taker = append(s.taker, takerPrint{TS: ts, Signed: signed, Quote: quote})
	// Feed the order-size baseline from the same print, so the two measurements
	// cannot drift apart.
	s.orderSizeBaseline.observe(quote)
	if len(s.taker) > 4000 {
		s.taker = s.taker[len(s.taker)-2000:]
	}
}

func (s *symbolFlow) pruneTaker(cutoff int64) {
	idx := 0
	for idx < len(s.taker) && s.taker[idx].TS < cutoff {
		idx++
	}
	if idx > 0 {
		s.taker = s.taker[idx:]
	}
}

// takerRatio returns the taker-buy share over the window and the quote volume
// that backed it. 0.5 with zero volume means "no trade-level data yet".
func (s *symbolFlow) takerRatio(windowSec float64, now int64) (float64, float64) {
	cutoff := now - int64(windowSec*1000)
	var buy, sell float64
	for _, p := range s.taker {
		if p.TS < cutoff {
			continue
		}
		if p.Signed > 0 {
			buy += p.Quote
		} else {
			sell += p.Quote
		}
	}
	total := buy + sell
	if total <= 0 {
		return 0.5, 0
	}
	return buy / total, total
}

// --------------------------------------------------------------------------
// Public row type
// --------------------------------------------------------------------------

// Candidate is one symbol's current flow state. It doubles as the API row.
type Candidate struct {
	Symbol        string  `json:"symbol"`
	Market        string  `json:"market"`
	Direction     int     `json:"direction"`
	Price         float64 `json:"last_price"`
	FlowQuote     float64 `json:"flow_quote"`
	BaselineQuote float64 `json:"baseline_quote"`
	Coverage      float64 `json:"coverage"`
	BurstMultiple float64 `json:"burst_multiple"`
	BurstZ        float64 `json:"burst_z"`
	// Multi-horizon moves. The short horizons are where an ONSET is visible and a
	// 60s figure is not: by the time 60 seconds of move has accumulated the entry
	// is gone. 3s and 5s catch the first push, 10s is the window the gates act on,
	// and 15s is the shortest horizon that can still distinguish a sustained move
	// from a single aggressive print.
	Change3s  *float64 `json:"change_3s,omitempty"`
	Change5s  *float64 `json:"change_5s,omitempty"`
	Change10s *float64 `json:"change_10s"`
	Change15s *float64 `json:"change_15s,omitempty"`
	Change30s *float64 `json:"change_30s"`
	Change60s *float64 `json:"change_60s"`
	// TrendSign is -1, 0 or +1: the direction the horizons agree on, weighted by
	// how much each has actually moved.
	TrendSign int `json:"trend_sign"`
	// TrendAgreement is the fraction of the four short horizons sharing that
	// sign, 0..1. This is the number that separates a genuine move from a
	// single-horizon flicker: four horizons agreeing is a trend, one horizon
	// moving alone is a print.
	TrendAgreement float64 `json:"trend_agreement"`
	// TrendLabel is a human read of the two above, for the UI and the log.
	TrendLabel string `json:"trend_label"`
	// Precursor is the quiet-then-buy-surge read over 20/30/45/60s. Present only
	// when the symbol could be assessed: a nil here means NOT MEASURABLE, which is
	// a different statement from a low score and must never render as zero.
	Precursor     *PrecursorRead `json:"precursor,omitempty"`
	Sigma10s      float64        `json:"sigma_10s"`
	SigmaMultiple float64        `json:"sigma_multiple"`
	Acceleration  *float64       `json:"acceleration"`
	RangePosition float64        `json:"range_position"`
	SpreadPct     float64        `json:"spread_pct"`
	BookDepthUSD  float64        `json:"book_depth_usd"`
	// BookMissing is true when the ticker carried no usable top-of-book (no
	// bid/ask, or a crossed book). Internal only: the wire keeps SpreadPct=0,
	// and the scorer must NOT read that as a zero-spread market -- missing
	// data earns zero quality points, not full ones.
	BookMissing    bool    `json:"-"`
	QuoteVolume24h float64 `json:"quote_volume_24h"`
	// The 24h standing. Already tracked on the symbol and discarded before
	// reaching the row, which is why a "greatest increases" view needed new
	// plumbing for data that was arriving all along.
	PriceChangePct24h float64 `json:"price_change_pct_24h"`
	High24h           float64 `json:"high_24h"`
	Low24h            float64 `json:"low_24h"`
	// Average print size against the symbol's own quiet baseline. Above 1 means
	// larger orders than usual: accumulation, rather than the retail attention a
	// pump attracts once it is already visible.
	AvgOrderSize    float64  `json:"avg_order_size"`
	OrderSizeBase   float64  `json:"order_size_baseline"`
	OrderSizeMult   float64  `json:"order_size_multiple"`
	OrderSizePrints int      `json:"order_size_prints"`
	Trades10s       int64    `json:"trades_10s"`
	TradesMultiple  float64  `json:"trades_multiple"`
	TakerBuyRatio   float64  `json:"taker_buy_ratio"`
	TakerQuote      float64  `json:"taker_quote"`
	UpdatedMS       int64    `json:"updated_ms"`
	Reasons         []string `json:"reasons"`
}

// --------------------------------------------------------------------------
// Engine
// --------------------------------------------------------------------------

// Config holds every onset-detection gate.
type Config struct {
	Market                string
	WindowSec             float64
	MinBurstMultiple      float64
	MinBurstZ             float64
	MinAbsFlow            float64
	MinMovePct            float64
	MinSigmaMultiple      float64
	MinCoverage           float64
	MaxSpreadPct          float64
	MinBookDepthUSD       float64
	MaxLatePct60s         float64
	Max24hMovePct         float64
	RequireAcceleration   bool
	MinTrades             int64
	BaselineFloorFraction float64
	TrackCapacitySeconds  int
}

// DefaultConfig returns the tuned defaults.
func DefaultConfig() Config {
	return Config{
		Market:                "spot",
		WindowSec:             10,
		MinBurstMultiple:      8,
		MinBurstZ:             4,
		MinAbsFlow:            5000,
		MinMovePct:            0.4,
		MinSigmaMultiple:      3,
		MinCoverage:           0.7,
		MaxSpreadPct:          0.004,
		MinBookDepthUSD:       500,
		MaxLatePct60s:         6,
		Max24hMovePct:         60,
		RequireAcceleration:   true,
		MinTrades:             8,
		BaselineFloorFraction: 0.25,
		TrackCapacitySeconds:  600,
	}
}

// Engine owns every tracked symbol's flow state.
type Engine struct {
	cfg Config

	mu      sync.Mutex
	states  map[string]*symbolFlow
	tracked map[string]struct{}

	messages  atomic.Int64
	truncated atomic.Int64
	lastMsgMS atomic.Int64
}

// NewEngine creates a flow engine.
func NewEngine(cfg Config) *Engine {
	if cfg.TrackCapacitySeconds <= 0 {
		cfg.TrackCapacitySeconds = 600
	}
	return &Engine{
		cfg:     cfg,
		states:  make(map[string]*symbolFlow),
		tracked: make(map[string]struct{}),
	}
}

// Config returns the engine configuration.
func (e *Engine) Config() Config { return e.cfg }

// SetUniverse restricts tracking to the given symbols.
func (e *Engine) SetUniverse(symbols []string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	wanted := make(map[string]struct{}, len(symbols))
	for _, s := range symbols {
		wanted[s] = struct{}{}
	}
	e.tracked = wanted
	for symbol := range e.states {
		if _, ok := wanted[symbol]; !ok {
			delete(e.states, symbol)
		}
	}
}

// Tracked returns the tracked symbol set, sorted.
func (e *Engine) Tracked() []string {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]string, 0, len(e.tracked))
	for s := range e.tracked {
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}

func (e *Engine) ensureLocked(symbol string) *symbolFlow {
	if s, ok := e.states[symbol]; ok {
		return s
	}
	s := newSymbolFlow(symbol, e.cfg.TrackCapacitySeconds, e.cfg.BaselineFloorFraction)
	e.states[symbol] = s
	return s
}

// IngestTicker consumes one @ticker payload and returns the symbol if applied.
//
// Keys are pulled by exact name; see the note above the key table for why a
// tagged struct is unsafe against this payload.
func (e *Engine) IngestTicker(data json.RawMessage) string {
	fields, err := decodeFields(data)
	if err != nil {
		return ""
	}
	symbol := rawString(fields, keySymbol)
	ts := rawInt(fields, keyEventTime)
	price := rawFloat(fields, keyClose)
	if symbol == "" || ts == 0 || price <= 0 {
		return ""
	}

	e.mu.Lock()
	if len(e.tracked) > 0 {
		if _, ok := e.tracked[symbol]; !ok {
			e.mu.Unlock()
			return ""
		}
	}
	s := e.ensureLocked(symbol)
	s.update(Tick{
		TS:       ts,
		Price:    price,
		QuoteVol: rawFloat(fields, keyQuoteVol),
		BaseVol:  rawFloat(fields, keyVolume),
		Trades:   rawInt(fields, keyTrades),
		Bid:      rawFloat(fields, keyBid),
		Ask:      rawFloat(fields, keyAsk),
		BidQty:   rawFloat(fields, keyBidQty),
		AskQty:   rawFloat(fields, keyAskQty),
	})
	s.PriceChangePct24h = rawFloat(fields, keyChangePct)
	s.High24h = rawFloat(fields, keyHigh)
	s.Low24h = rawFloat(fields, keyLow)
	e.mu.Unlock()

	e.messages.Add(1)
	if ts > e.lastMsgMS.Load() {
		e.lastMsgMS.Store(ts)
	}
	return symbol
}

// IngestAggTrade consumes one @aggTrade payload for a tracked symbol.
//
// Note the "m" / "M" pair: an @aggTrade carries both, "m" being the real
// buyer-is-maker flag and "M" a constant true. With a tagged struct, "M" would
// match `json:"m"` case-insensitively and overwrite the flag, marking every
// trade as a sell. Exact keys avoid that.
func (e *Engine) IngestAggTrade(data json.RawMessage) string {
	fields, err := decodeFields(data)
	if err != nil {
		return ""
	}
	symbol := rawString(fields, keySymbol)
	price := rawFloat(fields, keyPrice)
	qty := rawFloat(fields, keyQuantity)
	if symbol == "" || price <= 0 || qty <= 0 {
		return ""
	}
	ts := rawInt(fields, keyTradeTime)
	if ts == 0 {
		ts = rawInt(fields, keyEventTime)
	}
	buyerMaker := rawBool(fields, keyBuyerMaker)

	e.mu.Lock()
	s, ok := e.states[symbol]
	if ok {
		s.addTaker(ts, price*qty, buyerMaker)
	}
	e.mu.Unlock()
	if !ok {
		return ""
	}
	return symbol
}

// ApplyFloors refreshes liquidity floors from the latest 24h volumes.
func (e *Engine) ApplyFloors() {
	e.mu.Lock()
	defer e.mu.Unlock()
	for _, s := range e.states {
		s.applyLiquidityFloor()
	}
}

// Series1s is the minimal 1-second bar the seeder needs.
type Series1s struct {
	OpenTime    int64
	Close       float64
	QuoteVolume float64
}

// SetQuoteVolume primes the 24h volume behind the liquidity floor, so the floor
// is correct before the first live tick arrives.
func (e *Engine) SetQuoteVolume(symbol string, quoteVolume24h float64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	s := e.ensureLocked(symbol)
	s.QuoteVolume24h = quoteVolume24h
	s.applyLiquidityFloor()
}

// SeedBaseline seeds a symbol's baseline from 1s klines and returns the number
// of baseline samples added.
//
// Without this the scanner needs ~80 seconds of live ticks before any symbol
// has a usable baseline, which would mean missing the first minutes of a move
// right after startup.
func (e *Engine) SeedBaseline(symbol string, bars []Series1s) int {
	if len(bars) == 0 {
		return 0
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	s := e.ensureLocked(symbol)
	s.applyLiquidityFloor()

	var windowFlow float64
	var covered int64
	bucket := int64(-1)
	samples := 0
	returns := make([]float64, 0, len(bars))
	prevClose := 0.0

	for _, bar := range bars {
		if prevClose > 0 && bar.Close > 0 {
			returns = append(returns, (bar.Close-prevClose)/prevClose)
		}
		if bar.Close > 0 {
			prevClose = bar.Close
		}
		slot := bar.OpenTime / 10000
		if bucket == -1 {
			bucket = slot
		}
		if slot != bucket {
			if covered >= 9000 {
				s.baseline.add(windowFlow)
				samples++
			}
			windowFlow = 0
			covered = 0
			bucket = slot
		}
		windowFlow += bar.QuoteVolume
		covered += 1000
	}
	if covered >= 9000 {
		s.baseline.add(windowFlow)
		samples++
	}
	if len(returns) >= 2 {
		s.sigma1s = stdev(returns)
		s.sigmaSamples = int64(len(returns))
	}
	return samples
}

// Detect scans every tracked symbol for a flow burst that is just starting.
func (e *Engine) Detect(now int64) []Candidate {
	e.mu.Lock()
	defer e.mu.Unlock()

	staleCutoff := now - 15000
	out := make([]Candidate, 0, 16)
	for _, s := range e.states {
		if !s.hasLast || s.lastTick.TS < staleCutoff {
			continue
		}
		if !s.baseline.ready() {
			continue
		}
		if c, ok := e.evaluateLocked(s, now); ok {
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].FlowQuote > out[j].FlowQuote })
	return out
}

// evaluateLocked requires e.mu to be held.
func (e *Engine) evaluateLocked(s *symbolFlow, now int64) (Candidate, bool) {
	window := e.cfg.WindowSec

	flow, cov := s.flowQuote(window, now)
	if cov < e.cfg.MinCoverage {
		e.truncated.Add(1)
		return Candidate{}, false
	}
	if flow < e.cfg.MinAbsFlow {
		return Candidate{}, false
	}
	burstMultiple := s.baseline.multiple(flow)
	burstZ := s.baseline.zscore(flow)
	if burstMultiple < e.cfg.MinBurstMultiple {
		return Candidate{}, false
	}
	if burstZ < e.cfg.MinBurstZ && burstMultiple < 2*e.cfg.MinBurstMultiple {
		return Candidate{}, false
	}

	change10, ok := s.changeOver(window, now)
	if !ok {
		return Candidate{}, false
	}
	direction := 1
	if change10 < 0 {
		direction = -1
	}
	if math.Abs(change10) < e.cfg.MinMovePct/100 {
		return Candidate{}, false
	}

	sigma10 := s.sigmaWindow(window)
	sigmaMultiple := 0.0
	if sigma10 > 0 {
		sigmaMultiple = math.Abs(change10) / sigma10
		if sigmaMultiple < e.cfg.MinSigmaMultiple {
			return Candidate{}, false
		}
	}

	change30, has30 := s.changeOver(30, now)
	change60, has60 := s.changeOver(60, now)

	// "Already moved" guard: if the last minute dwarfs the last ten seconds,
	// this is the tail of a move, not the start of one.
	if has60 && math.Abs(change60) > e.cfg.MaxLatePct60s/100 {
		if math.Abs(change60) > 3*math.Abs(change10) {
			return Candidate{}, false
		}
	}
	if s.PriceChangePct24h != 0 && math.Abs(s.PriceChangePct24h) > e.cfg.Max24hMovePct {
		if float64(direction)*s.PriceChangePct24h > 0 {
			return Candidate{}, false
		}
	}

	acceleration, hasAccel := s.flowAcceleration(now)
	if e.cfg.RequireAcceleration && hasAccel && acceleration < 1.0 {
		return Candidate{}, false
	}

	trades10, _ := s.flowTrades(window, now)
	if trades10 < e.cfg.MinTrades {
		return Candidate{}, false
	}
	// Count against count: the trades baseline holds per-bucket trade counts.
	// Until it has enough samples (it cannot be seeded from klines, which
	// carry no per-window trade counts) the multiple stays 0, meaning "not
	// yet measurable" rather than a fabricated ratio.
	tradesMultiple := 0.0
	if s.tradesBaseline.ready() {
		if ref := s.tradesBaseline.reference(); ref > 0 {
			tradesMultiple = float64(trades10) / ref
		}
	}

	spread := s.spreadPct()
	if spread > e.cfg.MaxSpreadPct {
		return Candidate{}, false
	}
	depth := s.bookDepthUSD()
	if depth > 0 && depth < e.cfg.MinBookDepthUSD {
		return Candidate{}, false
	}
	// Missing or crossed top-of-book reads as spread 0 / depth 0. The gates
	// above stay permissive (a feed without book fields must not blind the
	// scanner), but BookMissing stops the scorer treating "no data" as "a
	// perfect book".
	bookMissing := !s.hasLast || s.lastTick.Bid <= 0 || s.lastTick.Ask <= 0 ||
		s.lastTick.Ask < s.lastTick.Bid

	rangePos := s.rangePosition(window, now)
	alignedRange := rangePos
	if direction < 0 {
		alignedRange = 1 - rangePos
	}
	takerRatio, takerQuote := s.takerRatio(window*2, now)

	reasons := []string{
		describeFlow(window, flow, burstMultiple, burstZ),
		describeMove(window, change10, sigma10, sigmaMultiple),
	}
	if hasAccel {
		reasons = append(reasons, "flow acceleration "+f2(acceleration)+"x vs prior window")
	}
	reasons = append(reasons, "trades "+strconv.FormatInt(trades10, 10)+" in "+f0(window)+"s")
	if spread > 0 {
		reasons = append(reasons, "spread "+f3(spread*100)+"%")
	}
	if alignedRange < 0.4 {
		reasons = append(reasons, "warning: closed at "+f0(rangePos*100)+"% of window range")
	}

	c := Candidate{
		Symbol:         s.Symbol,
		Market:         e.cfg.Market,
		Direction:      direction,
		Price:          s.lastPric,
		FlowQuote:      flow,
		BaselineQuote:  s.baseline.reference(),
		Coverage:       cov,
		BurstMultiple:  burstMultiple,
		BurstZ:         burstZ,
		Change10s:      &change10,
		Sigma10s:       sigma10,
		SigmaMultiple:  sigmaMultiple,
		RangePosition:  rangePos,
		SpreadPct:      spread,
		BookDepthUSD:   depth,
		BookMissing:    bookMissing,
		QuoteVolume24h: s.QuoteVolume24h,
		Trades10s:      trades10,
		TradesMultiple: tradesMultiple,
		TakerBuyRatio:  takerRatio,
		TakerQuote:     takerQuote,
		UpdatedMS:      s.updatedMS,
		Reasons:        reasons,
	}
	if has30 {
		c.Change30s = &change30
	}
	if has60 {
		c.Change60s = &change60
	}
	if hasAccel {
		c.Acceleration = &acceleration
	}
	return c, true
}

// Snapshot returns the current flow leaderboard for the API.
// MoneyFlow samples one symbol's order flow at several intervals and returns the
// combined conviction read. Returns false when the symbol is not tracked, which
// is distinct from a HOLD: "not measured" and "measured as balanced" are
// different answers and the caller must be able to tell them apart.
func (e *Engine) MoneyFlow(symbol string, now int64) (Conviction, bool) {
	e.mu.Lock()
	s, ok := e.states[symbol]
	e.mu.Unlock()
	if !ok || s == nil {
		return Conviction{}, false
	}
	return s.SampleMoneyFlow(now), true
}

func (e *Engine) Snapshot(limit int, now int64) []Candidate {
	e.mu.Lock()
	defer e.mu.Unlock()

	rows := make([]Candidate, 0, len(e.states))
	for _, s := range e.states {
		if !s.hasLast {
			continue
		}
		flow, cov := s.flowQuote(e.cfg.WindowSec, now)
		if flow <= 0 {
			continue
		}
		change10, has10 := s.changeOver(e.cfg.WindowSec, now)
		change30, has30 := s.changeOver(30, now)
		change60, has60 := s.changeOver(60, now)
		// The short horizons, computed from the same tick ring so they are
		// mutually consistent by construction rather than by four separate paths.
		// Average print size, from the same prints the flow uses.
		os := s.OrderSize(e.cfg.WindowSec, now)
		change3, has3 := s.changeOver(3, now)
		change5, has5 := s.changeOver(5, now)
		change15, has15 := s.changeOver(15, now)
		accel, hasAccel := s.flowAcceleration(now)
		takerRatio, takerQuote := s.takerRatio(e.cfg.WindowSec*2, now)

		row := Candidate{
			Symbol:            s.Symbol,
			Market:            e.cfg.Market,
			Direction:         1,
			Price:             s.lastPric,
			FlowQuote:         flow,
			BaselineQuote:     s.baseline.reference(),
			Coverage:          cov,
			BurstMultiple:     s.baseline.multiple(flow),
			BurstZ:            s.baseline.zscore(flow),
			Sigma10s:          s.sigmaWindow(e.cfg.WindowSec),
			RangePosition:     s.rangePosition(e.cfg.WindowSec, now),
			SpreadPct:         s.spreadPct(),
			BookDepthUSD:      s.bookDepthUSD(),
			QuoteVolume24h:    s.QuoteVolume24h,
			PriceChangePct24h: s.PriceChangePct24h,
			High24h:           s.High24h,
			Low24h:            s.Low24h,
			// Measured from the same taker prints as the flow, so the two cannot
			// drift apart.
			AvgOrderSize:    os.AvgQuote,
			OrderSizeBase:   os.Baseline,
			OrderSizeMult:   os.Ratio,
			OrderSizePrints: os.Prints,
			TakerBuyRatio:   takerRatio,
			TakerQuote:      takerQuote,
			UpdatedMS:       s.updatedMS,
		}
		if has3 {
			row.Change3s = &change3
		}
		if has5 {
			row.Change5s = &change5
		}
		if has15 {
			row.Change15s = &change15
		}
		row.TrendSign, row.TrendAgreement, row.TrendLabel = readTrend(
			moveOr(row.Change3s), moveOr(row.Change5s), moveOr(row.Change10s), moveOr(row.Change15s))
		// The quiet-then-buy-surge precursor, over its own four horizons.
		if s.baseline.ready() {
			read := s.assessPrecursor(now, e.cfg.WindowSec)
			if read.ConfidencePct != nil {
				row.Precursor = &read
			}
		}
		if !s.baseline.ready() {
			row.BurstMultiple = 0
			row.BurstZ = 0
		}
		if has10 {
			row.Change10s = &change10
			if change10 < 0 {
				row.Direction = -1
			}
		}
		if has30 {
			row.Change30s = &change30
		}
		if has60 {
			row.Change60s = &change60
		}
		if hasAccel {
			row.Acceleration = &accel
		}
		trades, _ := s.flowTrades(e.cfg.WindowSec, now)
		row.Trades10s = trades
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].FlowQuote > rows[j].FlowQuote })
	if limit > 0 && len(rows) > limit {
		rows = rows[:limit]
	}
	return rows
}

// Stats summarises engine health.
type Stats struct {
	SymbolsTracked int   `json:"symbols_tracked"`
	BaselinesReady int   `json:"baselines_ready"`
	Messages       int64 `json:"messages"`
	Truncated      int64 `json:"truncated"`
	LastMessageMS  int64 `json:"last_message_ms"`
}

// Stats returns engine health counters.
func (e *Engine) Stats() Stats {
	e.mu.Lock()
	defer e.mu.Unlock()
	ready := 0
	for _, s := range e.states {
		if s.baseline.ready() {
			ready++
		}
	}
	return Stats{
		SymbolsTracked: len(e.states),
		BaselinesReady: ready,
		Messages:       e.messages.Load(),
		Truncated:      e.truncated.Load(),
		LastMessageMS:  e.lastMsgMS.Load(),
	}
}

// TrackedCount returns the number of tracked symbols.
func (e *Engine) TrackedCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	if len(e.tracked) > 0 {
		return len(e.tracked)
	}
	return len(e.states)
}

// PruneTaker drops stale trade prints to bound memory.
func (e *Engine) PruneTaker() {
	cutoff := time.Now().UnixMilli() - 120000
	e.mu.Lock()
	defer e.mu.Unlock()
	for _, s := range e.states {
		s.pruneTaker(cutoff)
	}
}

// moveOr dereferences an optional move, returning 0 when absent.
func moveOr(v *float64) float64 {
	if v == nil {
		return 0
	}
	return *v
}

// readTrend turns four horizon moves into a direction, an agreement fraction and
// a label.
//
// Why agreement matters more than magnitude: a single horizon showing a large
// move is one aggressive print, which is exactly the thing that trips a naive
// detector and then reverses. Four horizons pointing the same way is a move that
// has been sustained across every timescale we watch, which is what a pump or a
// dump actually looks like while it is happening.
//
// The horizons are weighted rather than counted equally, because a 3s move is
// noisier evidence than a 15s one: the weighted agreement is the fraction of
// total movement that shares the dominant sign.
func readTrend(change3, change5, change10, change15 float64) (int, float64, string) {
	moves := [4]float64{change3, change5, change10, change15}
	// Weights rise with the horizon: 3s is the least trustworthy witness.
	weights := [4]float64{0.15, 0.20, 0.30, 0.35}

	up, down := 0.0, 0.0
	// A deadband, so market noise around zero does not count as agreement in
	// either direction. Without it a flat symbol reports 100% agreement on a
	// move of zero.
	const deadband = 0.02
	for i, move := range moves {
		if move > deadband {
			up += weights[i]
		} else if move < -deadband {
			down += weights[i]
		}
	}

	if up == 0 && down == 0 {
		return 0, 0, "flat"
	}
	sign, agreement := 1, up
	if down > up {
		sign, agreement = -1, down
	}

	// Agreement is the share of TOTAL weight, not the share of active weight.
	//
	// Dividing by (up + down) was wrong and a test caught it: horizons inside the
	// deadband contribute to neither side, so a single horizon moving alone got
	// up/total = 0.15/0.15 = 1.0 and was reported as a SUSTAINED PUMP. The one
	// case the metric exists to reject — one horizon flickering while the others
	// sit still — was the case it scored highest. Dividing by the full weight
	// makes a lone mover score 0.15, which is what it is.
	const totalWeight = 1.0
	agreement = agreement / totalWeight

	label := "mixed"
	switch {
	case agreement > 0.85 && sign > 0:
		label = "sustained pump"
	case agreement > 0.85 && sign < 0:
		label = "sustained dump"
	case agreement > 0.6 && sign > 0:
		label = "building up"
	case agreement > 0.6 && sign < 0:
		label = "building down"
	case sign > 0:
		label = "single-horizon up"
	default:
		label = "single-horizon down"
	}
	return sign, roundTo(agreement, 4), label
}

func roundTo(v float64, digits int) float64 {
	factor := 1.0
	for i := 0; i < digits; i++ {
		factor *= 10
	}
	if v != v {
		return 0
	}
	return float64(int64(v*factor+signOf(v)*0.5)) / factor
}

func signOf(v float64) float64 {
	if v < 0 {
		return -1
	}
	return 1
}
