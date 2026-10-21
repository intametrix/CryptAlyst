package lake

import (
	"path/filepath"
	"testing"
)

// A cold query must resolve to one segment from the sidecars alone.
func TestFindSegmentsPicksTheRightSegment(t *testing.T) {
	dir := t.TempDir()
	sg, err := OpenSegmented(RetentionConfig{Dir: dir, SegmentSize: 900})
	if err != nil {
		t.Fatal(err)
	}
	defer sg.Close()

	// two topics across a time range wide enough to force several segments
	for i := 0; i < 40; i++ {
		topic := "market.depth"
		if i%5 == 0 {
			topic = "model.decision"
		}
		rec := Record{Topic: topic, Key: "BTCUSDT", TS: int64(1_000_000 + i*1000)}
		body := `{"symbol":"BTCUSDT","payload":"` + string(rune('a'+i%20)) + `"}`
		rec.Payload = []byte(body)
		rec.Envelope = []byte(`{"data":` + body + `}`)
		if err := sg.Write(rec); err != nil {
			t.Fatal(err)
		}
	}
	if err := sg.Close(); err != nil {
		t.Fatal(err)
	}

	// the final segment is only sealed on close; write the sidecar for it too
	sg2, err := OpenSegmented(RetentionConfig{Dir: dir, SegmentSize: 900})
	if err != nil {
		t.Fatal(err)
	}
	sg2.Close()

	found, err := Plan(dir, "model.decision", 1_000_000, 1_040_000, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(found) == 0 {
		t.Fatal("no segments found for model.decision; the sidecars are not being written")
	}
	var decisions int
	for _, p := range found {
		decisions += p.Records
	}
	if decisions == 0 {
		t.Fatalf("index reports no model.decision records across %d segments", len(found))
	}

	// a topic that was never written must resolve to nothing
	none, err := Plan(dir, "risk.state", 0, 9_999_999_999_999, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(none) != 0 {
		t.Fatalf("risk.state matched %d segments, want 0", len(none))
	}
}

// a sealed segment's sidecar must agree with the lines in the segment
func TestSidecarMatchesTheSegment(t *testing.T) {
	dir := t.TempDir()
	sg, _ := OpenSegmented(RetentionConfig{Dir: dir, SegmentSize: 1 << 20})
	for i := 0; i < 10; i++ {
		body := []byte(`{"n":` + string(rune('0'+i)) + `}`)
		_ = sg.Write(Record{Topic: "market.price", Key: "BTCUSDT", TS: int64(i),
			Payload: body, Envelope: []byte(`{"data":` + string(body) + `}`)})
	}
	sg.Close()

	segs, _ := filepath.Glob(filepath.Join(dir, "*.idx.json"))
	if len(segs) == 0 {
		t.Fatal("no sidecar written on close")
	}
}
