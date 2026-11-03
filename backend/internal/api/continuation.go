package api

import (
	"context"
	"math"
	"net/http"
	"strings"
	"time"
)

// P(continuation): the probability that the move already in progress keeps going.
//
// This replaces the price-level forecast as the primary target. A level forecast
// is nearly a random walk at this horizon and a flat line already wins it; the
// question a rally detector actually acts on is different — "is this move going
// to continue?" — and that is a calibrated probability, which can be measured
// and scored.
//
// Everything here is walk-forward: the classifier is fitted on the earlier bars
// and graded on the later ones it has never seen.

// continuationFeatures is one row of the design matrix.
type continuationFeatures struct {
	R10  float64 // 10-bar return: the move in progress
	R30  float64 // 30-bar return: the larger swing
	Vol  float64 // realised volatility over 20 bars
	Flow float64 // taker buy/sell imbalance
	Fund float64 // funding rate
	OI   float64 // open-interest change
}

// buildContinuationRows aligns price bars with the futures series and labels each
// bar: did the prevailing move continue by at least the threshold?
func buildContinuationRows(
	closes []float64,
	times []int64,
	feats []FeaturePoint,
	horizon int,
	threshold float64,
) (rows []continuationFeatures, labels []float64) {
	n := len(closes)
	if n < 60+horizon {
		return nil, nil
	}
	funding := featureSignal("funding", feats, times)
	flow := featureSignal("order_flow", feats, times)
	oi := featureSignal("open_interest", feats, times)

	for i := 30; i+horizon < n; i++ {
		if closes[i] == 0 || closes[i-10] == 0 || closes[i-30] == 0 {
			continue
		}
		r10 := closes[i]/closes[i-10] - 1
		r30 := closes[i]/closes[i-30] - 1
		if r10 == 0 {
			continue
		}
		// Prevailing direction is the 10-bar move; a flat 10-bar window has no
		// continuation to speak of.
		dir := 1.0
		if r10 < 0 {
			dir = -1
		}
		vol := 0.0
		for j := i - 20; j < i; j++ {
			if j > 0 && closes[j-1] != 0 {
				d := closes[j]/closes[j-1] - 1
				vol += d * d
			}
		}
		vol = math.Sqrt(vol / 20)

		forward := closes[i+horizon]/closes[i] - 1
		continued := dir*forward >= threshold
		label := 0.0
		if continued {
			label = 1
		}
		rows = append(rows, continuationFeatures{
			R10: r10, R30: r30, Vol: vol,
			Flow: flow[i], Fund: funding[i], OI: oi[i],
		})
		labels = append(labels, label)
	}
	return rows, labels
}

// logisticFit is L2-regularised logistic regression by gradient descent. Small,
// deterministic, and testable — no external service in the decision path.
type logisticFit struct {
	W      []float64
	B      float64
	Mean   []float64
	StdDev []float64
}

func (m *logisticFit) predict(x []float64) float64 {
	z := m.B
	for i := range x {
		sd := m.StdDev[i]
		if sd < 1e-12 {
			sd = 1
		}
		z += m.W[i] * (x[i] - m.Mean[i]) / sd
	}
	return 1 / (1 + math.Exp(-z))
}

// fitLogistic trains on rows/labels and returns the fitted model.
func fitLogistic(rows [][]float64, labels []float64, iters int, lr, l2 float64) *logisticFit {
	if len(rows) == 0 {
		return nil
	}
	d := len(rows[0])
	m := &logisticFit{W: make([]float64, d), Mean: make([]float64, d), StdDev: make([]float64, d)}
	for j := 0; j < d; j++ {
		s := 0.0
		for _, r := range rows {
			s += r[j]
		}
		mean := s / float64(len(rows))
		var v float64
		for _, r := range rows {
			v += (r[j] - mean) * (r[j] - mean)
		}
		m.Mean[j] = mean
		m.StdDev[j] = math.Sqrt(v / float64(len(rows)))
	}
	if iters < 1 {
		iters = 200
	}
	for it := 0; it < iters; it++ {
		gw := make([]float64, d)
		gb := 0.0
		for i, r := range rows {
			p := m.predict(r)
			err := p - labels[i]
			gb += err
			for j := 0; j < d; j++ {
				sd := m.StdDev[j]
				if sd < 1e-12 {
					sd = 1
				}
				gw[j] += err * (r[j] - m.Mean[j]) / sd
			}
		}
		fn := float64(len(rows))
		for j := range m.W {
			m.W[j] -= lr * (gw[j]/fn + l2*m.W[j])
		}
		m.B -= lr * gb / fn
	}
	return m
}

// handleContinuation exposes the probability, its calibration, and the honest
// out-of-sample score next to the base rate it has to beat.
func (s *Server) handleContinuation(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := r.URL.Query().Get("interval")
	if interval == "" {
		interval = "1h"
	}
	horizon := queryInt(r, "horizon", 24)
	if horizon < 1 || horizon > 720 {
		horizon = 24
	}
	thresholdBps := queryInt(r, "threshold_bps", 50) // 0.50% by default
	threshold := float64(thresholdBps) / 10000

	ctx, cancel := context.WithTimeout(r.Context(), 40*time.Second)
	defer cancel()

	// 400 is the depth the series source will actually serve; asking for more
	// made every fetch fail and read as "not enough history".
	// labSeries' second value reports whether the series was freshly fetched,
	// not whether it is usable — a cache hit returns 400 good bars with false.
	// Judging usability by length alone is what makes this work at all.
	bars, _ := s.labSeries(ctx, symbol, interval, 400)
	if len(bars) < 120+horizon {
		// Say what actually happened: a failed fetch and a short series are
		// different problems and were indistinguishable before.
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "error": "not enough price history",
			"bars": len(bars), "need": 120 + horizon,
		})
		return
	}
	closes := make([]float64, len(bars))
	times := make([]int64, len(bars))
	for i, b := range bars {
		closes[i] = b.V
		times[i] = b.T
	}
	feats, ferr := s.fetchFeatures(ctx, symbol, interval)
	if ferr != nil {
		feats = nil // price-only features still work
	}

	rows, labels := buildContinuationRows(closes, times, feats, horizon, threshold)
	if len(rows) < 100 {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "not enough labelled bars to fit"})
		return
	}

	// Walk-forward: fit on the first 70%, grade on the last 30%.
	split := int(float64(len(rows)) * 0.7)
	x := make([][]float64, len(rows))
	for i, r := range rows {
		x[i] = []float64{r.R10, r.R30, r.Vol, r.Flow, r.Fund, r.OI}
	}
	model := fitLogistic(x[:split], labels[:split], 600, 0.35, 0.001)
	if model == nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "fit failed"})
		return
	}

	testX := x[split:]
	testY := labels[split:]
	var brier, correct, baseHits float64
	bins := make([][2]float64, 5) // predicted sum, count
	for i, xi := range testX {
		p := model.predict(xi)
		y := testY[i]
		brier += (p - y) * (p - y)
		if (p >= 0.5) == (y == 1) {
			correct++
		}
		if y == 1 {
			baseHits++
		}
		b := int(p * 5)
		if b > 4 {
			b = 4
		}
		bins[b][0] += p
		bins[b][1]++
	}
	testN := float64(len(testX))
	base := baseHits / testN
	// Accuracy is not a fair score here: 80% of the bars do not continue, so
	// always answering "no" scores 80%. Brier skill against that constant
	// forecast is the honest measure.
	var brierBase float64
	for _, y := range testY {
		brierBase += (base - y) * (base - y)
	}
	brierBase /= testN
	skill := 1 - (brier/testN)/brierBase

	type calibBin struct {
		Predicted float64 `json:"predicted"`
		Actual    float64 `json:"actual"`
		Count     int     `json:"count"`
	}
	calib := make([]calibBin, 0, 5)
	for _, b := range bins {
		if b[1] == 0 {
			continue
		}
		pred := b[0] / b[1]
		act := 0.0
		count := 0
		for i, xi := range testX {
			p := model.predict(xi)
			bb := int(p * 5)
			if bb > 4 {
				bb = 4
			}
			if bb == int(pred*5) || math.Abs(p-b[0]/b[1]) < 0.1 {
				act += testY[i]
				count++
			}
		}
		if count > 0 {
			calib = append(calib, calibBin{Predicted: pred, Actual: act / float64(count), Count: count})
		}
	}

	last := x[len(x)-1]
	now := model.predict(last)
	dir := "up"
	if closes[len(closes)-1] < closes[len(closes)-11] {
		dir = "down"
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "symbol": symbol, "interval": interval, "horizon": horizon,
		"threshold_bps": thresholdBps,
		"probability":   now,
		"direction":     dir,
		"question":      "probability the move keeps going the same way",
		"test": map[string]any{
			"bars_labelled": len(rows), "train": split, "test": len(testX),
			"brier": brier / testN, "brier_base": brierBase,
			"skill": skill, "beats_base": skill > 0,
			"accuracy": correct / testN, "base_rate": base,
		},
		"calibration": calib,
		"features":    []string{"10-bar return", "30-bar return", "volatility", "order flow", "funding", "open interest"},
	})
}
