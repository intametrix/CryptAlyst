package api

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// TestHistoryForSymbolFallsBackToDiskBeyondMemory is the regression test for
// HistoryForSymbol answering from the in-memory ring alone. The ring is a
// memory bound (the loader trims each symbol to its last 400 on startup), not
// a retention bound: the file keeps every alert, so a request deeper than
// memory must be completed from disk rather than silently truncated.
func TestHistoryForSymbolFallsBackToDiskBeyondMemory(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "alerts.jsonl")

	// 450 alerts on disk, written directly. SetHistoryPath loads the per-symbol
	// history but caps it at 400, so memory alone cannot satisfy a limit of 450.
	const total = 450
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	enc := json.NewEncoder(f)
	for i := 0; i < total; i++ {
		if err := enc.Encode(Alert{Type: "alert", Symbol: "XXUSDT", TSMS: int64(1_000_000 + i), Kind: "PUMP"}); err != nil {
			t.Fatal(err)
		}
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}

	b := NewAlertBuffer(500)
	b.SetHistoryPath(path)

	got := b.HistoryForSymbol("XXUSDT", total)
	if len(got) != total {
		t.Fatalf("HistoryForSymbol returned %d alerts, want %d: the disk fallback must complete the answer beyond the memory cap", len(got), total)
	}
	// Newest first, all the way back to the oldest record on disk.
	if got[0].TSMS != int64(1_000_000+total-1) {
		t.Fatalf("first alert TSMS = %d, want newest %d (order must be newest first)", got[0].TSMS, int64(1_000_000+total-1))
	}
	if got[len(got)-1].TSMS != int64(1_000_000) {
		t.Fatalf("last alert TSMS = %d, want oldest %d (the fallback must reach the file's first record)", got[len(got)-1].TSMS, int64(1_000_000))
	}
}

// TestHistoryForSymbolServesMemoryWhenItCan: when the ring holds enough, the
// answer comes straight from memory (no path configured at all -- if the
// implementation needed the file here, this buffer could not produce it).
func TestHistoryForSymbolServesMemoryWhenItCan(t *testing.T) {
	b := NewAlertBuffer(500)
	for i := 0; i < 5; i++ {
		b.Add(Alert{Type: "alert", Symbol: "YYUSDT", TSMS: int64(2_000_000 + i), Kind: "DUMP"})
	}

	got := b.HistoryForSymbol("YYUSDT", 5)
	if len(got) != 5 {
		t.Fatalf("got %d alerts, want 5", len(got))
	}
	if got[0].TSMS != 2_000_004 || got[len(got)-1].TSMS != 2_000_000 {
		t.Fatalf("memory path must be newest first, got first=%d last=%d", got[0].TSMS, got[len(got)-1].TSMS)
	}

	capped := b.HistoryForSymbol("YYUSDT", 3)
	if len(capped) != 3 || capped[0].TSMS != 2_000_004 {
		t.Fatalf("limit=3 must return the 3 newest, got %d alerts starting at %d", len(capped), capped[0].TSMS)
	}
}
