package api

import (
	"math"

	"pumpdump/backend/internal/binance"
)

// Hybrid: blend models that fail differently.
//
// Each model here is wrong in its own way — VWAP reverts to the traded average,
// vwtrend carries the volume-weighted slope, vwdrift follows participation, ARIMA
// extrapolates its own dynamics. Averaging them cuts the variance of those
// individual errors, which is the cheapest real improvement available once single
// models plateau.

// hybridBase lists the components and the lookback each uses.
var hybridBase = []struct {
	method   string
	lookback int
	weight   float64
}{
	{"vwap", 12, 0.30},
	{"vwap", 24, 0.15},
	{"vwtrend", 50, 0.25},
	{"vwdrift", 24, 0.10},
	{"arima", 0, 0.10},
	{"analog10", 0, 0.10},
}

// hybridForecast blends the component paths. Components that cannot be computed
// are dropped and the remaining weights renormalised, so a missing model never
// silently contributes a zero.
func hybridForecast(bars []binance.Kline, horizon int) ([]float64, bool) {
	out := make([]float64, horizon)
	var totalW float64
	for _, b := range hybridBase {
		path, ok := pathForLookback(b.method, bars, horizon, b.lookback)
		if !ok || len(path) != horizon {
			continue
		}
		w := b.weight
		for i := 0; i < horizon; i++ {
			if math.IsNaN(path[i]) || math.IsInf(path[i], 0) || path[i] <= 0 {
				w = 0
				break
			}
			out[i] += path[i] * w
		}
		if w > 0 {
			totalW += w
		} else {
			// Undo the partial accumulation for a component that failed mid-way.
			for i := 0; i < horizon; i++ {
				out[i] -= path[i] * b.weight
			}
		}
	}
	if totalW <= 0 {
		return nil, false
	}
	for i := range out {
		out[i] /= totalW
	}
	return out, true
}
