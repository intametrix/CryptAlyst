package api

import (
	"math"

	"pumpdump/backend/internal/forecastlab"
)

// Optimal scale for a model's path.
//
// Every model here forecasts a path around the last close. Whether that path is
// worth trusting is measurable: scale its departure from the last close by a
// factor λ and ask which λ minimised the error on recent, already-resolved
// windows. λ=0 collapses the model to "hold the current price", λ=1 uses it as
// published, and values between shrink an overconfident path.
//
// This is the closest a forecast can get given the model: for each model it is
// the error-minimising scalar, chosen only from data that had already resolved
// before the window being predicted.
type calibration struct {
	Lambda float64 `json:"lambda"`
	MAE    float64 `json:"mae_fitted"`
	N      int     `json:"windows"`
}

// fitLambda searches λ over prior windows and returns the best one.
func fitLambda(name string, closes []float64, horizon int, windows int) (calibration, bool) {
	if windows < 2 || horizon < 2 {
		return calibration{}, false
	}
	// The furthest cut leaves room for `horizon` actual bars plus training data.
	minTrain := 60
	type win struct {
		trainEnd int
	}
	cuts := make([]win, 0, windows)
	last := len(closes) - horizon
	for k := 0; k < windows; k++ {
		end := last - k*horizon
		if end-minTrain < 30 {
			break
		}
		cuts = append(cuts, win{trainEnd: end})
	}
	if len(cuts) < 2 {
		return calibration{}, false
	}

	// Paths for every prior cut, computed by the same model implementation.
	type sample struct {
		base  float64
		path  []float64
		truth []float64
	}
	samples := make([]sample, 0, len(cuts))
	for _, c := range cuts {
		train := closes[:c.trainEnd]
		if len(train) < minTrain || c.trainEnd+horizon > len(closes) {
			continue
		}
		path, ok := forecastlab.Forecast(name, train, horizon)
		if !ok || len(path) < horizon {
			continue
		}
		truth := closes[c.trainEnd : c.trainEnd+horizon]
		samples = append(samples, sample{base: closeOf(train), path: path, truth: truth})
	}
	if len(samples) < 2 {
		return calibration{}, false
	}

	bestLambda := 1.0
	bestMAE := math.Inf(1)
	for l := 0.0; l <= 1.0001; l += 0.05 {
		var sum float64
		var n int
		for _, s := range samples {
			for i := 0; i < horizon && i < len(s.truth); i++ {
				pred := s.base + l*(s.path[i]-s.base)
				sum += math.Abs(pred - s.truth[i])
				n++
			}
		}
		if n == 0 {
			continue
		}
		mae := sum / float64(n)
		if mae < bestMAE {
			bestMAE = mae
			bestLambda = l
		}
	}
	return calibration{Lambda: bestLambda, MAE: bestMAE, N: len(samples)}, true
}

func closeOf(v []float64) float64 {
	if len(v) == 0 {
		return 0
	}
	return v[len(v)-1]
}

// applyLambda scales a path's departure from the last close.
func applyLambda(path []float64, base, lambda float64) []float64 {
	if lambda == 1 || len(path) == 0 {
		return path
	}
	out := make([]float64, len(path))
	for i, v := range path {
		out[i] = base + lambda*(v-base)
	}
	return out
}
