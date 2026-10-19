package ledger

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

// (c) Round-trip: every record type the ledger writes carries the units
// block, and the values match the implemented convention.
func TestWrittenRecordsCarryUnits(t *testing.T) {
	m := fakeMarket{map[string]float64{"AAA": 98.5}}
	r := newTestRecorder(t, m, Config{Horizon: time.Minute})
	r.Record(testSignal("p1", "PUMP", "AAA", 1, 100))
	r.tick(context.Background(), 1000+10_000) // resolves WRONG at stop-out
	if err := r.Retract("p1", "test"); err != nil {
		t.Fatal(err)
	}
	r.Close()

	f, err := os.Open(r.cfg.Path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	types := map[string]bool{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Bytes()
		var head struct {
			Type  string            `json:"type"`
			Units map[string]string `json:"units"`
		}
		if err := json.Unmarshal(line, &head); err != nil {
			t.Fatalf("line not valid JSON: %v", err)
		}
		types[head.Type] = true
		if head.Units == nil {
			t.Fatalf("record %q missing units block: %s", head.Type, line)
		}
		for k, want := range unitsBlock {
			if head.Units[k] != want {
				t.Fatalf("record %q units.%s = %q, want %q", head.Type, k, head.Units[k], want)
			}
		}
	}
	for _, want := range []string{"prediction", "path", "resolution", "retraction"} {
		if !types[want] {
			t.Fatalf("no %q record written in round-trip", want)
		}
	}
}

// (b) A units block naming an unimplemented convention fails loudly, naming
// the record type, the field, and the value.
func TestCheckUnitsLoudFailure(t *testing.T) {
	raw := []byte(`{"type":"resolution","id":"x","label":"EXPIRED","mfe_pct":0.5,"units":{"mfe_pct":"fraction_of_entry"}}`)
	err := CheckUnits("resolution", raw)
	if err == nil {
		t.Fatal("expected loud failure")
	}
	msg := err.Error()
	for _, part := range []string{`"resolution"`, "units.mfe_pct", "fraction_of_entry"} {
		if !strings.Contains(msg, part) {
			t.Fatalf("error %q missing %q", msg, part)
		}
	}
}

// (b2) Unknown keys inside a known convention are ignored.
func TestCheckUnitsIgnoresUnknownKeys(t *testing.T) {
	raw := []byte(`{"type":"path_1s","units":{"price":"quote_usd","pct_fields":"percent_of_entry","mfe_pct":"percent_of_entry","mae_pct":"percent_of_entry","future_field":"some_convention"}}`)
	if err := CheckUnits("path_1s", raw); err != nil {
		t.Fatalf("unknown key inside known convention must be tolerated: %v", err)
	}
}

// (a) Absent units means the historical convention: parses as today. This
// is exercised by every pre-existing fixture test; here explicitly.
func TestCheckUnitsAbsentIsHistorical(t *testing.T) {
	raw := []byte(`{"type":"resolution","id":"x","label":"EXPIRED","mfe_pct":0.5}`)
	if err := CheckUnits("resolution", raw); err != nil {
		t.Fatalf("absent units must parse: %v", err)
	}
}
