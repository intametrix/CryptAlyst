package api

import (
	"context"
	"net/http"
	"sync"
	"time"
)

// Scoring the live feed with the continuation model.
//
// The model itself is fitted from resolved history (leave-one-symbol-out says it
// carries a small, real signal). This surfaces it where it can be used: each
// alert in the live list gets P(continue), computed from the same features the
// model was trained on. The fit is cached because refitting on every request
// would be wasteful and produce slightly different numbers for the same alert.

var (
	alertModelMu       sync.Mutex
	alertModelCached   *logisticFit
	alertModelFittedAt time.Time
	alertModelHorizon  int
)

const alertModelTTL = 10 * time.Minute

// fittedAlertModel returns a cached continuation model, refitting when stale.
func (s *Server) fittedAlertModel(ctx context.Context, horizon int, threshold float64) *logisticFit {
	alertModelMu.Lock()
	defer alertModelMu.Unlock()
	if alertModelCached != nil && alertModelHorizon == horizon && time.Since(alertModelFittedAt) < alertModelTTL {
		return alertModelCached
	}
	samples, _, err := s.buildAlertSamples(ctx, horizon, threshold, 6000)
	if err != nil || len(samples) < 200 {
		return alertModelCached // keep whatever we had rather than dropping to nil
	}
	rows := make([][]float64, len(samples))
	labels := make([]float64, len(samples))
	for i, a := range samples {
		rows[i] = alertFeatureRow(a)
		labels[i] = a.Label
	}
	m := fitLogistic(rows, labels, 600, 0.3, 0.002)
	if m != nil {
		alertModelCached = m
		alertModelHorizon = horizon
		alertModelFittedAt = time.Now()
	}
	return alertModelCached
}

// alertFeatureRow is the predictor vector, in the same order the model expects.
func alertFeatureRow(a alertSample) []float64 {
	dir := 1.0
	if a.Kind == "DUMP" {
		dir = -1
	}
	return []float64{dir, a.Score, a.R6, a.R30, a.Vol, a.Since}
}

// handleAlertsScored annotates the live alert list with P(continuation).
func (s *Server) handleAlertsScored(w http.ResponseWriter, r *http.Request) {
	if s.deps.Alerts == nil {
		http.Error(w, "alerts unavailable", http.StatusServiceUnavailable)
		return
	}
	limit := queryInt(r, "limit", 50)
	if limit < 1 || limit > 200 {
		limit = 50
	}
	horizon := queryInt(r, "horizon", 24)
	threshold := 0.005

	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()

	model := s.fittedAlertModel(ctx, horizon, threshold)
	alerts := s.deps.Alerts.List(limit)

	// One series per symbol, however many alerts it has.
	series := map[string][]float64{}
	for _, a := range alerts {
		if _, ok := series[a.Symbol]; ok {
			continue
		}
		bars, _ := s.labSeries(ctx, a.Symbol, "1h", 400)
		closes := make([]float64, len(bars))
		for i, b := range bars {
			closes[i] = b.V
		}
		series[a.Symbol] = closes
	}

	out := make([]map[string]any, 0, len(alerts))
	for _, a := range alerts {
		closes := series[a.Symbol]
		prob := -1.0
		var r6, r30, vol float64
		if model != nil && len(closes) >= 40 {
			n := len(closes)
			if closes[n-7] != 0 && closes[n-31] != 0 {
				r6 = closes[n-1]/closes[n-7] - 1
				r30 = closes[n-1]/closes[n-31] - 1
			}
			var sq float64
			for j := n - 20; j < n; j++ {
				if j > 0 && closes[j-1] != 0 {
					d := closes[j]/closes[j-1] - 1
					sq += d * d
				}
			}
			vol = sqrtOf(20, sq)
			prob = model.predict(alertFeatureRow(alertSample{
				Kind: a.Kind, Score: a.Score / 100, R6: r6, R30: r30, Vol: vol, Since: 999,
			}))
		}
		out = append(out, map[string]any{
			"symbol": a.Symbol, "kind": a.Kind, "score": a.Score, "ts_ms": a.TSMS,
			"severity": a.Severity, "price": a.Price,
			"continuation": prob,
			"horizon_bars": horizon,
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"ok": model != nil, "alerts": out, "horizon": horizon,
		"basis": "leave-one-symbol-out logistic model; small effect, ranks rather than predicts",
	})
}

// sqrtOf is the volatility helper: the square root of the mean of squares.
func sqrtOf(n int, sumSquares float64) float64 {
	if n <= 0 {
		return 0
	}
	v := sumSquares / float64(n)
	if v <= 0 {
		return 0
	}
	// Newton's method keeps this dependency-free.
	x := v
	for i := 0; i < 20; i++ {
		x = 0.5 * (x + v/x)
	}
	return x
}
