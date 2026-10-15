// Calibration: offline logistic regression over the per-prediction feature
// rows captured at alert time, fitted against ledger resolution labels.
//
// Label mapping (deliberate, and NOT the same as expectancy): CORRECT=1,
// WRONG=0, EXPIRED=0. Expectancy treats EXPIRED as "MFE capped at target" —
// a soft partial win — but a probabilistic classifier needs a binary event,
// and "did it hit target before stop" is the event the live gate cares
// about. EXPIRED means the target was NOT hit, so it is a 0. Consequence:
// the model predicts P(hit target), not expected R; the base rate is the
// hit rate.
//
// All quality numbers from FitLogistic are IN-SAMPLE (computed on the fitted
// rows), not holdout. With 100-300 rows a train/holdout split would leave
// the holdout too thin to judge anything; honest provenance beats a
// pretend-out-of-sample number.

package ledger

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sort"
	"time"
)

// MinCalibrationN is the minimum number of resolved rows required before a
// calibration file is written. Fewer than this and the reliability table is
// noise shaped like a table.
const MinCalibrationN = 100

// CalibrationVersion is the current calibration.json schema version.
const CalibrationVersion = 1

const (
	calibrateIters   = 2000
	calibrateLR      = 0.1
	calibrateL2      = 1e-3
	reliabilityBins  = 10
	labelRuleExplain = "CORRECT=1, WRONG=0, EXPIRED=0 (in-sample fit)"
)

// FeatureRow is one training example: the flat feature map captured at
// alert time.
type FeatureRow struct {
	ID       string
	Features map[string]float64
}

// ReliabilityBin is one bucket of the 10-bin reliability table.
type ReliabilityBin struct {
	Lo        float64 `json:"lo"`
	Hi        float64 `json:"hi"`
	Predicted float64 `json:"predicted"`
	Observed  float64 `json:"observed"`
	Count     int     `json:"count"`
}

// Model is the calibration.json document and the fitted logistic model.
type Model struct {
	Version     int              `json:"version"`
	FittedMS    int64            `json:"fitted_ms"`
	N           int              `json:"n"`
	BaseRate    float64          `json:"base_rate"`
	LabelRule   string           `json:"label_rule"`
	Features    []string         `json:"features"`
	Mean        []float64        `json:"mean"`
	Std         []float64        `json:"std"`
	Intercept   float64          `json:"intercept"`
	Weights     []float64        `json:"weights"`
	Brier       float64          `json:"brier"`
	LogLoss     float64          `json:"log_loss"`
	Reliability []ReliabilityBin `json:"reliability"`
}

// Predict returns the model's probability for a feature map. Features
// missing from the map are imputed at their train-set mean (standardized
// value 0), exactly as at fit time.
func (m *Model) Predict(features map[string]float64) (float64, bool) {
	if len(m.Features) == 0 || len(m.Weights) != len(m.Features) {
		return 0, false
	}
	z := m.Intercept
	any := false
	for i, name := range m.Features {
		v, ok := features[name]
		if !ok {
			continue // impute mean -> standardized 0 -> no contribution
		}
		any = true
		std := 1.0
		if i < len(m.Std) && m.Std[i] > 0 {
			std = m.Std[i]
		}
		z += m.Weights[i] * (v - m.Mean[i]) / std
	}
	if !any {
		return 0, false
	}
	return sigmoid(z), true
}

// CalibratedProbability is the live-scoring entry point: pass the raw
// calibration.json bytes plus a feature map. ok is false when the model is
// absent/unparseable or NONE of the model's features are present in the
// map; in that case the caller should fall back to the uncalibrated prior.
func CalibratedProbability(modelJSON []byte, features map[string]float64) (float64, bool) {
	var m Model
	if err := json.Unmarshal(modelJSON, &m); err != nil || m.Version != CalibrationVersion {
		return 0, false
	}
	return m.Predict(features)
}

// sigmoid clamps z to avoid overflow.
func sigmoid(z float64) float64 {
	if z > 30 {
		return 1
	}
	if z < -30 {
		return 0
	}
	return 1 / (1 + math.Exp(-z))
}

// FitLogistic fits logistic regression by gradient descent with L2
// (lambda=1e-3, not applied to the intercept), 2000 iterations, lr 0.1, on
// standardized features. Returns the model plus in-sample quality metrics.
// rows and labels must have equal length; rows with no known feature are
// still usable (all-zero vector) but rows with no features at all anywhere
// leave the model degenerate.
func FitLogistic(rows []FeatureRow, labels []int) (Model, error) {
	var m Model
	if len(rows) != len(labels) {
		return m, fmt.Errorf("rows (%d) and labels (%d) length mismatch", len(rows), len(labels))
	}
	if len(rows) == 0 {
		return m, fmt.Errorf("no rows")
	}

	// Feature set: union of keys, sorted for determinism.
	seen := map[string]bool{}
	for _, r := range rows {
		for k := range r.Features {
			seen[k] = true
		}
	}
	features := make([]string, 0, len(seen))
	for k := range seen {
		features = append(features, k)
	}
	sort.Strings(features)
	idx := make(map[string]int, len(features))
	for i, f := range features {
		idx[f] = i
	}

	n := len(rows)
	d := len(features)
	X := make([][]float64, n)
	hits := 0
	for i, r := range rows {
		X[i] = make([]float64, d)
		for k, v := range r.Features {
			if j, ok := idx[k]; ok {
				X[i][j] = v
			}
		}
		if labels[i] == 1 {
			hits++
		}
	}

	// Standardization stats over present values; constant features get std 1.
	mean := make([]float64, d)
	std := make([]float64, d)
	for j := 0; j < d; j++ {
		cnt := 0
		sum := 0.0
		for i := 0; i < n; i++ {
			if v, ok := rows[i].Features[features[j]]; ok {
				sum += v
				cnt++
			}
		}
		if cnt == 0 {
			mean[j] = 0
			std[j] = 1
			continue
		}
		mean[j] = sum / float64(cnt)
		sq := 0.0
		for i := 0; i < n; i++ {
			if v, ok := rows[i].Features[features[j]]; ok {
				sq += (v - mean[j]) * (v - mean[j])
			}
		}
		s := math.Sqrt(sq / float64(cnt))
		if s < 1e-9 {
			s = 1
		}
		std[j] = s
	}
	// Standardize: missing -> 0 (the mean).
	Z := make([][]float64, n)
	for i := 0; i < n; i++ {
		Z[i] = make([]float64, d)
		for j := 0; j < d; j++ {
			if _, ok := rows[i].Features[features[j]]; ok {
				Z[i][j] = (X[i][j] - mean[j]) / std[j]
			}
		}
	}

	w := make([]float64, d)
	b := 0.0
	y := make([]float64, n)
	for i, l := range labels {
		y[i] = float64(l)
	}
	for iter := 0; iter < calibrateIters; iter++ {
		gb := 0.0
		g := make([]float64, d)
		for i := 0; i < n; i++ {
			p := sigmoid(b + dot(w, Z[i]))
			err := p - y[i]
			gb += err
			for j := 0; j < d; j++ {
				g[j] += err * Z[i][j]
			}
		}
		b -= calibrateLR * gb / float64(n)
		for j := 0; j < d; j++ {
			g[j] = g[j]/float64(n) + calibrateL2*w[j]
			w[j] -= calibrateLR * g[j]
		}
	}

	// In-sample metrics.
	var brier, logLoss float64
	counts := make([]int, reliabilityBins)
	predSum := make([]float64, reliabilityBins)
	obsSum := make([]float64, reliabilityBins)
	for i := 0; i < n; i++ {
		p := sigmoid(b + dot(w, Z[i]))
		brier += (p - y[i]) * (p - y[i])
		lp := math.Max(p, 1e-15)
		lq := math.Max(1-p, 1e-15)
		logLoss += -(y[i]*math.Log(lp) + (1-y[i])*math.Log(lq))
		bin := int(p * reliabilityBins)
		if bin >= reliabilityBins {
			bin = reliabilityBins - 1
		}
		counts[bin]++
		predSum[bin] += p
		obsSum[bin] += y[i]
	}

	rel := make([]ReliabilityBin, reliabilityBins)
	for k := 0; k < reliabilityBins; k++ {
		rel[k] = ReliabilityBin{
			Lo: float64(k) / reliabilityBins,
			Hi: float64(k+1) / reliabilityBins,
		}
		if counts[k] > 0 {
			rel[k].Predicted = predSum[k] / float64(counts[k])
			rel[k].Observed = obsSum[k] / float64(counts[k])
			rel[k].Count = counts[k]
		}
	}

	m = Model{
		Version:     CalibrationVersion,
		FittedMS:    nowMS(),
		N:           n,
		BaseRate:    float64(hits) / float64(n),
		LabelRule:   labelRuleExplain,
		Features:    features,
		Mean:        mean,
		Std:         std,
		Intercept:   b,
		Weights:     w,
		Brier:       brier / float64(n),
		LogLoss:     logLoss / float64(n),
		Reliability: rel,
	}
	return m, nil
}

func dot(w, x []float64) float64 {
	s := 0.0
	for j := range w {
		s += w[j] * x[j]
	}
	return s
}

// WriteCalibration writes the calibration file atomically (temp file in the
// same directory + rename) so a live reader never sees a torn document.
// An existing file at path is replaced wholesale — the new fit supersedes
// the old one; there is no merging of coefficients.
func WriteCalibration(path string, m Model) error {
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	// Best effort: keep the temp cleanup honest if rename failed.
	if _, err := os.Stat(tmp); err == nil {
		_ = os.Remove(tmp)
	}
	return nil
}

// nowMS is a var for testability.
var nowMS = func() int64 {
	return time.Now().UnixMilli()
}
