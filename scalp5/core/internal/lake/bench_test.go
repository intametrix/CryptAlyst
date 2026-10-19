package lake

import (
	"encoding/json"
	"path/filepath"
	"testing"
)

func benchRecord(i int) Record {
	body, _ := json.Marshal(map[string]any{
		"symbol": "BTCUSDT", "bid": 82750.12, "ask": 82750.13,
		"bids": [][2]float64{{82750.12, 0.5}, {82749.9, 1.2}}, "i": i,
	})
	return Record{Topic: "market.depth", Key: "BTCUSDT", TS: int64(i), Payload: body}
}

func BenchmarkMemoryWrite(b *testing.B) {
	m := NewMemory(1000)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = m.Write(benchRecord(i))
	}
}

func BenchmarkJournalWrite(b *testing.B) {
	j, err := OpenJournal(filepath.Join(b.TempDir(), "bench.jsonl"))
	if err != nil {
		b.Fatal(err)
	}
	defer j.Close()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = j.Write(benchRecord(i))
	}
	_ = j.Flush()
}

func BenchmarkLakeFanOut(b *testing.B) {
	j, err := OpenJournal(filepath.Join(b.TempDir(), "fan.jsonl"))
	if err != nil {
		b.Fatal(err)
	}
	defer j.Close()
	l := New(NewMemory(1000), j)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = l.Write(benchRecord(i))
	}
	_ = j.Flush()
}

func BenchmarkReplay(b *testing.B) {
	const records = 20000
	path := filepath.Join(b.TempDir(), "replay.jsonl")
	j, _ := OpenJournal(path)
	for i := 0; i < records; i++ {
		_ = j.Write(benchRecord(i))
	}
	_ = j.Close()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		n, err := Replay(path, func(r Record) error { return nil })
		if err != nil || n != records {
			b.Fatalf("replay failed: %v %d", err, n)
		}
	}
	b.SetBytes(int64(records))
}
