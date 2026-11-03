package api

import (
	"context"
	"math"
	"net/http"
	"time"

	"pumpdump/backend/internal/forecastlab"
)

// Does a new input actually help?
//
// The only honest way to answer is the same test the price models face: hold out
// the last bars, forecast them from the training window, and compare the error.
// A feature model that cannot beat flat-at-last-close on that test is not an
// improvement, however plausible the mechanism sounds.

// fitDrift regresses the forward return on a standardised signal, using only the
// windows that end before the test cut.
func fitDrift(signal []float64, closes []float64, end, horizon, warmup int) (beta float64, ok bool) {
	var sx, sy, sxy, sxx float64
	n := 0
	for i := warmup; i+horizon < end; i++ {
		x := signal[i]
		y := closes[i+horizon]/closes[i] - 1
		if closes[i] == 0 || math.IsNaN(x) || math.IsInf(x, 0) {
			continue
		}
		sx += x
		sy += y
		sxy += x * y
		sxx += x * x
		n++
	}
	if n < 12 {
		return 0, false
	}
	fn := float64(n)
	den := fn*sxx - sx*sx
	if math.Abs(den) < 1e-12 {
		return 0, false
	}
	return (fn*sxy - sx*sy) / den, true
}

// featureSignal turns one published series into a comparable number per bar.
func featureSignal(name string, pts []FeaturePoint, barTimes []int64) []float64 {
	out := make([]float64, len(barTimes))
	idx := 0
	for i, t := range barTimes {
		for idx+1 < len(pts) && pts[idx+1].T <= t {
			idx++
		}
		if len(pts) == 0 {
			continue
		}
		p := pts[idx]
		switch name {
		case "funding":
			out[i] = p.Funding
		case "open_interest":
			out[i] = p.OpenIntChg
		case "order_flow":
			out[i] = p.TakerRatio - 0.5
		}
	}
	return zscoreSafe(out)
}

func zscoreSafe(v []float64) []float64 {
	n := len(v)
	if n == 0 {
		return v
	}
	mean := 0.0
	for _, x := range v {
		mean += x
	}
	mean /= float64(n)
	var sd float64
	for _, x := range v {
		sd += (x - mean) * (x - mean)
	}
	sd = math.Sqrt(sd / float64(n))
	out := make([]float64, n)
	if sd < 1e-12 {
		return out
	}
	for i, x := range v {
		out[i] = (x - mean) / sd
	}
	return out
}

// handleFeatureForecast grades the feature models against the price-only ones on
// the same window, so the comparison cannot be argued about.
func (s *Server) handleFeatureForecast(w http.ResponseWriter, r *http.Request) {
	symbol := paramSymbol(r)
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := paramInterval(r, "1h")
	horizon := queryInt(r, "horizon", 24)
	if horizon < 1 || horizon > 720 {
		horizon = 24
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	// labSeries' second value reports whether the series was freshly fetched, not
	// whether it is usable: a cache hit returns good bars with false. Judging by
	// length alone is what makes this work, and the same mistake was fixed in the
	// continuation handler.
	bars, _ := s.labSeries(ctx, symbol, interval, 400)
	if len(bars) < policyFor(interval).minBarsFor(horizon) {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "error": "not enough price history to grade features",
			"bars": len(bars), "need": horizon + 80,
		})
		return
	}
	feats, ferr := s.fetchFeatures(ctx, symbol, interval)
	if ferr != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "symbol": symbol, "interval": interval,
			"error": "futures features unavailable: " + ferr.Error(),
		})
		return
	}

	closes := make([]float64, len(bars))
	times := make([]int64, len(bars))
	for i, b := range bars {
		closes[i] = b.V
		times[i] = b.T
	}
	cut := len(closes) - horizon
	actual := closes[cut:]

	mape := func(path []float64) float64 {
		sum := 0.0
		for i := 0; i < horizon && i < len(path); i++ {
			a := actual[i]
			if a == 0 {
				continue
			}
			sum += math.Abs(path[i]-a) / math.Abs(a)
		}
		return 100 * sum / float64(horizon)
	}

	// Price-only baseline: the flat line, which is what a feature model must beat.
	naive := make([]float64, horizon)
	for i := range naive {
		naive[i] = closes[cut-1]
	}

	rows := []featureRow{{Name: "naive", Label: "price only, held flat", MAPE: mape(naive)}}

	for _, spec := range []struct{ name, label string }{
		{"order_flow", "taker buy/sell imbalance"},
		{"funding", "funding rate"},
		{"open_interest", "open interest change"},
	} {
		sig := featureSignal(spec.name, feats, times)
		beta, ok := fitDrift(sig, closes, cut, horizon, 60)
		if !ok {
			rows = append(rows, featureRow{Name: spec.name, Label: spec.label, MAPE: math.NaN()})
			continue
		}
		path := make([]float64, horizon)
		base := closes[cut-1]
		for i := range path {
			// The drift is applied progressively; the path still starts at the
			// last close, so no model can win by teleporting.
			drift := beta * sig[cut-1] * float64(i+1) / float64(horizon)
			path[i] = base * (1 + drift)
		}
		m := mape(path)
		rows = append(rows, featureRow{
			Name: spec.name, Label: spec.label, MAPE: m, Beta: beta,
			Samples: cut - 60 - horizon, Helped: m < rows[0].MAPE,
		})
	}

	// And the price-only ranking, for context.
	study := forecastlab.EvaluateWindow(symbol, interval, bars, horizon, 8, 40, 0)

	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "symbol": symbol, "interval": interval, "horizon": horizon,
		"bars": len(bars), "feature_points": len(feats),
		"rows": rows, "best_price_model": study.Best,
		"verdict": verdict(rows),
	})
}

// featureRow is one graded input on the held-out window.
type featureRow struct {
	Name    string  `json:"name"`
	Label   string  `json:"label"`
	MAPE    float64 `json:"mape"`
	Beta    float64 `json:"beta"`
	Samples int     `json:"samples"`
	Helped  bool    `json:"helped"`
}

func verdict(rows []featureRow) string {
	helped := 0
	for _, r := range rows[1:] {
		if r.Helped {
			helped++
		}
	}
	if helped == 0 {
		return "no feature input beat a flat line on this window"
	}
	return "at least one feature input beat a flat line on this window"
}
