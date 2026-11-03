package api

import (
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"

	"pumpdump/backend/internal/envelope"
	"pumpdump/backend/internal/mocks"
)

// The rule is deterministic, so it is tested with generated values and no clock.
func TestDecideThresholdAndFreshness(t *testing.T) {
	cases := []struct {
		name  string
		edge  float64
		fresh bool
		want  string
	}{
		{"stale beats a profitable edge", 5.0, false, "refused"},
		{"edge above the floor is a buy", decisionThresholdPct + 0.01, true, "buy"},
		{"edge below the floor is a sell", -decisionThresholdPct - 0.01, true, "sell"},
		{"edge at zero is no trade", 0, true, "none"},
		{"edge just inside the floor is no trade", decisionThresholdPct * 0.5, true, "none"},
		{"negative edge inside the floor is no trade", -decisionThresholdPct * 0.5, true, "none"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := decide(decisionVerdict{EdgePct: c.edge}, c.fresh)
			if got.Verdict != c.want {
				t.Fatalf("verdict = %q, want %q (edge %.4f, fresh %v)", got.Verdict, c.want, c.edge, c.fresh)
			}
			if got.Reason == "" {
				t.Fatal("every verdict must carry a reason")
			}
		})
	}
}

// The chain walk must answer "why", which means following parent links backwards.
func TestChainWalksAncestors(t *testing.T) {
	s := newTestServer(t, &mocks.Market{Bars: mocks.Series("BTCUSDT", 400, 77_000, 5_000, time.Now().UnixMilli())})

	// Two packets: a forecast, then a decision whose parent is the forecast.
	forecast := recordTestPacket(t, "intel.forecast", "BTCUSDT", "forecast", time.Now().UnixMilli())
	req := envelope.Request{
		Topic: "model.decision", Key: "BTCUSDT", Producer: "engine",
		ParentCID: forecast.Meta.CID,
	}
	decision, _, err := envelope.Run(req, time.Now(), func() (any, error) {
		return map[string]any{"verdict": "none"}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	recordEnvelope(decision)

	rec := httptest.NewRecorder()
	s.Routes().ServeHTTP(rec, httptest.NewRequest("GET", "/api/envelope/chain?cid="+decision.Meta.CID, nil))
	var body struct {
		Count   int               `json:"count"`
		Packets []envelope.Packet `json:"packets"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("%v — %s", err, rec.Body.String())
	}
	if body.Count < 2 {
		t.Fatalf("lineage depth = %d, want at least 2 (decision + its forecast): %s", body.Count, rec.Body.String())
	}
	if body.Packets[0].Meta.Topic != "model.decision" {
		t.Fatalf("lineage starts at %q, want the decision", body.Packets[0].Meta.Topic)
	}
	found := false
	for _, p := range body.Packets {
		if p.Meta.Topic == "intel.forecast" {
			found = true
		}
	}
	if !found {
		t.Fatalf("lineage does not reach the forecast it came from: %s", rec.Body.String())
	}
}
