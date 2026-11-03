package api

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/forecastlab"
)

// handleForecastVerify runs a forecast on an agent over a holdout split and
// grades it against the bars that actually followed.
//
// The point is that a job result alone is just numbers: a forecast is only
// meaningful next to the truth. So the history is split, the agent is asked to
// forecast from the split point, and the reply carries the forecast, the real
// closes and the error for each step.
func (s *Server) handleForecastVerify(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) {
		return
	}
	var body struct {
		Symbol    string `json:"symbol"`
		Interval  string `json:"interval"`
		Horizon   int    `json:"horizon"`
		TrainBars int    `json:"train_bars"`
		Method    string `json:"method"`
		Agent     string `json:"agent"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(body.Symbol))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := body.Interval
	if interval == "" {
		interval = "1h"
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}
	horizon := body.Horizon
	if horizon < 1 {
		horizon = 12
	}
	trainBars := body.TrainBars
	if trainBars < 0 {
		trainBars = 0
	}
	if trainBars > 1000 {
		trainBars = 1000
	}
	if horizon > 720 {
		horizon = 720 // a month at 1h; the panel offers up to this
	}
	// "best" (or nothing) means: use whichever model currently wins the
	// walk-forward leaderboard for this symbol/interval/horizon. That is what
	// makes the drawn forecast the same one the table ranks, instead of a
	// hardcoded default nobody checked.
	savedLookback := 0
	method := body.Method
	// "saved" uses the configuration this symbol's optimisation settled on.
	if strings.EqualFold(method, "saved") {
		if cfg, ok := savedFor(body.Symbol); ok {
			method = cfg.Method
			savedLookback = cfg.Lookback
			if cfg.Horizon > 0 {
				horizon = cfg.Horizon
			}
		} else {
			method = "vwap"
		}
	}

	// Only agents that have checked in recently are candidates: a registry
	// entry outlives the process, and dispatching to a dead agent is the
	// difference between "forecast failed" and "forecast never tried".
	candidates := s.liveAgents(agentFreshMS)
	if body.Agent != "" {
		// An explicit agent is tried first, but it must still be alive.
		tried := []string{strings.TrimRight(body.Agent, "/")}
		for _, c := range candidates {
			if c != tried[0] {
				tried = append(tried, c)
			}
		}
		candidates = tried
	}
	if len(candidates) == 0 {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "no live agents — is the agent running?"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	// The same window the leaderboard ranks (400 bars, 8 cuts), widened when the
	// requested training span needs more history than that. Without this a
	// "3 month" training window would be silently truncated to what 400 bars
	// holds, which is exactly the kind of quiet lie this panel exists to avoid.
	const evalWindows = 8
	limit := 400
	if need := trainBars + horizon + 80; need > limit {
		limit = need
	}
	if limit > 1000 {
		limit = 1000
	}
	var (
		rows []binance.Kline
		err  error
	)
	if s.deps.Market == nil {
		http.Error(w, "market client unavailable", http.StatusServiceUnavailable)
		return
	}
	pts, _ := s.labSeries(ctx, symbol, interval, limit)
	if len(pts) == 0 {
		http.Error(w, "klines unavailable", http.StatusBadGateway)
		return
	}
	rows = make([]binance.Kline, len(pts))
	for i, p := range pts {
		rows[i] = binance.Kline{OpenTime: p.T, Close: p.V}
	}
	if err != nil {
		http.Error(w, "klines unavailable: "+err.Error(), http.StatusBadGateway)
		return
	}
	if len(rows) < horizon+10 {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "error": "not enough history to hold out a forecast window",
			"bars": len(rows), "horizon": horizon,
		})
		return
	}

	cut := len(rows) - horizon
	trainStart := 0
	if trainBars > 0 && cut-trainBars > 0 {
		trainStart = cut - trainBars
	}
	series := make([]map[string]any, cut-trainStart)
	for i := trainStart; i < cut; i++ {
		series[i-trainStart] = map[string]any{"t": rows[i].OpenTime, "v": rows[i].Close}
	}
	times := make([]int64, horizon)
	actual := make([]float64, horizon)
	for i := 0; i < horizon; i++ {
		times[i] = rows[cut+i].OpenTime
		actual[i] = rows[cut+i].Close
	}

	trainCloses := make([]float64, cut-trainStart)
	for i := trainStart; i < cut; i++ {
		trainCloses[i-trainStart] = rows[i].Close
	}
	if method == "" || method == "best" {
		study := forecastlab.EvaluateWindow(symbol, interval, toPoints(rows[:cut]), horizon, evalWindows, 40, trainBars)
		method = study.Best
	}
	if method == "" {
		method = "naive"
	}

	// The agent only knows ema/sma/trend. Any other winner is computed here, so
	// the chart is never forced onto a worse model just because the box cannot
	// run it.
	var forecastPts []float64
	var autoChosen map[string]any
	var quantiles map[string][]float64
	var source, jobID, usedAgent, device, compute, ferr string

	// Volume-weighted models also need the full bars (volume lives there, not in
	// the closes the agent receives), so they are computed here.
	// `auto` asks for whatever this symbol's own backtest chose.
	if s.deps.Market != nil && method == "auto" {
		if vb, verr := s.barsFor(ctx, symbol, interval, 400); verr == nil && len(vb) > 140+horizon {
			if am, alb, ok := s.autoConfig(symbol, interval, vb, horizon); ok {
				if pts, pok := pathForLookback(am, vb, horizon, alb); pok && len(pts) == horizon {
					forecastPts = pts
					source, device, compute = "platform", "cpu", "go+auto"
					autoChosen = map[string]any{"method": am, "lookback": alb}
				}
			}
		}
	}

	if s.deps.Market != nil && (method == "super" || method == "hybrid" || method == "vwap" || method == "vwtrend" || method == "vwdrift") {
		if vb, verr := s.barsFor(ctx, symbol, interval, 500); verr == nil && len(vb) > 120+horizon {
			var pts []float64
			var ok bool
			// One dispatch, shared with the backtest and the composite. A saved
			// configuration pins the lookback; 0 lets the registry default it.
			pts, ok = modelPath(method, vb, horizon, savedLookback)
			if ok && len(pts) == horizon {
				forecastPts = pts
				source, device, compute = "platform", "cpu", "go+volume"
			}
		}
	}

	// The price+volume+time model needs the full OHLCV bars, which only this
	// handler holds, so it is fitted here rather than shipped to the agent.
	if method == "mlr" && forecastPts == nil && s.deps.Market != nil {
		if mlrBars, mlrErr := s.barsFor(ctx, symbol, interval, 500); mlrErr == nil && len(mlrBars) > 120+horizon {
			if pts, ok := mlrForecast(mlrBars, horizon); ok {
				forecastPts = pts
				source, device, compute = "platform", "cpu", "go+volume+time"
			}
		}
	}

	if forecastPts == nil {
		forecastPts, quantiles, source, jobID, usedAgent, device, compute, ferr = s.runForecastJob(
			ctx, candidates, method, series, trainCloses, horizon)
	}
	if ferr != "" {
		fail(w, ferr)
		return
	}
	forecast := forecastPts
	target := usedAgent

	// Fit the error-minimising scale for this model on windows that had already
	// resolved, then apply it. This is the closest the model can get: it removes
	// overconfidence without inventing direction.
	// Calibration is for the AUTOMATIC choice only.
	//
	// The fitted scale often lands on 0, which multiplies a model's departure
	// from the last close by zero and collapses it to a flat line. For `best`
	// that is the right call — "ignore this model". But when a reader picks
	// trend50 or arima they are asking to see THAT model, and silently turning it
	// into naive is a bug: five different models were returning the identical
	// number.
	cal, calOK := fitLambda(method, closeSeries(rows[:cut]), horizon, 5)
	applyCalibration := calOK && method == "best"
	if applyCalibration {
		forecast = applyLambda(forecast, rows[cut-1].Close, cal.Lambda)
	}
	_ = source

	// Grade it.
	perPoint := make([]map[string]any, 0, len(forecast))
	var sumAbs, sumSq, sumPct, sumSigned, maxAbs float64
	prev := rows[cut-1].Close
	hits := 0
	for i, f := range forecast {
		if i >= len(actual) {
			break
		}
		a := actual[i]
		e := f - a
		ae := math.Abs(e)
		sumAbs += ae
		sumSq += e * e
		sumSigned += e
		if a != 0 {
			sumPct += ae / math.Abs(a)
		}
		if ae > maxAbs {
			maxAbs = ae
		}
		if (f-prev)*(a-prev) > 0 {
			hits++
		}
		prev = a
		// Values are sent unrounded. A fixed 2-decimal rounding silently turns
		// every price below 0.005 into 0, which is exactly what happened to
		// BEAMXUSDT: the chart drew a flat line at zero and MAE reported 0.
		perPoint = append(perPoint, map[string]any{
			"t": times[i], "forecast": f, "actual": a,
			"error": e, "abs_error": ae,
			"error_pct": round3(100 * ae / math.Abs(a)),
		})
	}
	n := float64(len(perPoint))
	if n == 0 {
		failAgent(w, "nothing to grade", target)
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"ok":         true,
		"symbol":     symbol,
		"interval":   interval,
		"method":     method,
		"agent":      target,
		"source":     source,
		"device":     device,
		"compute":    compute,
		"job":        jobID,
		"horizon":    len(perPoint),
		"train_bars": len(trainCloses),
		"last_train": rows[cut-1].Close,
		// The anchor: the last bar the model actually saw. Without it on the
		// chart the forecast line starts in mid-air, because the plotted actual
		// series begins one bar later.
		"anchor_t":     rows[cut-1].OpenTime,
		"anchor_price": rows[cut-1].Close,
		// Context: the bars leading up to the forecast. Without them the chart
		// only shows the hold-out window, so there is no past for the forecast to
		// extend from and it does not read as a forecast at all.
		"history":       orEmptyF(historyCloses(rows, cut, 120)),
		"history_times": orEmptyI(historyTimes(rows, cut, 120)),
		"times":         times[:len(perPoint)],
		"forecast":      forecast[:len(perPoint)],
		"actual":        actual[:len(perPoint)],
		"points":        perPoint,
		"fan":           nonNilFan(fanFromQuantiles(quantiles, horizon, trainCloses)),
		// Five individual simulated futures: what "possible" looks like, as
		// opposed to the mean, which cannot wiggle.
		"paths":       samplePaths(trainCloses, horizon, 5),
		"auto_config": autoChosen,
		"calibration": func() any {
			if !calOK {
				return nil
			}
			return map[string]any{
				"lambda": cal.Lambda, "windows": cal.N, "basis": "error-minimising scale on resolved windows",
			}
		}(),
		"errors": map[string]any{
			"mae":       sumAbs / n,
			"rmse":      math.Sqrt(sumSq / n),
			"mape_pct":  round3(100 * sumPct / n),
			"bias":      sumSigned / n,
			"max_error": maxAbs,
			"direction": round2(100 * float64(hits) / n),
		},
	})
}

// toF coerces a JSON number to float64.
func toF(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	case int64:
		return float64(n)
	case json.Number:
		f, _ := n.Float64()
		return f
	}
	return 0
}

func round2(v float64) float64 { return math.Round(v*100) / 100 }
func round3(v float64) float64 { return math.Round(v*1000) / 1000 }

// agentFreshMS is how long a registry entry counts as live. The agent beacons
// every 30s, so three missed beacons means it is gone.
const agentFreshMS int64 = 90_000

// liveAgents lists agent hosts that checked in recently, least loaded first.
func (s *Server) liveAgents(freshMS int64) []string {
	if s.deps.Agents == nil {
		return nil
	}
	now := time.Now().UnixMilli()
	list := s.deps.Agents.List()
	hosts := make([]string, 0, len(list))
	for _, a := range list {
		if a.LastSeen == 0 || now-a.LastSeen > freshMS {
			continue
		}
		if a.Host == "" {
			continue
		}
		hosts = append(hosts, strings.TrimRight(a.Host, "/"))
	}
	return hosts
}

// runForecastJob produces the forecast for a chosen model.
//
// The agent can only run ema/sma/trend. A winner outside that set is computed
// here instead, so the chart always shows the model the leaderboard actually
// ranked rather than a weaker substitute.
func (s *Server) runForecastJob(
	ctx context.Context,
	candidates []string,
	method string,
	series []map[string]any,
	trainCloses []float64,
	horizon int,
) (forecast []float64, quantiles map[string][]float64, source, jobID, agent, device, compute, errMsg string) {
	// The price+volume+time model is fitted here: it needs the full OHLCV bars,
	// which the agent's arithmetic runner never sees (it receives closes only).
	// Price+volume+time is fitted by the caller, which holds the OHLCV bars;
	// this function only sees closes.

	// ARIMA is fitted here: estimating an order by BIC and solving the recursive
	// forecast is a numerical job the Go lab does deterministically, and the
	// agent's runner has no ARIMA implementation to route to.
	if method == "arima" {
		if pts, ok := forecastlab.Forecast("arima", trainCloses, horizon); ok && len(pts) == horizon {
			return pts, nil, "platform", "", "", "cpu", "go", ""
		}
		return nil, nil, "", "", "", "", "", "arima fit failed"
	}

	// Otherwise the agent owns the forecast: it has the GPU, and it now
	// implements every model the leaderboard can rank. The platform only
	// computes locally as a fallback when no agent can take the job.
	job := map[string]any{
		"model": method, "type": "forecast", "device": "gpu",
		"payload": map[string]any{"series": series, "horizon": horizon, "method": method},
	}
	jobBody, _ := json.Marshal(job)

	var lastErr string
	agentDevice, agentCompute := "", ""
	for _, cand := range candidates {
		resp, err := http.Post(cand+"/jobs", "application/json", bytes.NewReader(jobBody))
		if err != nil {
			lastErr = err.Error()
			continue
		}
		var created map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&created)
		resp.Body.Close()
		id, _ := created["id"].(string)
		if id == "" {
			lastErr = "agent rejected the job"
			continue
		}

		// Poll for the result.
		deadline := time.Now().Add(20 * time.Second)
		for time.Now().Before(deadline) {
			select {
			case <-ctx.Done():
			case <-time.After(400 * time.Millisecond):
			}
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, cand+"/jobs/"+id, nil)
			if err != nil {
				break
			}
			rr, err := http.DefaultClient.Do(req)
			if err != nil {
				break
			}
			var st map[string]any
			_ = json.NewDecoder(rr.Body).Decode(&st)
			rr.Body.Close()
			status, _ := st["status"].(string)
			if status == "failed" {
				msg, _ := st["error"].(string)
				if msg == "" {
					msg = "the agent reported a failure"
				}
				return nil, nil, "", id, cand, "", "", msg
			}
			if status != "done" {
				continue
			}
			result, _ := st["result"].(map[string]any)
			if d, ok := result["device"].(string); ok {
				agentDevice = d
			}
			if c, ok := result["compute"].(string); ok {
				agentCompute = c
			}
			raw, _ := result["forecast"].([]any)
			out := make([]float64, 0, len(raw))
			for _, v := range raw {
				out = append(out, toF(v))
			}
			if len(out) == 0 {
				return nil, nil, "", id, cand, "", "", "the agent returned no forecast points"
			}
			// Quantiles, when the model produces them, are the model's own
			// calibrated uncertainty — better than any band we could synthesise.
			qs := map[string][]float64{}
			if qm, ok := result["quantiles"].(map[string]any); ok {
				for level, v := range qm {
					arr, _ := v.([]any)
					vals := make([]float64, 0, len(arr))
					for _, x := range arr {
						vals = append(vals, toF(x))
					}
					if len(vals) > 0 {
						qs[level] = vals
					}
				}
			}
			return out, qs, "agent", id, cand, agentDevice, agentCompute, ""
		}
		lastErr = "agent did not finish in time"
	}
	// No agent could do it: compute here rather than fail outright.
	if pts, ok := forecastlab.Forecast(method, trainCloses, horizon); ok {
		return pts, nil, "platform", "", "", "cpu", "go", ""
	}
	if lastErr == "" {
		lastErr = "no agent accepted the job"
	}
	return nil, nil, "", "", "", "", "", lastErr
}

// toPoints converts bars into the lab's series type.
func toPoints(rows []binance.Kline) []forecastlab.Point {
	out := make([]forecastlab.Point, 0, len(rows))
	for _, k := range rows {
		out = append(out, forecastlab.Point{T: k.OpenTime, V: k.Close})
	}
	return out
}

// handleForecastOptimize runs the agent's hyperparameter search and returns the
// winning parameters. The search itself lives on the agent because it is dense
// array work and the accelerator is there, not here.
func (s *Server) handleForecastOptimize(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) {
		return
	}
	var body struct {
		Symbol     string `json:"symbol"`
		Interval   string `json:"interval"`
		Horizon    int    `json:"horizon"`
		TrainBars  int    `json:"train_bars"`
		Windows    int    `json:"windows"`
		Iterations int    `json:"iterations"`
		Agent      string `json:"agent"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(body.Symbol))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := body.Interval
	if interval == "" {
		interval = "1h"
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}
	if body.Horizon < 1 {
		body.Horizon = 12
	}
	if body.Horizon > 500 {
		body.Horizon = 500
	}
	windows := body.Windows
	if windows < 2 {
		windows = 8
	}
	if windows > 30 {
		windows = 30
	}
	trainBars := body.TrainBars
	if trainBars < 0 {
		trainBars = 0
	}
	iterations := body.Iterations
	if iterations < 1 {
		iterations = 50 // the optimiser is iterative; 50 rounds is the default
	}
	if iterations > 500 {
		iterations = 500
	}

	candidates := s.liveAgents(agentFreshMS)
	if body.Agent != "" {
		candidates = append([]string{strings.TrimRight(body.Agent, "/")}, candidates...)
	}
	if len(candidates) == 0 {
		failStatus(w, http.StatusServiceUnavailable, "no live agents")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
	defer cancel()

	limit := 400
	if need := trainBars + body.Horizon + 80; need > limit {
		limit = need
	}
	if limit > 1000 {
		limit = 1000
	}
	pts, _ := s.labSeries(ctx, symbol, interval, limit)
	if len(pts) < 80 {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "error": "need >= 80 bars of history to optimise", "bars": len(pts),
		})
		return
	}
	series := make([]map[string]any, 0, len(pts))
	for _, p := range pts {
		series = append(series, map[string]any{"t": p.T, "v": p.V})
	}

	job := map[string]any{
		"model": "optimize", "type": "optimize", "device": "gpu",
		"payload": map[string]any{"series": series, "horizon": body.Horizon, "windows": windows, "iterations": iterations},
	}
	jobBody, _ := json.Marshal(job)

	var lastErr string
	for _, cand := range candidates {
		resp, err := http.Post(cand+"/jobs", "application/json", bytes.NewReader(jobBody))
		if err != nil {
			lastErr = err.Error()
			continue
		}
		var created map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&created)
		resp.Body.Close()
		id, _ := created["id"].(string)
		if id == "" {
			lastErr = "agent rejected the job"
			continue
		}

		deadline := time.Now().Add(75 * time.Second)
		for time.Now().Before(deadline) {
			select {
			case <-ctx.Done():
			case <-time.After(500 * time.Millisecond):
			}
			req, _ := http.NewRequestWithContext(ctx, http.MethodGet, cand+"/jobs/"+id, nil)
			rr, err := http.DefaultClient.Do(req)
			if err != nil {
				break
			}
			var st map[string]any
			_ = json.NewDecoder(rr.Body).Decode(&st)
			rr.Body.Close()
			status, _ := st["status"].(string)
			if status == "failed" {
				msg, _ := st["error"].(string)
				failAgent(w, msg, cand)
				return
			}
			if status != "done" {
				continue
			}
			result, _ := st["result"].(map[string]any)
			result["agent"] = cand
			result["job"] = id
			result["symbol"] = symbol
			result["interval"] = interval
			writeJSON(w, http.StatusOK, result)
			return
		}
		lastErr = "agent did not finish the optimisation in time"
	}
	fail(w, lastErr)
}

// fanPayload packages the bootstrap fan, or nil when there is too little history
// to resample. Nil rather than zeros: the chart must be able to tell "no fan"
// from "a fan that happens to be zero".
func fanPayload(closes []float64, horizon int) any {
	p10, p25, p50, p75, p90, ok := bootstrapFan(closes, horizon, 400)
	if !ok {
		return nil
	}
	return map[string]any{
		"p10": p10, "p25": p25, "p50": p50, "p75": p75, "p90": p90,
		"simulations": 400,
		"basis":       "bootstrap of this symbol's one-bar returns",
	}
}

// closeSeries is the closing price of every bar, for the calibration fit.
func closeSeries(rows []binance.Kline) []float64 {
	out := make([]float64, len(rows))
	for i := range rows {
		out[i] = rows[i].Close
	}
	return out
}

// historyCloses returns up to n closes ending at the cut (exclusive).
func historyCloses(rows []binance.Kline, cut, n int) []float64 {
	start := cut - n
	if start < 0 {
		start = 0
	}
	out := make([]float64, 0, cut-start)
	for i := start; i < cut; i++ {
		out = append(out, rows[i].Close)
	}
	return out
}

// historyTimes mirrors historyCloses for the x-axis.
func historyTimes(rows []binance.Kline, cut, n int) []int64 {
	start := cut - n
	if start < 0 {
		start = 0
	}
	out := make([]int64, 0, cut-start)
	for i := start; i < cut; i++ {
		out = append(out, rows[i].OpenTime)
	}
	return out
}

// The JSON below reaches a browser that reads .length on these fields. A null
// array is a crash waiting for a slow request, so empty is the only safe default.

func orEmptyF(v []float64) []float64 {
	if v == nil {
		return []float64{}
	}
	return v
}

func orEmptyI(v []int64) []int64 {
	if v == nil {
		return []int64{}
	}
	return v
}

func orEmptyPaths(v [][]float64) [][]float64 {
	if v == nil {
		return [][]float64{}
	}
	return v
}

// nonNilFan replaces a nil fan with an empty structure whose arrays are real.
func nonNilFan(v any) any {
	f, ok := v.(map[string]any)
	if !ok || f == nil {
		return map[string]any{}
	}
	for _, key := range []string{"p10", "p25", "p50", "p75", "p90"} {
		if arr, ok := f[key].([]float64); !ok || arr == nil {
			f[key] = []float64{}
		}
	}
	return f
}
