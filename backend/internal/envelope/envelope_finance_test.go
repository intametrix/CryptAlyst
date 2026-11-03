package envelope

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// A refusal is a packet you can open, not a log line.
func TestRefusalIsRecordedAsEnvelope(t *testing.T) {
	req := Request{Topic: "model.decision", Key: "BTCUSDT", Producer: "not-a-model"}
	_, pl, err := Run(req, time.Now(), func() (any, error) { return nil, nil })
	if err == nil {
		t.Fatal("expected the authority interceptor to refuse an unregistered producer")
	}
	stage, ok := pl.FirstRefusal()
	if !ok || stage.Name != "authority" {
		t.Fatalf("refusal stage = %+v", stage)
	}
	p := RefusalPacket(req, pl, stage.Detail)
	if p.Meta.Topic != "system.error" {
		t.Fatalf("refusal topic = %q", p.Meta.Topic)
	}
	var body map[string]any
	if err := p.Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body["verdict"] != "REFUSED" || body["stage"] != "authority" {
		t.Fatalf("refusal body = %v", body)
	}
}

// Staleness is refused from the payload's own label, not a wall-clock guess.
func TestFreshnessRefusesStalePayload(t *testing.T) {
	old := time.Now().Add(-2 * time.Minute).UnixMilli()
	req := Request{Topic: "intel.forecast", Key: "BTCUSDT", Producer: "forecast", ComputedAt: old}
	_, pl, err := Run(req, time.Now(), func() (any, error) { return map[string]any{"x": 1}, nil })
	if err == nil {
		t.Fatal("expected the freshness interceptor to refuse a 2-minute-old payload (TTL 60s)")
	}
	stage, _ := pl.FirstRefusal()
	if stage.Name != "freshness" {
		t.Fatalf("refusal stage = %q, want freshness", stage.Name)
	}
}

// A published packet carries provenance and a correlation id.
func TestPacketCarriesProvenance(t *testing.T) {
	req := Request{Topic: "intel.forecast", Key: "BTCUSDT", Producer: "forecast",
		Refs: []Ref{{Topic: "market.candle", Key: "BTCUSDT", Generation: 7, AgeMS: 1200}}}
	p, pl, err := Run(req, time.Now(), func() (any, error) { return map[string]any{"path": []float64{1, 2}}, nil })
	if err != nil {
		t.Fatal(err)
	}
	if !pl.AllPassed() {
		t.Fatalf("pipeline did not pass: %+v", pl)
	}
	if p.Meta.CID == "" || p.Meta.Generation == 0 || p.Meta.ComputedAt == 0 {
		t.Fatalf("meta incomplete: %+v", p.Meta)
	}
	if len(p.Refs) != 1 || p.Refs[0].Topic != "market.candle" {
		t.Fatalf("refs = %+v", p.Refs)
	}
	if ok, age, limit := p.Fresh(time.Now()); !ok || age < 0 || limit != 60_000 {
		t.Fatalf("freshness = %v %d %d", ok, age, limit)
	}
}

// The journal appends and replays in order.
func TestJournalAppendAndReplay(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "journal.jsonl")
	j, err := OpenJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		p, _, err := Run(Request{Topic: "intel.forecast", Key: "BTCUSDT", Producer: "forecast"},
			time.Now(), func() (any, error) { return map[string]any{"i": i}, nil })
		if err != nil {
			t.Fatal(err)
		}
		if err := j.Record(p); err != nil {
			t.Fatal(err)
		}
	}
	if err := j.Close(); err != nil {
		t.Fatal(err)
	}

	seen := 0
	var gens []int64
	if err := Replay(path, nil, func(p Packet) {
		seen++
		gens = append(gens, p.Meta.Generation)
	}); err != nil {
		t.Fatal(err)
	}
	if seen != 3 {
		t.Fatalf("replayed %d packets, want 3", seen)
	}
	for i := 1; i < len(gens); i++ {
		if gens[i] <= gens[i-1] {
			t.Fatalf("generations not monotonic: %v", gens)
		}
	}
	counts, err := Count(path)
	if err != nil {
		t.Fatal(err)
	}
	if counts["intel.forecast"] != 3 {
		t.Fatalf("counts = %v", counts)
	}
	_ = os.Remove(path)
}
