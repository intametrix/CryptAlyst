package gpuagent

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"pumpdump/backend/internal/envelope"
	"pumpdump/backend/internal/graph"
)

func TestClientServerRoundTrip(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(HandleSweep))
	defer srv.Close()

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
		Seed:    7,
	}

	n := 10
	contexts := make([]graph.Context, n)
	closes := make([]float64, n)
	for i := 0; i < n; i++ {
		contexts[i] = graph.Context{TSMS: int64(i), Symbol: "BTCUSDT"}
		closes[i] = 100 + float64(i)
	}

	client := NewClient(srv.URL)
	res, err := client.Sweep(job, contexts, closes)
	if err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	if len(res.Combos) != 3 {
		t.Fatalf("combos = %d, want 3", len(res.Combos))
	}
	if res.Device != "cpu" {
		t.Errorf("device = %q", res.Device)
	}
}
