package envelope

import "testing"

func TestEnvelopeRoundTrip(t *testing.T) {
	job := SweepJob{
		Graph: GraphSpec{
			Nodes: []NodeSpec{
				{ID: "source.flow", Params: map[string]any{"symbol": "BTCUSDT"}},
				{ID: "calc.threshold", Params: map[string]any{"threshold": 50}},
				{ID: "sink.verdict"},
			},
			Links: []LinkSpec{
				{From: "source.flow", FromPort: "flow", To: "calc.threshold", ToPort: "score"},
				{From: "calc.threshold", FromPort: "direction", To: "sink.verdict", ToPort: "in"},
			},
		},
		Grid:    map[string][]float64{"threshold": {40, 50, 60}},
		Symbols: []string{"BTCUSDT", "ETHUSDT"},
		History: HistoryRef{Source: "klines", Symbol: "BTCUSDT", Interval: "1s"},
		Forward: 5,
		Seed:    42,
	}
	env, err := New("job_1", KindJob, job)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	b, err := env.Marshal()
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}

	got, err := Unmarshal(b)
	if err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if got.ID != "job_1" || got.Kind != KindJob || got.Version != Version {
		t.Errorf("header = %+v", got)
	}
	var decoded SweepJob
	if err := got.Decode(&decoded); err != nil {
		t.Fatalf("Decode: %v", err)
	}
	if len(decoded.Graph.Nodes) != 3 || decoded.Seed != 42 {
		t.Errorf("decoded = %+v", decoded)
	}
	if len(decoded.Grid["threshold"]) != 3 {
		t.Errorf("grid = %v", decoded.Grid)
	}
}

func TestEnvelopeVersionRejected(t *testing.T) {
	env := Envelope{Version: 99, Kind: KindJob, Payload: []byte(`{"a":1}`)}
	if err := env.Decode(&struct{}{}); err == nil {
		t.Error("Decode should reject an unknown version")
	}
}
