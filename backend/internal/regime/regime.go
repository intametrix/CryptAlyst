// Package regime answers "what is the broad market doing?" — rising, falling
// or sideways — from the 24-hour universe snapshot the server already holds.
//
// It is deliberately a small, transparent rule, not a learned model, because a
// market-regime badge is the first thing a reader sees and the easiest place to
// overstate confidence. Every output therefore carries the exact inputs it was
// computed from, so the label can be audited without reading the code.
//
// The rule: BTC's 24h move is the anchor (half the score), the median 24h move
// across the universe is the confirmation (the other half), and the label is a
// dead-banded three-way split of that score. Breadth (the fraction of symbols
// up) is reported alongside as evidence, never as a hidden threshold.
package regime

import (
	"fmt"
	"math"
	"sort"
	"sync"
	"time"

	"pumpdump/backend/internal/market"
)

// Labels.
const (
	Rising   = "rising"
	Falling  = "falling"
	Sideways = "sideways"
)

// scoreBand is the dead band inside which the regime is reported sideways
// rather than pretending to a direction.
const scoreBand = 0.12

// Regime is the broad-market read.
type Regime struct {
	Type      string  `json:"type"`
	TSMS      int64   `json:"ts_ms"`
	Label     string  `json:"label"`
	Score     float64 `json:"score"`
	BTCRet24  float64 `json:"btc_ret_24h_pct"`
	ETHRet24  float64 `json:"eth_ret_24h_pct"`
	BreadthUp float64 `json:"breadth_up"`
	MedianRet float64 `json:"median_ret_24h_pct"`
	Symbols   int     `json:"symbols"`
	Basis     string  `json:"basis"`
}

// Compute derives the regime from the universe's 24h contexts.
func Compute(ctxs []market.Context) Regime {
	r := Regime{Type: "regime", TSMS: time.Now().UnixMilli()}
	if len(ctxs) == 0 {
		r.Label, r.Basis = Sideways, "no universe data"
		return r
	}

	rets := make([]float64, 0, len(ctxs))
	up := 0
	for _, c := range ctxs {
		rets = append(rets, c.PriceChangePct)
		if c.PriceChangePct > 0 {
			up++
		}
		switch c.Symbol {
		case "BTCUSDT":
			r.BTCRet24 = c.PriceChangePct
		case "ETHUSDT":
			r.ETHRet24 = c.PriceChangePct
		}
	}

	sort.Float64s(rets)
	r.MedianRet = rets[len(rets)/2]
	r.BreadthUp = float64(up) / float64(len(rets))
	r.Symbols = len(rets)

	// BTC anchors half the score; the median confirms the other half. Each
	// term is capped at ±1 by dividing by a 3% move, so one huge outlier cannot
	// single-handedly flip the label.
	r.Score = 0.5*clamp(r.BTCRet24/3.0, -1, 1) + 0.5*clamp(r.MedianRet/3.0, -1, 1)

	switch {
	case r.Score > scoreBand:
		r.Label = Rising
	case r.Score < -scoreBand:
		r.Label = Falling
	default:
		r.Label = Sideways
	}

	r.Basis = fmt.Sprintf("BTC 24h %+.2f%%, breadth %.0f%% up, median %+.2f%% over %d symbols",
		r.BTCRet24, r.BreadthUp*100, r.MedianRet, r.Symbols)
	return r
}

func clamp(v, lo, hi float64) float64 {
	if math.IsNaN(v) {
		return 0
	}
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// Cache holds the most recent regime so a loop can refresh it and the HTTP
// handler can serve the last computed value without recomputing.
type Cache struct {
	mu  sync.Mutex
	cur Regime
}

// NewCache returns an empty cache.
func NewCache() *Cache { return &Cache{} }

// Update recomputes the regime from the contexts and stores it.
func (c *Cache) Update(ctxs []market.Context) Regime {
	r := Compute(ctxs)
	c.mu.Lock()
	c.cur = r
	c.mu.Unlock()
	return r
}

// Get returns the most recently computed regime (zero value if never updated).
func (c *Cache) Get() Regime {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.cur
}
