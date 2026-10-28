package main

import (
	"encoding/json"
	"math"
	"testing"
)

func TestExtractCloses(t *testing.T) {
	series := []any{
		map[string]any{"v": 1.0},
		map[string]any{"c": 2.0},
		map[string]any{"v": 3.0},
	}
	got := extractCloses(series)
	want := []float64{1, 2, 3}
	if len(got) != len(want) {
		t.Fatalf("len = %d, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got[%d] = %v, want %v", i, got[i], want[i])
		}
	}
}

func TestSharpe(t *testing.T) {
	// Constant positive returns -> mean>0, sd=0 -> sharpe 0 (guarded).
	if s := sharpe([]float64{0.01, 0.01, 0.01}); s != 0 {
		t.Fatalf("constant-return sharpe = %v, want 0", s)
	}
	// Alternating returns -> mean 0 -> sharpe 0.
	if s := sharpe([]float64{0.01, -0.01, 0.01, -0.01}); math.Abs(s) > 1e-9 {
		t.Fatalf("alternating sharpe = %v, want 0", s)
	}
}

func TestToFloatAndRound(t *testing.T) {
	if toFloat(json.Number("3.5")) != 3.5 {
		t.Fatalf("toFloat(3.5) != 3.5")
	}
	if toFloat(2) != 2 {
		t.Fatalf("toFloat(2) != 2")
	}
	if round(1.23456, 4) != 1.2346 {
		t.Fatalf("round(1.23456,4) != 1.2346")
	}
}
