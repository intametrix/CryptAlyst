package lake

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"testing"
)

// A market-depth record, about the size the feed actually produces.
func benchRecord(i int) Record {
	body := []byte(fmt.Sprintf(
		`{"symbol":"BTCUSDT","bids":[{"p":%d.1,"q":2.5},{"p":%d.2,"q":1.5}],"asks":[{"p":%d.3,"q":3.5}],"imbalance":0.12,"ts":%d}`,
		i, i, i, i))
	env, _ := json.Marshal(map[string]any{"data": json.RawMessage(body),
		"meta": map[string]any{"topic": "market.depth", "cid": "cid-abcdef0123456789", "gen": i}})
	return Record{Topic: "market.depth", Key: "BTCUSDT", TS: int64(i), Payload: body, Envelope: env}
}

// BenchmarkJournalAppend is the hot path: one line per envelope, thousands a second.
func BenchmarkJournalAppend(b *testing.B) {
	j, err := OpenJournal(filepath.Join(b.TempDir(), "j.jsonl"))
	if err != nil {
		b.Fatal(err)
	}
	defer j.Close()
	rec := benchRecord(1)
	b.SetBytes(int64(len(rec.Envelope) + len(rec.Payload)))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if err := j.Write(rec); err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkLakeWrite measures the composite: journal plus placement, which is what
// the recorder actually calls.
func BenchmarkLakeWrite(b *testing.B) {
	j, err := OpenJournal(filepath.Join(b.TempDir(), "j.jsonl"))
	if err != nil {
		b.Fatal(err)
	}
	defer j.Close()
	lk := New(j)
	rec := benchRecord(1)
	b.SetBytes(int64(len(rec.Envelope) + len(rec.Payload)))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if err := lk.Write(rec); err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkPlacementDecision is the routing decision alone: it must stay in-memory
// and allocation-light, because it runs for every record.
func BenchmarkPlacementDecision(b *testing.B) {
	sql, docs, kv := newSinks()
	p := NewPlacement(DefaultPolicy(), sql, docs, kv)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = p.Route("model.fill")
	}
}
