package api

import (
	"context"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/forecastlab"
)

// Rolling backtest over a long window.
//
// A single forecast at the end of the series says very little. This walks the
// whole period: at every bar it forecasts the next `horizon` bars using only the
// data available at that moment, then records what actually happened. The result
// is a forecast line and an actual line spanning days, plus the error of every
// step — which is what "did this model work" actually means.

// backtestPoint is one bar of the reconstructed series.
type backtestPoint struct {
	T        int64   `json:"t"`
	Forecast float64 `json:"forecast"`
	Actual   float64 `json:"actual"`
}

// pathFor computes a model's forecast path from bars up to the end.
// pathForLookback is kept as a thin alias so callers read naturally; the model
// itself is defined once, in the registry.
func pathForLookback(method string, bars []binance.Kline, horizon, lookback int) ([]float64, bool) {
	return modelPath(method, bars, horizon, lookback)
}

func pathFor(method string, bars []binance.Kline, horizon int) ([]float64, bool) {
	closes := make([]float64, len(bars))
	for i, b := range bars {
		closes[i] = b.Close
	}
	switch method {
	case "vwap":
		return vwapForecast(bars, horizon, 24)
	case "vwtrend":
		return vwTrend(bars, horizon, 50)
	case "vwdrift":
		return vwapDrift(bars, horizon, 24)
	case "mlr":
		return mlrForecast(bars, horizon)
	}
	pts, ok := forecastlab.Forecast(method, closes, horizon)
	return pts, ok
}

// handleBacktest walks a window and returns every forecast against its outcome.
func (s *Server) handleBacktest(w http.ResponseWriter, r *http.Request) {
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
	barsWanted := queryInt(r, "bars", 240) // ten days at 1h
	if barsWanted < 100 {
		barsWanted = 100
	}
	if barsWanted > 900 {
		barsWanted = 900
	}
	// Explicit dates win over a bar count: a backtest is usually a period, not a
	// number of candles.
	fromMS := queryInt64(r, "from_ms", 0)
	toMS := queryInt64(r, "to_ms", 0)
	method := strings.TrimSpace(r.URL.Query().Get("method"))
	if method == "" {
		method = "vwap"
	}
	lookback := queryInt(r, "lookback", 24)
	if lookback < 2 || lookback > 400 {
		lookback = 24
	}

	ctx, cancel := context.WithTimeout(r.Context(), 120*time.Second)
	defer cancel()
	if s.deps.Market == nil {
		fail(w, "market client unavailable")
		return
	}
	bars, err := s.barsFor(ctx, symbol, interval, barsWanted+horizon+60)
	// Sub-minute frames can only reach a few minutes back, so the minimum has to
	// scale with the frame rather than assume an hour of history.
	minBars := 120 + horizon
	if interval == "5s" {
		minBars = 40 + horizon
	}
	if interval == "30s" {
		minBars = 18 + horizon
	}
	if err != nil || len(bars) < minBars {
		fail(w, "not enough bars for a backtest")
		return
	}

	if fromMS > 0 || toMS > 0 {
		kept := make([]binance.Kline, 0, len(bars))
		for _, b := range bars {
			if fromMS > 0 && b.OpenTime < fromMS {
				continue
			}
			if toMS > 0 && b.OpenTime > toMS {
				continue
			}
			kept = append(kept, b)
		}
		if len(kept) >= 120+horizon {
			bars = kept
		}
	}

	// Start far enough in that every model has its warm-up history.
	start := 60
	if len(bars)-horizon-10 < start {
		start = len(bars) - horizon - 10
	}
	if start < 5 {
		start = 5
	}
	last := len(bars) - horizon
	if last <= start {
		fail(w, "window too short")
		return
	}

	// Every bar gets a forecast, taken from the most recent model call that
	// covered it: models are re-run once per horizon step and their path fills
	// the following bars. Re-running per bar would be 24x the work for the same
	// line.
	forecast := make([]float64, len(bars))
	filled := make([]bool, len(bars))
	for i := start; i < last; i += horizon {
		path, ok := pathForLookback(method, bars[:i+1], horizon, lookback)
		if !ok || len(path) != horizon {
			continue
		}
		for j := 0; j < horizon && i+1+j < len(bars); j++ {
			forecast[i+1+j] = path[j]
			filled[i+1+j] = true
		}
	}

	pts := make([]backtestPoint, 0, len(bars)-start)
	var sumAbs, sumPct, sumSq, maxAbs float64
	var n int
	var mean float64
	for i := start; i < len(bars); i++ {
		if !filled[i] {
			continue
		}
		a := bars[i].Close
		f := forecast[i]
		if a == 0 {
			continue
		}
		pts = append(pts, backtestPoint{T: bars[i].OpenTime, Forecast: f, Actual: a})
		e := math.Abs(f - a)
		sumAbs += e
		sumSq += e * e
		sumPct += e / a
		if e > maxAbs {
			maxAbs = e
		}
		mean += a
		n++
	}
	// A thirty-second frame only holds a few minutes, so demanding twenty points
	// would make the frame permanently untestable. Grade what exists.
	minPoints := 20
	if interval == "5s" || interval == "30s" {
		minPoints = 6
	}
	if n < minPoints {
		fail(w, "backtest produced too few points")
		return
	}
	mean /= float64(n)

	// R² against the mean of the actual series: 1.0 would be perfect, 0 is no
	// better than predicting the average price, below 0 is worse than that.
	var ssRes, ssTot float64
	for _, p := range pts {
		ssRes += (p.Forecast - p.Actual) * (p.Forecast - p.Actual)
		ssTot += (p.Actual - mean) * (p.Actual - mean)
	}
	r2 := 0.0
	if ssTot > 0 {
		r2 = 1 - ssRes/ssTot
	}
	// The flat-line reference: hold the price from `start`.
	var flatAbs, flatPct float64
	base := bars[start].Close
	for i := start; i < len(bars); i++ {
		if !filled[i] || bars[i].Close == 0 {
			continue
		}
		e := math.Abs(base - bars[i].Close)
		flatAbs += e
		flatPct += e / bars[i].Close
	}
	cnt := float64(len(pts))

	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "symbol": symbol, "interval": interval, "method": method, "lookback": lookback,
		"horizon": horizon, "bars": len(bars), "points_n": len(pts),
		"from_ms": pts[0].T, "to_ms": pts[len(pts)-1].T,
		"span_days": float64(pts[len(pts)-1].T-pts[0].T) / 86_400_000,
		"points":    pts,
		"metrics": map[string]any{
			"mape_pct":      100 * sumPct / cnt,
			"mae":           sumAbs / cnt,
			"rmse":          math.Sqrt(sumSq / cnt),
			"max_error":     maxAbs,
			"r2":            r2,
			"flat_mape_pct": 100 * flatPct / cnt,
			"flat_mae":      flatAbs / cnt,
			"skill_vs_flat": 1 - (sumAbs/cnt)/math.Max(flatAbs/cnt, 1e-9),
		},
	})
}

// walkBacktest runs the rolling forecast and returns points plus the metrics.
func walkBacktest(bars []binance.Kline, method string, horizon, lookback int) (pts []backtestPoint, metrics map[string]any, ok bool) {
	start := 60
	last := len(bars) - horizon
	if last <= start {
		return nil, nil, false
	}
	forecast := make([]float64, len(bars))
	filled := make([]bool, len(bars))
	for i := start; i < last; i += horizon {
		path, pok := pathForLookback(method, bars[:i+1], horizon, lookback)
		if !pok || len(path) != horizon {
			continue
		}
		for j := 0; j < horizon && i+1+j < len(bars); j++ {
			forecast[i+1+j] = path[j]
			filled[i+1+j] = true
		}
	}
	var sumAbs, sumSq, sumPct, maxAbs, mean float64
	var n int
	for i := start; i < len(bars); i++ {
		if !filled[i] || bars[i].Close == 0 {
			continue
		}
		a := bars[i].Close
		f := forecast[i]
		pts = append(pts, backtestPoint{T: bars[i].OpenTime, Forecast: f, Actual: a})
		e := math.Abs(f - a)
		sumAbs += e
		sumSq += e * e
		sumPct += e / a
		if e > maxAbs {
			maxAbs = e
		}
		mean += a
		n++
	}
	if n < 20 {
		return nil, nil, false
	}
	mean /= float64(n)
	var ssRes, ssTot float64
	for _, p := range pts {
		ssRes += (p.Forecast - p.Actual) * (p.Forecast - p.Actual)
		ssTot += (p.Actual - mean) * (p.Actual - mean)
	}
	r2 := 0.0
	if ssTot > 0 {
		r2 = 1 - ssRes/ssTot
	}
	base := bars[start].Close
	var flatAbs, flatPct float64
	for i := start; i < len(bars); i++ {
		if !filled[i] || bars[i].Close == 0 {
			continue
		}
		flatAbs += math.Abs(base - bars[i].Close)
		flatPct += math.Abs(base-bars[i].Close) / bars[i].Close
	}
	cnt := float64(n)
	return pts, map[string]any{
		"mape_pct":      100 * sumPct / cnt,
		"mae":           sumAbs / cnt,
		"rmse":          math.Sqrt(sumSq / cnt),
		"max_error":     maxAbs,
		"r2":            r2,
		"flat_mape_pct": 100 * flatPct / cnt,
		"flat_mae":      flatAbs / cnt,
		"skill_vs_flat": 1 - (sumAbs/cnt)/math.Max(flatAbs/cnt, 1e-9),
	}, true
}

// handleOptimizeBacktest ranks candidate models and parameters by backtest, so
// the choice of model is evidence rather than preference.
func (s *Server) handleOptimizeBacktest(w http.ResponseWriter, r *http.Request) {
	symbol := paramSymbol(r)
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := paramInterval(r, "1h")
	horizon := queryInt(r, "horizon", 24)
	barsWanted := queryInt(r, "bars", 240)
	if barsWanted < 100 {
		barsWanted = 100
	}
	if barsWanted > 900 {
		barsWanted = 900
	}

	ctx, cancel := context.WithTimeout(r.Context(), 180*time.Second)
	defer cancel()
	if s.deps.Market == nil {
		fail(w, "market client unavailable")
		return
	}
	bars, err := s.barsFor(ctx, symbol, interval, barsWanted+horizon+60)
	if err != nil || len(bars) < 140+horizon {
		fail(w, "not enough bars to optimise")
		return
	}

	type cand struct {
		method   string
		lookback int
	}
	cands := []cand{}
	for _, m := range []string{"naive", "super", "hybrid", "vwap", "vwtrend", "vwdrift", "arima", "analog10", "trend50", "mlr"} {
		for _, lb := range []int{12, 24, 50, 100} {
			cands = append(cands, cand{method: m, lookback: lb})
		}
	}

	type result struct {
		Method   string  `json:"method"`
		Lookback int     `json:"lookback"`
		MAPE     float64 `json:"mape_pct"`
		MAE      float64 `json:"mae"`
		R2       float64 `json:"r2"`
		Skill    float64 `json:"skill_vs_flat"`
		Points   int     `json:"points"`
	}
	results := make([]result, 0, len(cands))
	seen := map[string]bool{}
	for _, c := range cands {
		// Only the volume models actually take a lookback; skip the duplicates
		// that would otherwise appear for the rest.
		if c.method != "vwap" && c.method != "vwtrend" && c.method != "vwdrift" {
			if seen[c.method] {
				continue
			}
			seen[c.method] = true
		}
		pts, m, ok := walkBacktest(bars, c.method, horizon, c.lookback)
		if !ok {
			continue
		}
		results = append(results, result{
			Method: c.method, Lookback: c.lookback,
			MAPE: toF(m["mape_pct"]), MAE: toF(m["mae"]),
			R2: toF(m["r2"]), Skill: toF(m["skill_vs_flat"]), Points: len(pts),
		})
	}
	sort.Slice(results, func(i, j int) bool { return results[i].R2 > results[j].R2 })

	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "symbol": symbol, "interval": interval, "horizon": horizon,
		"bars": len(bars), "tested": len(results),
		"results":    results,
		"best_by_r2": results[0],
		"basis":      "rolling backtest over the window; ranked by R² against the mean price",
	})
}
