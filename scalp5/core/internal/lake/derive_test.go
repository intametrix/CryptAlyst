package lake

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// goldenLine is a real journal line shape, payload and envelope both present.
func goldenLine(t *testing.T) Record {
	t.Helper()
	payload := json.RawMessage(`{"symbol":"BTCUSDT","bids":[{"p":1.1,"q":2.5}],"asks":[{"p":1.2,"q":1.5}],"imbalance":0.12,"ts":1700000000000}`)
	env := map[string]any{
		"data": json.RawMessage(payload),
		"meta": map[string]any{"topic": "market.depth", "key": "BTCUSDT", "cid": "cid-abc", "version": 1},
		"refs": []any{},
	}
	raw, _ := json.Marshal(env)
	return Record{Topic: "market.depth", Key: "BTCUSDT", TS: 1700000000000, Payload: payload, Envelope: raw}
}

// The condition KIMI set: deriving the payload must reproduce the stored bytes
// exactly, or the change is not safe.
func TestDerivedPayloadIsByteIdentical(t *testing.T) {
	rec := goldenLine(t)
	j, err := OpenJournal(filepath.Join(t.TempDir(), "j.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if err := j.Write(rec); err != nil {
		t.Fatal(err)
	}
	j.Close()

	var back Record
	n, err := Replay(filepath.Join(filepath.Dir(j.path), "j.jsonl"), func(r Record) error {
		back = r
		return nil
	})
	if err != nil || n != 1 {
		t.Fatalf("replay: n=%d err=%v", n, err)
	}
	if len(back.Payload) != 0 {
		t.Fatalf("payload was still stored (%d bytes); the duplication is not removed", len(back.Payload))
	}
	derived, err := DerivePayload(back)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(derived, rec.Payload) {
		t.Fatalf("derived payload differs\n want %s\n  got %s", rec.Payload, derived)
	}
}

// old segments still carry payload, and must keep working unchanged
func TestOldSegmentWithPayloadStillReads(t *testing.T) {
	rec := goldenLine(t)
	j, _ := OpenJournal(filepath.Join(t.TempDir(), "old.jsonl"))
	line, _ := json.Marshal(rec) // the old form: payload present
	j.w.Write(append(line, '\n'))
	j.Close()

	got, err := DerivePayload(rec)
	if err != nil || !bytes.Equal(got, rec.Payload) {
		t.Fatalf("old segment payload not readable: %v", err)
	}
}

// and a record with neither is an error, not a silent empty payload
func TestDeriveRefusesAnEmptyRecord(t *testing.T) {
	if _, err := DerivePayload(Record{Topic: "x"}); err == nil {
		t.Fatal("expected an error for a record with no payload and no envelope")
	}
}

// the journal file is smaller than the record it replaced
func TestWrittenLineDropsThePayload(t *testing.T) {
	rec := goldenLine(t)
	j, _ := OpenJournal(filepath.Join(t.TempDir(), "j.jsonl"))
	if err := j.Write(rec); err != nil {
		t.Fatal(err)
	}
	j.Close()
	body, err := os.ReadFile(j.path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(body, []byte(`"payload":`)) {
		t.Fatalf("journal line still contains a payload field: %s", body[:120])
	}
}
