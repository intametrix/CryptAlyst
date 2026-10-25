package microburst

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"

	"pumpdump/backend/internal/ledger"
)

// oneFeatureModel builds a deterministic logistic model over a single feature
// with weight 1, so P = sigmoid(intercept + 1*value) on standardised input.
func oneFeatureModel(intercept float64) ledger.Model {
	return ledger.Model{
		Version:   ledger.CalibrationVersion,
		Features:  []string{"burst_multiple"},
		Mean:      []float64{0},
		Std:       []float64{1},
		Intercept: intercept,
		Weights:   []float64{1},
	}
}

func writeModelFile(t *testing.T, dir string, horizonS int, interceptUp, interceptRev float64) {
	t.Helper()
	mf := modelFile{
		HorizonS: horizonS,
		Up:       oneFeatureModel(interceptUp),
		Reversal: oneFeatureModel(interceptRev),
	}
	data, err := json.Marshal(mf)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "microburst_"+itoa(horizonS)+"s.json")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestServiceProbability(t *testing.T) {
	dir := t.TempDir()
	writeModelFile(t, dir, 10, 0.0, -0.5)
	writeModelFile(t, dir, 30, 0.5, -1.0)

	svc := New(dir)
	got := svc.Probability(map[string]float64{"burst_multiple": 1.0})
	if len(got) != 2 {
		t.Fatalf("want 2 horizons, got %d", len(got))
	}

	h10 := got[10]
	pUp10 := sigmoid(0.0 + 1.0) // sigmoid(1) = 0.731...
	if math.Abs(h10.PUp-pUp10) > 1e-6 {
		t.Fatalf("PUp(10s)=%v want %v", h10.PUp, pUp10)
	}
	// P(neither) = 1 - P(up) - P(reversal), clamped to [0,1].
	resid := 1 - h10.PUp - h10.PReversal
	wantNeither := 0.0
	if resid > 0 {
		wantNeither = resid
	}
	if math.Abs(h10.PNeither-wantNeither) > 1e-9 {
		t.Fatalf("PNeither(10s)=%v want %v (resid %v)", h10.PNeither, wantNeither, resid)
	}
	if _, ok := got[30]; !ok {
		t.Fatalf("horizon 30s missing")
	}
}

func TestServiceAbsentDirIsEmpty(t *testing.T) {
	svc := New(filepath.Join(t.TempDir(), "nope"))
	if got := svc.Probability(map[string]float64{"burst_multiple": 1.0}); len(got) != 0 {
		t.Fatalf("absent dir must yield no probabilities, got %d", len(got))
	}
}

func sigmoid(z float64) float64 { return 1 / (1 + math.Exp(-z)) }

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
