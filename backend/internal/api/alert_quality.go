package api

import (
	"bufio"
	"context"
	"encoding/json"
	"math"
	"net/http"
	"os"
	"sort"
	"time"
)

// Alert quality: does an alert predict its own continuation, and by how much?
//
// The bar-by-bar classifier failed because it asked an unconditioned question.
// An alert is already a filter, and the earlier sweep showed alerts continue
// ~8 points more often than a random bar at 24 bars. This endpoint turns that
// into a calibrated per-alert probability.
//
// The validation is the part that matters. Alerts cluster — many fire on the
// same move — and symbols differ, so the model is scored with LEAVE-ONE-SYMBOL-
// OUT cross-validation: fitted on every other symbol, graded on the one held
// out. Pooled random splits inflate this badly.

// alertSample is one resolved alert: its features at the time it fired, and
// whether the move continued.
type alertSample struct {
	Symbol string
	TS     int64
	Kind   string
	Score  float64
	R6     float64
	R30    float64
	Vol    float64
	Since  float64 // bars since the previous alert on this symbol, capped
	Label  float64
}

func (s *Server) buildAlertSamples(ctx context.Context, horizon int, threshold float64, maxAlerts int) ([]alertSample, map[string]int, error) {
	// The in-memory ring keeps only the newest alerts, which have not resolved
	// yet. Stream the file so the sample spans the window and the labels exist.
	if s.deps.AlertPath == "" {
		return nil, nil, errNoAlerts
	}
	since := time.Now().Add(-300 * time.Hour).UnixMilli()
	f, err := os.Open(s.deps.AlertPath)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()

	all := make([]Alert, 0, maxAlerts)
	seen := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) < 2 {
			continue
		}
		var a Alert
		if json.Unmarshal(line, &a) != nil {
			continue
		}
		if a.TSMS < since || (a.Kind != "PUMP" && a.Kind != "DUMP") {
			continue
		}
		// Keep a spread: every alert would be millions of rows and thousands of
		// symbol fetches, so take one in `stride`.
		seen++
		if seen%20 != 0 {
			continue
		}
		all = append(all, a)
	}
	alerts := all

	// Group by symbol so each series is fetched once.
	bySym := map[string][]Alert{}
	for _, a := range alerts {
		if a.Kind != "PUMP" && a.Kind != "DUMP" {
			continue
		}
		bySym[a.Symbol] = append(bySym[a.Symbol], a)
	}

	var out []alertSample
	perSymbol := map[string]int{}
	// Each symbol costs one series fetch; keep the busiest so the run finishes
	// inside the request budget instead of timing out on the long tail.
	symNames := make([]string, 0, len(bySym))
	for sym := range bySym {
		symNames = append(symNames, sym)
	}
	sort.Slice(symNames, func(i, j int) bool { return len(bySym[symNames[i]]) > len(bySym[symNames[j]]) })
	if len(symNames) > 24 {
		symNames = symNames[:24]
	}
	for _, sym := range symNames {
		list := bySym[sym]
		bars, _ := s.labSeries(ctx, sym, "1h", 400)
		if len(bars) < horizon+60 {
			continue
		}
		closes := make([]float64, len(bars))
		times := make([]int64, len(bars))
		for i, b := range bars {
			closes[i] = b.V
			times[i] = b.T
		}
		sort.Slice(list, func(i, j int) bool { return list[i].TSMS < list[j].TSMS })
		lastTS := int64(0)
		for _, a := range list {
			idx := -1
			for i, t := range times {
				if t >= a.TSMS {
					idx = i
					break
				}
			}
			if idx < 31 || idx+horizon >= len(closes) {
				continue
			}
			if closes[idx] == 0 || closes[idx-6] == 0 || closes[idx-30] == 0 {
				continue
			}
			move := closes[idx+horizon]/closes[idx] - 1
			dir := 1.0
			if a.Kind == "DUMP" {
				dir = -1
			}
			label := 0.0
			if dir*move >= threshold {
				label = 1
			}
			var vol float64
			for j := idx - 20; j < idx; j++ {
				if j > 0 && closes[j-1] != 0 {
					d := closes[j]/closes[j-1] - 1
					vol += d * d
				}
			}
			vol = math.Sqrt(vol / 20)
			gap := 999.0
			if lastTS != 0 {
				gap = float64(a.TSMS-lastTS) / 3_600_000
				if gap > 48 {
					gap = 48
				}
			}
			lastTS = a.TSMS
			out = append(out, alertSample{
				Symbol: sym, TS: a.TSMS, Kind: a.Kind, Score: a.Score / 100,
				R6: closes[idx]/closes[idx-6] - 1, R30: closes[idx]/closes[idx-30] - 1,
				Vol: vol, Since: gap, Label: label,
			})
		}
		perSymbol[sym] = len(out)
	}
	return out, perSymbol, nil
}

var errNoAlerts = &alertErr{"alert history unavailable"}

type alertErr struct{ msg string }

func (e *alertErr) Error() string { return e.msg }

// auc ranks the model without assuming a threshold: the probability that a
// continuing alert is scored above a non-continuing one.
func aucOf(preds, labels []float64) float64 {
	var pos, neg float64
	for _, y := range labels {
		if y == 1 {
			pos++
		} else {
			neg++
		}
	}
	if pos == 0 || neg == 0 {
		return math.NaN()
	}
	var wins float64
	for i := range preds {
		for j := range preds {
			if labels[i] == 1 && labels[j] == 0 {
				if preds[i] > preds[j] {
					wins++
				} else if preds[i] == preds[j] {
					wins += 0.5
				}
			}
		}
	}
	return wins / (pos * neg)
}

// handleAlertQuality fits the per-alert model and reports leave-one-symbol-out
// performance alongside the current alert scores.
func (s *Server) handleAlertQuality(w http.ResponseWriter, r *http.Request) {
	horizon := queryInt(r, "horizon", 24)
	if horizon < 1 || horizon > 720 {
		horizon = 24
	}
	threshold := float64(queryInt(r, "threshold_bps", 50)) / 10000

	ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
	defer cancel()

	samples, _, err := s.buildAlertSamples(ctx, horizon, threshold, 6000)
	if err != nil || len(samples) < 80 {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "error": "not enough resolved alerts to fit", "samples": len(samples),
		})
		return
	}

	toRow := func(a alertSample) []float64 {
		dir := 1.0
		if a.Kind == "DUMP" {
			dir = -1
		}
		return []float64{dir, a.Score, a.R6, a.R30, a.Vol, a.Since}
	}

	// Leave-one-symbol-out: every symbol is graded by a model that never saw it.
	syms := map[string]bool{}
	for _, a := range samples {
		syms[a.Symbol] = true
	}
	var allPreds, allLabels []float64
	folds := 0
	for held := range syms {
		var trainX [][]float64
		var trainY []float64
		var testX [][]float64
		var testY []float64
		for _, a := range samples {
			if a.Symbol == held {
				testX = append(testX, toRow(a))
				testY = append(testY, a.Label)
			} else {
				trainX = append(trainX, toRow(a))
				trainY = append(trainY, a.Label)
			}
		}
		if len(trainX) < 60 || len(testX) == 0 {
			continue
		}
		m := fitLogistic(trainX, trainY, 600, 0.3, 0.002)
		if m == nil {
			continue
		}
		folds++
		for i, x := range testX {
			allPreds = append(allPreds, m.predict(x))
			allLabels = append(allLabels, testY[i])
		}
	}

	if len(allPreds) == 0 {
		fail(w, "cross-validation produced no folds")
		return
	}

	var baseCount float64
	for _, y := range allLabels {
		baseCount += y
	}
	base := baseCount / float64(len(allLabels))
	var brier, brierBase float64
	for i, p := range allPreds {
		y := allLabels[i]
		brier += (p - y) * (p - y)
		brierBase += (base - y) * (base - y)
	}
	n := float64(len(allPreds))
	brier /= n
	brierBase /= n

	// Five-bin reliability table: predicted vs what actually happened.
	type bin struct{ sum, hit, count float64 }
	bins := make([]bin, 5)
	for i, p := range allPreds {
		b := int(p * 5)
		if b > 4 {
			b = 4
		}
		bins[b].sum += p
		bins[b].hit += allLabels[i]
		bins[b].count++
	}
	calib := make([]map[string]any, 0, 5)
	for _, b := range bins {
		if b.count == 0 {
			continue
		}
		calib = append(calib, map[string]any{
			"predicted": b.sum / b.count,
			"actual":    b.hit / b.count,
			"count":     int(b.count),
		})
	}

	// Full-data model for scoring the alerts that are live right now.
	var allX [][]float64
	var allY []float64
	for _, a := range samples {
		allX = append(allX, toRow(a))
		allY = append(allY, a.Label)
	}
	full := fitLogistic(allX, allY, 600, 0.3, 0.002)

	recent := append([]alertSample(nil), samples...)
	sort.Slice(recent, func(i, j int) bool { return recent[i].TS > recent[j].TS })
	if len(recent) > 15 {
		recent = recent[:15]
	}
	scored := make([]map[string]any, 0, len(recent))
	for _, a := range recent {
		p := 0.0
		if full != nil {
			p = full.predict(toRow(a))
		}
		scored = append(scored, map[string]any{
			"symbol": a.Symbol, "kind": a.Kind, "ts_ms": a.TS,
			"score": a.Score * 100, "probability": p, "continued": a.Label == 1,
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "alerts": len(samples), "symbols": len(syms), "folds": folds,
		"horizon": horizon, "threshold_bps": int(threshold * 10000),
		"validation": map[string]any{
			"method": "leave-one-symbol-out", "n": len(allPreds),
			"auc": aucOf(allPreds, allLabels), "base_rate": base,
			"brier": brier, "brier_base": brierBase,
			"skill": 1 - brier/brierBase,
		},
		"calibration": calib,
		"live":        scored,
		"weights":     weightMap(full),
		"feature_names": []string{
			"direction", "score", "6-bar move", "30-bar move", "volatility", "bars since last alert",
		},
	})
}

// weightMap names the fitted coefficients so the model is inspectable.
func weightMap(m *logisticFit) map[string]float64 {
	names := []string{"direction", "score", "r6", "r30", "vol", "since_prev_alert"}
	out := map[string]float64{}
	if m == nil {
		return out
	}
	for i, w := range m.W {
		if i < len(names) {
			out[names[i]] = w
		}
	}
	out["bias"] = m.B
	return out
}
