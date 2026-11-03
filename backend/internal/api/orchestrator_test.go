package api

import (
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"

	"pumpdump/backend/internal/envelope"
	"pumpdump/backend/internal/mocks"
)

// The orchestrator reasons over two things only: the newest packet for a topic+key,
// and that topic's deadline. These tests drive both with a hand-built journal.

func recordTestPacket(t *testing.T, topic, key, producer string, computedAt int64) envelope.Packet {
	t.Helper()
	req := envelope.Request{Topic: topic, Key: key, Producer: producer, ComputedAt: computedAt}
	now := time.UnixMilli(computedAt)
	p, _, err := envelope.Run(req, now, func() (any, error) { return map[string]any{"probe": true}, nil })
	if err != nil {
		t.Fatalf("build packet: %v", err)
	}
	recordEnvelope(p)
	return p
}

// A fresh answer is served; nothing should be recomputed.
func TestOrchestratorServesAFreshAnswer(t *testing.T) {
	s := newTestServer(t, &mocks.Market{})
	now := time.Now().UnixMilli()
	recordTestPacket(t, "intel.forecast", "BTCUSDT", "forecast", now)

	plan, err := s.planFor("BTCUSDT", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	var found bool
	for _, p := range plan {
		if p.Topic == "intel.forecast" && p.Key == "BTCUSDT" {
			found = true
			if p.Decision != "serve" {
				t.Fatalf("fresh packet should be served, got %q (%s)", p.Decision, p.Reason)
			}
		}
	}
	if !found {
		t.Fatalf("intel.forecast missing from the plan: %+v", plan)
	}
}

// An answer older than its deadline is scheduled for recompute, and the tick
// publishes a control packet naming the capability.
func TestOrchestratorRecomputesAStaleAnswer(t *testing.T) {
	s := newTestServer(t, &mocks.Market{})
	// intel.forecast has a 60s TTL in the registry; this is two minutes old.
	recordTestPacket(t, "intel.forecast", "BTCUSDT", "forecast", time.Now().Add(-2*time.Minute).UnixMilli())

	plan, err := s.planFor("BTCUSDT", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	var stale bool
	for _, p := range plan {
		if p.Topic == "intel.forecast" && p.Decision == "recompute" && p.Reason == "older than its deadline" {
			stale = true
		}
	}
	if !stale {
		t.Fatalf("a two-minute-old forecast should be recomputed: %+v", plan)
	}

	rec := httptest.NewRecorder()
	s.Routes().ServeHTTP(rec, httptest.NewRequest("POST", "/api/orchestrator/tick?symbol=BTCUSDT", nil))
	var body struct {
		Count     int `json:"count"`
		Requested []struct {
			Capability string `json:"capability"`
		} `json:"requested"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("tick answer: %v — %s", err, rec.Body.String())
	}
	if body.Count == 0 {
		t.Fatal("the tick requested no work for a stale capability")
	}
	counts, err := envelope.Count(envelopeJournalPath())
	if err != nil {
		t.Fatal(err)
	}
	if counts["control.engine"] == 0 {
		t.Fatalf("no control.engine packet recorded: %v", counts)
	}
}

// A capability with nothing recorded is recompute, not serve — an absent answer is
// not a fresh one.
func TestOrchestratorAsksForMissingEvidence(t *testing.T) {
	s := newTestServer(t, &mocks.Market{})
	plan, err := s.planFor("ETHUSDT", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if len(plan) == 0 {
		t.Fatal("an empty plan hides the fact that nothing has been computed")
	}
	for _, p := range plan {
		if p.Decision != "recompute" {
			t.Fatalf("missing evidence was treated as %q for %s", p.Decision, p.Topic)
		}
	}
}

// The orchestrator must not know what a model computes: its table is derived from
// the topic registry.
func TestCapabilitiesComeFromTheRegistry(t *testing.T) {
	caps := capabilities()
	if len(caps) == 0 {
		t.Fatal("no capabilities derived from the registry")
	}
	seen := map[string]bool{}
	for _, c := range caps {
		seen[c.Topic] = true
		if c.MaxAgeMS <= 0 {
			t.Fatalf("%s has no deadline", c.Name)
		}
	}
	if !seen["intel.forecast"] || !seen["model.decision"] {
		t.Fatalf("expected the finance topics in the capability table: %v", seen)
	}
}
