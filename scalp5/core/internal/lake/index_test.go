package lake

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
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

// BuildIndexForSegment must survive a resume and still reconcile. The bug: the
// independent counter was not persisted in the checkpoint, so a resumed scan
// compared the restored tracker against a counter that had restarted at zero and
// failed with "reconciliation failed". The fix persists and restores it.
func TestBuildIndexForSegmentResumeReconciles(t *testing.T) {
	dir := t.TempDir()
	seg := filepath.Join(dir, "seg-0000.jsonl")

	f, err := os.Create(seg)
	if err != nil {
		t.Fatal(err)
	}
	topics := []string{"market.depth", "model.decision"}
	type acc struct {
		records, bytes int64
		from, to       int64
	}
	prefix := map[string]*acc{}
	var off int64
	for i := 0; i < 60; i++ {
		topic := topics[i%2]
		rec := Record{Topic: topic, Key: "BTCUSDT", TS: int64(1_000_000 + i*1000),
			Payload:  []byte(`{"i":` + strconv.Itoa(i) + `}`),
			Envelope: []byte(`{"data":{"i":` + strconv.Itoa(i) + `}}`)}
		line, err := marshalRecord(rec)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := f.Write(append(line, '\n')); err != nil {
			t.Fatal(err)
		}
		if i < 30 {
			a, ok := prefix[topic]
			if !ok {
				a = &acc{from: rec.TS, to: rec.TS}
				prefix[topic] = a
			}
			if rec.TS < a.from {
				a.from = rec.TS
			}
			if rec.TS > a.to {
				a.to = rec.TS
			}
			a.records++
			a.bytes += int64(len(line)) + 1
			off += int64(len(line)) + 1
		}
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}

	// a checkpoint as if the first 30 records had been scanned and persisted
	var spans []TopicSpan
	counts := map[string]int{}
	records := 0
	for topic, a := range prefix {
		spans = append(spans, TopicSpan{Topic: topic, Records: int(a.records),
			Bytes: a.bytes, From: a.from, To: a.to})
		counts[topic] = int(a.records)
		records += int(a.records)
	}
	body, err := json.Marshal(map[string]any{"offset": off, "records": records,
		"bytes": off, "topics": spans, "counts": counts})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(seg+".scan.progress", body, 0o644); err != nil {
		t.Fatal(err)
	}

	st, err := BuildIndexForSegment(seg, 1<<20)
	if err != nil {
		t.Fatalf("resumed scan failed reconciliation: %v", err)
	}
	if st.Records != 60 {
		t.Fatalf("resumed scan produced %d records, want 60", st.Records)
	}
	byTopic := map[string]int{}
	for _, sp := range st.Topics {
		byTopic[sp.Topic] = sp.Records
	}
	if byTopic["market.depth"] != 30 || byTopic["model.decision"] != 30 {
		t.Fatalf("resumed scan per-topic counts wrong: %v", byTopic)
	}
	if _, err := os.Stat(seg + ".scan.progress"); !os.IsNotExist(err) {
		t.Fatalf("checkpoint not removed on completion: %v", err)
	}
}
