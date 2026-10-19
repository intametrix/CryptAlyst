// Package levels derives horizontal support and resistance from swing pivots.
//
// Levels are found from price structure rather than drawn from round numbers or
// moving averages, because those are not what price actually reacted to. A
// swing pivot is a place where the market turned, so it is evidence of resting
// interest; a level's strength is then the number of times price turned there,
// weighted toward recent reactions.
package levels

import (
	"math"
	"sort"

	"pumpdump/backend/internal/binance"
)

// Params controls pivot detection and clustering.
type Params struct {
	// PivotSpan is how many bars either side a high/low must dominate to count
	// as a swing point. Larger span means fewer, more significant pivots.
	PivotSpan int
	// ClusterPct merges pivots within this percentage of each other into one
	// level: two turns 0.2% apart are the same level, not two.
	ClusterPct float64
	// MaxLevels caps how many are returned per side.
	MaxLevels int
	// RecencyHalfLifeBars weights recent touches more heavily.
	RecencyHalfLifeBars float64
	// MinTouches is the number of pivots a cluster needs to be reported.
	MinTouches int
}

// DefaultParams returns the starting values.
func DefaultParams() Params {
	return Params{
		PivotSpan:           3,
		ClusterPct:          0.35,
		MaxLevels:           4,
		RecencyHalfLifeBars: 120,
		MinTouches:          2,
	}
}

// Level is one horizontal price level.
type Level struct {
	Price       float64 `json:"price"`
	Kind        string  `json:"kind"` // "support" | "resistance"
	Strength    float64 `json:"strength"`
	Touches     int     `json:"touches"`
	LastTouchMS int64   `json:"last_touch_ms"`
	// DistancePct is how far price must travel to reach the level, signed.
	DistancePct float64 `json:"distance_pct"`
}

// Compute returns supports below price and resistances above it, each sorted
// by proximity to the current price (nearest first).
func Compute(bars []binance.Kline, intervalMS int64, p Params) (supports, resistances []Level) {
	if len(bars) < p.PivotSpan*2+1 || intervalMS <= 0 {
		return nil, nil
	}
	current := bars[len(bars)-1].Close
	if current <= 0 {
		return nil, nil
	}

	type pivot struct {
		price float64
		index int
	}
	pivots := make([]pivot, 0, 32)
	for i := p.PivotSpan; i < len(bars)-p.PivotSpan; i++ {
		isHigh, isLow := true, true
		for j := i - p.PivotSpan; j <= i+p.PivotSpan; j++ {
			if j == i {
				continue
			}
			if bars[j].High >= bars[i].High {
				isHigh = false
			}
			if bars[j].Low <= bars[i].Low {
				isLow = false
			}
		}
		if isHigh {
			pivots = append(pivots, pivot{price: bars[i].High, index: i})
		}
		if isLow {
			pivots = append(pivots, pivot{price: bars[i].Low, index: i})
		}
	}
	if len(pivots) == 0 {
		return nil, nil
	}
	sort.Slice(pivots, func(a, b int) bool { return pivots[a].price < pivots[b].price })

	// Greedy clustering over the sorted pivots.
	type cluster struct {
		sum      float64
		count    int
		lastIdx  int
		weighted float64
	}
	clusters := make([]cluster, 0, len(pivots))
	for _, pv := range pivots {
		merged := false
		if len(clusters) > 0 {
			last := &clusters[len(clusters)-1]
			mean := last.sum / float64(last.count)
			if mean > 0 && math.Abs(pv.price-mean)/mean*100 <= p.ClusterPct {
				last.sum += pv.price
				last.count++
				if pv.index > last.lastIdx {
					last.lastIdx = pv.index
				}
				merged = true
			}
		}
		if !merged {
			clusters = append(clusters, cluster{sum: pv.price, count: 1, lastIdx: pv.index})
		}
	}

	lastIndex := len(bars) - 1
	for _, c := range clusters {
		if c.count < p.MinTouches {
			continue
		}
		price := c.sum / float64(c.count)
		// Strength: how many turns, capped, decayed by how long ago the last
		// turn was. A level touched five times a year ago is weaker evidence
		// than one touched twice this hour.
		touches := math.Min(1.0, float64(c.count)/5.0)
		age := float64(lastIndex - c.lastIdx)
		recency := math.Pow(0.5, age/math.Max(p.RecencyHalfLifeBars, 1))
		strength := 0.7*touches + 0.3*recency
		level := Level{
			Price:       round(price),
			Strength:    round(strength),
			Touches:     c.count,
			LastTouchMS: bars[c.lastIdx].OpenTime,
			DistancePct: round((price - current) / current * 100),
		}
		if price >= current {
			level.Kind = "resistance"
			resistances = append(resistances, level)
		} else {
			level.Kind = "support"
			supports = append(supports, level)
		}
	}

	// Nearest first: the next level price meets matters more than a distant one.
	sort.Slice(resistances, func(a, b int) bool { return resistances[a].Price < resistances[b].Price })
	sort.Slice(supports, func(a, b int) bool { return supports[a].Price > supports[b].Price })
	if len(resistances) > p.MaxLevels {
		resistances = resistances[:p.MaxLevels]
	}
	if len(supports) > p.MaxLevels {
		supports = supports[:p.MaxLevels]
	}
	return supports, resistances
}

func round(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	scale := math.Pow(10, math.Max(0, 6-math.Floor(math.Log10(math.Abs(v)+1e-12))))
	return math.Round(v*scale) / scale
}
