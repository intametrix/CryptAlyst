package lake

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func rec(topic, key string, v int) Record {
	body, _ := json.Marshal(map[string]int{"v": v})
	return Record{Topic: topic, Key: key, TS: int64(v), Payload: body}
}

func TestMemoryKeepsLatestAndDropsOldest(t *testing.T) {
	m := NewMemory(3)
	for i := 1; i <= 5; i++ {
		if err := m.Write(rec("ticks", "BTC", i)); err != nil {
			t.Fatal(err)
		}
	}
	got := m.Latest("ticks", 10)
	if len(got) != 3 {
		t.Fatalf("kept %d, want 3", len(got))
	}
	if string(got[2].Payload) != `{"v":5}` {
		t.Fatalf("latest is not the newest: %s", got[2].Payload)
	}
	writes, drops := m.Stats()
	if writes != 5 || drops != 2 {
		t.Fatalf("writes=%d drops=%d, want 5/2", writes, drops)
	}
}

func TestJournalRoundTripAndReplay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lake.jsonl")
	j, err := OpenJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	for i := 1; i <= 100; i++ {
		if err := j.Write(rec("md.ticks", "BTC", i)); err != nil {
			t.Fatal(err)
		}
	}
	if err := j.Close(); err != nil {
		t.Fatal(err)
	}
	seen := 0
	n, err := Replay(path, func(r Record) error {
		seen++
		if r.Topic != "md.ticks" {
			t.Fatalf("topic = %q", r.Topic)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if n != 100 || seen != 100 {
		t.Fatalf("replayed %d (seen %d), want 100", n, seen)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal("journal file missing")
	}
}

func TestLakeFanOut(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lake.jsonl")
	j, _ := OpenJournal(path)
	m := NewMemory(10)
	l := New(m, j)
	for i := 0; i < 10; i++ {
		if err := l.Write(rec("intel.flow", "ETH", i)); err != nil {
			t.Fatal(err)
		}
	}
	if err := j.Flush(); err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	if got := len(m.Latest("intel.flow", 100)); got != 10 {
		t.Fatalf("memory has %d", got)
	}
	if j.Writes() != 10 {
		t.Fatalf("journal writes = %d", j.Writes())
	}
	if names := l.BackendNames(); len(names) != 2 {
		t.Fatalf("backends = %v", names)
	}
}
