package gpuagent

import (
	"testing"

	"pumpdump/backend/internal/envelope"
	"pumpdump/backend/internal/graph"
)

func TestRunSweepJob(t *testing.T) {
	job := envelope.SweepJob{
		Graph: envelope.GraphSpec{
			Nodes: []envelope.NodeSpec{
				{ID: "source.constant", Params: map[string]any{"value": float64(80)}},
				{ID: "calc.threshold", Params: map[string]any{"threshold": float64(50)}},
				{ID: "sink.verdict"},
			},
			Links: []envelope.LinkSpec{
				{From: "source.constant", FromPort: "out", To: "calc.threshold", ToPort: "score"},
				{From: "calc.threshold", FromPort: "direction", To: "sink.verdict", ToPort: "in"},
			},
		},
		Grid:    map[string][]float64{"threshold": {40, 60, 80}},
		Forward: 1,
		Seed:    42,
	}

	n := 10
	contexts := make([]graph.Context, n)
	closes := make([]float64, n)
	for i := 0; i < n; i++ {
		contexts[i] = graph.Context{TSMS: int64(i), Symbol: "BTCUSDT"}
		closes[i] = 100 + float64(i) // rising
	}

	res, err := Run(job, contexts, closes)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Device != "cpu" {
		t.Errorf("device = %q", res.Device)
	}
	if len(res.Combos) != 3 {
		t.Fatalf("combos = %d, want 3", len(res.Combos))
	}
	// all thresholds <= 80 fire bull into a rising price -> hit rate 1.0
	for _, c := range res.Combos {
		if c.Params["threshold"] <= 80 && c.HitRate != 1.0 {
			t.Errorf("threshold %.0f: hit rate = %v, want 1.0", c.Params["threshold"], c.HitRate)
		}
	}
}

func TestRunUnknownNode(t *testing.T) {
	job := envelope.SweepJob{
		Graph: envelope.GraphSpec{
			Nodes: []envelope.NodeSpec{{ID: "ghost.node"}},
		},
	}
	if _, err := Run(job, nil, nil); err == nil {
		t.Error("Run should error on an unknown node")
	}
}
