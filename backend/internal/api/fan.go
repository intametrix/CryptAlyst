package api

import (
	"math"
	"math/rand"
	"sort"
)

// bootstrapFan is the range of paths the price could plausibly take, built by
// resampling the symbol's own one-bar returns.
//
// A single forecast line cannot have "shape" — the best estimate of a future
// price is a level, and pretending otherwise would be fitting noise. What does
// have shape is the uncertainty: a bootstrap of the return distribution produces
// a fan that widens with the horizon and wobbles the way this symbol wobbles.
// The seed is fixed so the same history always yields the same fan.
func bootstrapFan(closes []float64, horizon int, sims int) (p10, p25, p50, p75, p90 []float64, ok bool) {
	n := len(closes)
	if n < 60 || horizon < 2 {
		return nil, nil, nil, nil, nil, false
	}
	if sims < 100 {
		sims = 400
	}
	rets := make([]float64, 0, n-1)
	for i := 1; i < n; i++ {
		if closes[i-1] != 0 {
			r := closes[i]/closes[i-1] - 1
			if !math.IsNaN(r) && !math.IsInf(r, 0) && math.Abs(r) < 0.5 {
				rets = append(rets, r)
			}
		}
	}
	if len(rets) < 40 {
		return nil, nil, nil, nil, nil, false
	}

	base := closes[n-1]
	rng := rand.New(rand.NewSource(0x5EED))
	paths := make([][]float64, sims)
	for s := 0; s < sims; s++ {
		path := make([]float64, horizon)
		level := base
		for i := 0; i < horizon; i++ {
			level *= 1 + rets[rng.Intn(len(rets))]
			path[i] = level
		}
		paths[s] = path
	}

	quant := func(q float64) []float64 {
		out := make([]float64, horizon)
		col := make([]float64, sims)
		for i := 0; i < horizon; i++ {
			for s := 0; s < sims; s++ {
				col[s] = paths[s][i]
			}
			sort.Float64s(col)
			idx := int(q * float64(sims-1))
			if idx < 0 {
				idx = 0
			}
			if idx >= sims {
				idx = sims - 1
			}
			out[i] = col[idx]
		}
		return out
	}

	return quant(0.10), quant(0.25), quant(0.50), quant(0.75), quant(0.90), true
}

// fanFromQuantiles prefers the model's own predictive quantiles over a
// bootstrap: a foundation model produces them from what it learned about
// forecasts, which is strictly more information than resampling our history.
// It falls back to the bootstrap when the model publishes none.
func fanFromQuantiles(q map[string][]float64, horizon int, closes []float64) any {
	get := func(level string) []float64 {
		v, ok := q[level]
		if !ok || len(v) < horizon {
			return nil
		}
		return v[:horizon]
	}
	p10, p90 := get("0.1"), get("0.9")
	if p10 != nil && p90 != nil {
		median := make([]float64, horizon)
		mid := get("0.5")
		for i := 0; i < horizon; i++ {
			if mid != nil {
				median[i] = mid[i]
			} else {
				median[i] = (p10[i] + p90[i]) / 2
			}
		}
		return map[string]any{
			"p10": p10, "p90": p90, "p50": median,
			"p25": p10, "p75": p90,
			"basis": "the model's own predictive quantiles",
		}
	}
	return bootstrapAny(closes, horizon)
}

// bootstrapAny is the payload wrapper for the resampled fan.
func bootstrapAny(closes []float64, horizon int) any {
	return fanPayload(closes, horizon)
}

// samplePaths draws a handful of individual simulated futures.
//
// The fan shows the range and the forecast shows the mean; neither looks like a
// price, because a mean cannot wiggle. These are five actual resampled paths
// from the same bootstrap — each one is a plausible future that has the texture
// of the instrument, and drawing them is the honest way to show what "possible"
// looks like rather than pretending to know the curve.
func samplePaths(closes []float64, horizon int, want int) [][]float64 {
	n := len(closes)
	if n < 60 || horizon < 2 || want < 1 {
		return nil
	}
	rets := make([]float64, 0, n-1)
	for i := 1; i < n; i++ {
		if closes[i-1] != 0 {
			r := closes[i]/closes[i-1] - 1
			if !math.IsNaN(r) && !math.IsInf(r, 0) && math.Abs(r) < 0.5 {
				rets = append(rets, r)
			}
		}
	}
	if len(rets) < 40 {
		return nil
	}
	base := closes[n-1]
	rng := rand.New(rand.NewSource(0xBEEF))
	out := make([][]float64, want)
	for s := 0; s < want; s++ {
		path := make([]float64, horizon)
		level := base
		for i := 0; i < horizon; i++ {
			level *= 1 + rets[rng.Intn(len(rets))]
			path[i] = level
		}
		out[s] = path
	}
	return out
}
