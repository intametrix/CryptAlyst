package lake

import (
	"testing"

	"cryptalyst/core/internal/store"
)

// recordingSink counts what it was given, so a test can assert that a topic
// reached exactly the sinks it is meant to reach — and no others.
type recordingSink struct {
	name  string
	takes map[string]bool
	got   []string
}

func (s *recordingSink) Name() string { return s.name }
func (s *recordingSink) Takes(topic string) bool {
	if len(s.takes) == 0 {
		return true
	}
	return s.takes[topic]
}
func (s *recordingSink) Write(rec store.Envelope) error {
	s.got = append(s.got, rec.Topic)
	return nil
}

func newSinks() (*recordingSink, *recordingSink, *recordingSink) {
	return &recordingSink{name: "sql"}, &recordingSink{name: "docs"}, &recordingSink{name: "kv"}
}

// market data is the bulk of the volume and is never queried by field: journal only.
func TestMarketDataStaysInTheJournal(t *testing.T) {
	sql, docs, kv := newSinks()
	p := NewPlacement(DefaultPolicy(), sql, docs, kv)
	for _, topic := range []string{"market.depth", "market.price", "market.candle", "ticks"} {
		if got := p.Route(topic); len(got) != 0 {
			t.Fatalf("%s routed to %d sinks, want 0 (journal only)", topic, len(got))
		}
	}
}

// a fill earns SQLite; a decision earns SQLite and the document plane.
func TestFillsAndDecisionsRoute(t *testing.T) {
	sql, docs, kv := newSinks()
	p := NewPlacement(DefaultPolicy(), sql, docs, kv)

	if got := names(p.Route("model.fill")); len(got) != 1 || got[0] != "sql" {
		t.Fatalf("model.fill -> %v, want [sql]", got)
	}
	if got := names(p.Route("model.decision")); len(got) != 2 {
		t.Fatalf("model.decision -> %v, want [sql docs]", got)
	}
	if got := names(p.Route("intel.signal")); len(got) != 1 || got[0] != "docs" {
		t.Fatalf("intel.signal -> %v, want [docs]", got)
	}
	if got := names(p.Route("risk.state")); len(got) != 0 {
		t.Fatalf("risk.state -> %v, want journal only", got)
	}
}

// a sink that is not configured is skipped, never fatal.
func TestMissingSinkIsIgnored(t *testing.T) {
	sql, _, _ := newSinks()
	p := NewPlacement(DefaultPolicy(), sql)
	if got := names(p.Route("model.decision")); len(got) != 1 || got[0] != "sql" {
		t.Fatalf("with only sql configured, model.decision -> %v, want [sql]", got)
	}
}

// the invariant from the design: the stored record carries correlation ids, not
// resolved JSON. Placement must never add to the record.
func TestPlacementDoesNotRewriteTheRecord(t *testing.T) {
	sql, docs, _ := newSinks()
	p := NewPlacement(DefaultPolicy(), sql, docs)
	rec := store.Envelope{
		ID: "x1", Topic: "model.decision", Key: "BTCUSDT", TS: 1,
		Data: []byte(`{"cid":"cid-abc","allowed":true}`),
	}
	before := string(rec.Data)
	if err := p.Place(rec); err != nil {
		t.Fatal(err)
	}
	if string(rec.Data) != before {
		t.Fatalf("placement modified the record: %s -> %s", before, rec.Data)
	}
	if len(sql.got) != 1 || len(docs.got) != 1 {
		t.Fatalf("routing wrote sql=%d docs=%d, want 1 each", len(sql.got), len(docs.got))
	}
}

func names(sinks []Sink) []string {
	out := make([]string, 0, len(sinks))
	for _, s := range sinks {
		out = append(out, s.Name())
	}
	return out
}
