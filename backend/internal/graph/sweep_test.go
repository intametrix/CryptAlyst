package graph

import (
	"testing"
)

func TestCombos(t *testing.T) {
	grid := Grid{"a": {1, 2}, "b": {10, 20}}
	cs := combos(grid)
	if len(cs) != 4 {
		t.Fatalf("combos = %d, want 4", len(cs))
	}
}

func TestSweepThreshold(t *testing.T) {
	// A score of 80 -> threshold -> direction -> verdict. Thresholds at or below
	// 80 fire bull; above 80 stay neutral.
	build := func(p map[string]float64) (*Graph, *VerdictNode) {
		g := NewGraph()
		g.Add(ConstantNode{Value: 80, Type: TypeScore})
		g.Add(ThresholdNode{Threshold: p["threshold"]})
		v := &VerdictNode{}
		g.Add(v)
		g.Link(Link{From: "source.constant", FromPort: "out", To: "calc.threshold", ToPort: "score"})
		g.Link(Link{From: "calc.threshold", FromPort: "direction", To: "sink.verdict", ToPort: "in"})
		return g, v
	}

	n := 10
	contexts := make([]Context, n)
	closes := make([]float64, n)
	for i := 0; i < n; i++ {
		contexts[i] = Context{TSMS: int64(i), Symbol: "BTCUSDT"}
		closes[i] = 100 + float64(i) // rising
	}
	ctx := &Context{}

	res, err := Sweep(build, Grid{"threshold": {40, 60, 80, 90}}, contexts, closes, ctx, 1)
	if err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	if len(res) != 4 {
		t.Fatalf("Sweep results = %d, want 4", len(res))
	}
	// All thresholds <= 80 fire bull into a rising price -> 100% hit.
	for _, r := range res {
		if r.Params["threshold"] <= 80 {
			if r.HitRate != 1.0 {
				t.Errorf("threshold %.0f: hit rate = %v, want 1.0", r.Params["threshold"], r.HitRate)
			}
		} else {
			if r.Signals != 0 {
				t.Errorf("threshold %.0f: signals = %d, want 0", r.Params["threshold"], r.Signals)
			}
		}
	}
	// Results are sorted by hit rate descending.
	if res[0].HitRate < res[len(res)-1].HitRate {
		t.Errorf("results not sorted: first %.2f < last %.2f", res[0].HitRate, res[len(res)-1].HitRate)
	}
}
