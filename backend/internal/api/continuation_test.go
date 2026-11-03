package api

import (
	"math"
	"math/rand"
	"testing"
)

// The labeller must produce both classes and must not look ahead: a bar's label
// depends only on prices at or after it, and the last `horizon` bars cannot be
// labelled at all.
func TestContinuationLabelling(t *testing.T) {
	rng := rand.New(rand.NewSource(11))
	n := 400
	closes := make([]float64, n)
	closes[0] = 100
	for i := 1; i < n; i++ {
		closes[i] = closes[i-1] * (1 + rng.NormFloat64()*0.004)
	}
	times := make([]int64, n)
	for i := range times {
		times[i] = int64(i) * 3_600_000
	}

	rows, labels := buildContinuationRows(closes, times, nil, 12, 0.001)
	if len(rows) == 0 || len(rows) != len(labels) {
		t.Fatalf("rows %d, labels %d", len(rows), len(labels))
	}
	// Every bar before n-horizon is eligible (from index 30); none after.
	if want := n - 12 - 30; len(rows) != want {
		t.Fatalf("expected %d labelled bars, got %d", want, len(rows))
	}
	ones := 0
	for _, y := range labels {
		if y != 0 && y != 1 {
			t.Fatalf("label must be 0 or 1, got %v", y)
		}
		if y == 1 {
			ones++
		}
	}
	share := float64(ones) / float64(len(labels))
	if share == 0 || share == 1 {
		t.Fatalf("a real series must yield both classes, got share %.3f", share)
	}
	t.Logf("labelled %d bars, %.0f%% continued", len(rows), share*100)
}

// Training must reduce the loss it is optimising. That is the property the rest
// of the pipeline depends on; whether the market has signal is measured on real
// data by the endpoint, not asserted here.
func TestLogisticTrainingReducesLoss(t *testing.T) {
	rng := rand.New(rand.NewSource(3))
	rows := make([][]float64, 400)
	labels := make([]float64, 400)
	for i := range rows {
		x := rng.NormFloat64()
		rows[i] = []float64{x, rng.NormFloat64() * 0.5}
		// A real but noisy relationship, so the fit has something to find.
		if x+rng.NormFloat64()*0.8 > 0 {
			labels[i] = 1
		}
	}
	before := []float64{0, 0}
	untrained := &logisticFit{
		W: make([]float64, 2), B: 0,
		Mean:   []float64{0, 0},
		StdDev: []float64{1, 1},
	}
	_ = before

	loss := func(m *logisticFit) float64 {
		var s float64
		for i, r := range rows {
			p := m.predict(r)
			if p < 1e-9 {
				p = 1e-9
			}
			if p > 1-1e-9 {
				p = 1 - 1e-9
			}
			s += -(labels[i]*math.Log(p) + (1-labels[i])*math.Log(1-p))
		}
		return s / float64(len(rows))
	}

	l0 := loss(untrained)
	m := fitLogistic(rows, labels, 800, 0.3, 0.001)
	if m == nil {
		t.Fatal("nil model")
	}
	l1 := loss(m)
	if !(l1 < l0) {
		t.Fatalf("training must reduce log loss: before %.4f after %.4f", l0, l1)
	}
	t.Logf("log loss %.4f -> %.4f", l0, l1)

	// And it must rank: a strongly positive feature scores above a strongly
	// negative one.
	if m.predict([]float64{2.5, 0}) <= m.predict([]float64{-2.5, 0}) {
		t.Fatal("model must rank a positive feature above a negative one")
	}
}
