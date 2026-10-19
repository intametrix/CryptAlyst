package ledger

import (
	"encoding/json"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"testing"
)

// TestFitLogisticRecoversPlantedSignal generates 500 rows where the label is
// driven by a known logistic function of burst_multiple (standardized weight
// +2.0, intercept -0.5) plus five pure-noise features, then asserts the fit
// recovers the signal: in-sample Brier < 0.15 and burst_multiple carries the
// largest absolute standardized weight.
func TestFitLogisticRecoversPlantedSignal(t *testing.T) {
	rng := rand.New(rand.NewSource(42))
	const n = 500
	noise := []string{"noise_a", "noise_b", "noise_c", "noise_d", "noise_e"}
	rows := make([]FeatureRow, 0, n)
	labels := make([]int, 0, n)
	bmMean, bmStd := 8.5, 4.33
	for i := 0; i < n; i++ {
		bm := 1 + 15*rng.Float64()
		feat := map[string]float64{"burst_multiple": bm}
		for _, k := range noise {
			feat[k] = rng.NormFloat64()
		}
		z := (bm - bmMean) / bmStd
		logit := -0.5 + 2.0*z
		p := 1 / (1 + math.Exp(-logit))
		label := 0
		if rng.Float64() < p {
			label = 1
		}
		rows = append(rows, FeatureRow{ID: itoa(i), Features: feat})
		labels = append(labels, label)
	}

	m, err := FitLogistic(rows, labels)
	if err != nil {
		t.Fatalf("FitLogistic: %v", err)
	}
	if m.Brier >= 0.15 {
		t.Fatalf("brier = %.4f, want < 0.15 (base_rate=%.3f)", m.Brier, m.BaseRate)
	}
	idx := -1
	for i, f := range m.Features {
		if f == "burst_multiple" {
			idx = i
		}
	}
	if idx < 0 {
		t.Fatal("burst_multiple not in model features")
	}
	for i, f := range m.Features {
		if i == idx {
			continue
		}
		if math.Abs(m.Weights[i]) > math.Abs(m.Weights[idx]) {
			t.Fatalf("noise feature %s has larger |weight| (%.3f) than burst_multiple (%.3f)",
				f, m.Weights[i], m.Weights[idx])
		}
	}
	// Planted standardized weight is +2.0; recovery should be in that region.
	if m.Weights[idx] < 1.0 || m.Weights[idx] > 3.0 {
		t.Fatalf("burst_multiple weight = %.3f, want ~2.0", m.Weights[idx])
	}
	// Reliability table: buckets must be in ascending order and counts sum to n.
	total := 0
	prevLo := -1.0
	for _, b := range m.Reliability {
		if b.Lo <= prevLo {
			t.Fatalf("reliability bins not ascending: %+v", m.Reliability)
		}
		prevLo = b.Lo
		total += b.Count
	}
	if total != n {
		t.Fatalf("reliability counts sum %d, want %d", total, n)
	}

	// Roundtrip: WriteCalibration (atomic) -> file exists, no .tmp left ->
	// CalibratedProbability scores a high burst_multiple above a low one.
	dir := t.TempDir()
	path := filepath.Join(dir, "calibration.json")
	if err := WriteCalibration(path, m); err != nil {
		t.Fatalf("WriteCalibration: %v", err)
	}
	if _, err := os.Stat(path + ".tmp"); err == nil {
		t.Fatal("leftover temp file after atomic write")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read calibration: %v", err)
	}
	var doc Model
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("calibration.json not valid JSON: %v", err)
	}
	low, okLow := CalibratedProbability(raw, map[string]float64{"burst_multiple": 1})
	high, okHigh := CalibratedProbability(raw, map[string]float64{"burst_multiple": 16})
	if !okLow || !okHigh {
		t.Fatalf("CalibratedProbability ok=false (low=%v high=%v)", okLow, okHigh)
	}
	if high <= low {
		t.Fatalf("monotonicity violated: low=%.3f high=%.3f", low, high)
	}
	if _, ok := CalibratedProbability(raw, map[string]float64{"unrelated": 1}); ok {
		t.Fatal("ok=true when none of the model's features are present")
	}
	if _, ok := CalibratedProbability([]byte("not json"), map[string]float64{"burst_multiple": 1}); ok {
		t.Fatal("ok=true on unparseable model")
	}
}

func itoa(i int) string {
	return string(rune('0'+i%10)) + "_" + string(rune('A'+i%26))
}
