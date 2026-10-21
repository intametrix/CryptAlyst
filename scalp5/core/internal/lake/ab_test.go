package lake

import (
	"path/filepath"
	"sort"
	"testing"
	"time"
)

// KIMI's condition before the raw scan is retired: the same window must return the
// same rows through the scan path and through Plan()+ScanSegment.
func TestABScanVersusPlan(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 1500})
	if err != nil {
		t.Fatal(err)
	}
	base := int64(1_700_000_000_000)
	for i := 0; i < 200; i++ {
		topic := "market.depth"
		if i%4 == 0 {
			topic = "model.decision"
		}
		body := []byte(`{"i":` + string(rune('0'+i%10)) + `}`)
		if err := s.Write(Record{Topic: topic, Key: "BTCUSDT", TS: base + int64(i)*100,
			Payload: body, Envelope: []byte(`{"data":` + string(body) + `}`)}); err != nil {
			t.Fatal(err)
		}
	}
	s.Close()

	from, to := base, base+20_000
	topic := "market.depth"

	// path A: scan every segment file without a plan
	var scanIDs []int64
	segs, _ := filepath.Glob(filepath.Join(dir, "seg-*"))
	for _, seg := range segs {
		if _, err := ReadIndex(seg); err != nil && filepath.Ext(seg) != ".gz" {
			continue // index or sidecar, not a segment
		}
		if _, err := ScanSegment(seg, from, to, func(r Record) error {
			if r.Topic == topic {
				scanIDs = append(scanIDs, r.TS)
			}
			return nil
		}); err != nil {
			continue // not a segment
		}
	}

	// path B: plan, then scan only what the plan names
	plans, err := Plan(dir, topic, from, to, "")
	if err != nil {
		t.Fatal(err)
	}
	var planIDs []int64
	for _, p := range plans {
		if _, err := ScanSegment(p.Segment, from, to, func(r Record) error {
			if r.Topic == topic {
				planIDs = append(planIDs, r.TS)
			}
			return nil
		}); err != nil {
			t.Fatal(err)
		}
	}

	sort.Slice(scanIDs, func(i, j int) bool { return scanIDs[i] < scanIDs[j] })
	sort.Slice(planIDs, func(i, j int) bool { return planIDs[i] < planIDs[j] })
	if len(scanIDs) != len(planIDs) {
		t.Fatalf("A-B mismatch: scan %d rows, plan %d rows", len(scanIDs), len(planIDs))
	}
	for i := range scanIDs {
		if scanIDs[i] != planIDs[i] {
			t.Fatalf("A-B mismatch at %d: scan %d, plan %d", i, scanIDs[i], planIDs[i])
		}
	}
	if len(planIDs) == 0 {
		t.Fatal("the sample window returned no rows; the test proves nothing")
	}
}
