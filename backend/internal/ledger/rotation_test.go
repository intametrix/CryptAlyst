package ledger

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// silentFetcher satisfies PriceFetcher; rotation does not need real prices.
type silentFetcher struct{}

func (silentFetcher) Price(context.Context, string) (float64, error) { return 1, nil }

// A ledger must not grow without bound: past MaxBytes it rotates, and only
// KeepFiles rotations survive.
func TestLedgerRotatesAndPrunes(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ledger.jsonl")

	r, err := New(path, silentFetcher{}, Config{MaxBytes: 2 << 10, KeepFiles: 2})
	if err != nil {
		t.Fatalf("new: %v", err)
	}
	// Write far past several rotations.
	for i := 0; i < 400; i++ {
		if err := r.write(map[string]any{"type": "filler", "i": i, "pad": "0123456789012345678901234567890123456789"}); err != nil {
			t.Fatalf("write %d: %v", i, err)
		}
	}
	_ = r.Close()

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	rotated := 0
	for _, e := range entries {
		if len(e.Name()) > len("ledger.jsonl") && e.Name()[:len("ledger.jsonl")] == "ledger.jsonl" {
			rotated++
		}
	}
	if rotated > 2 {
		t.Fatalf("expected at most 2 rotated files kept, found %d", rotated)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Size() > 4<<10 {
		t.Fatalf("active file should have rotated near 2KB, got %d bytes", st.Size())
	}
	t.Logf("active %d bytes, %d rotated files kept", st.Size(), rotated)
}

// Replay must read only the tail, so a huge file cannot stall startup.
func TestReadTailIsBounded(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "big.jsonl")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	line := make([]byte, 0, 200)
	for i := 0; i < 1000; i++ {
		line = append(line[:0], []byte("{\"type\":\"x\",\"pad\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}\n")...)
		if _, werr := f.Write(line); werr != nil {
			t.Fatal(werr)
		}
	}
	_ = f.Close()

	got, err := readTail(path, 1024)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) > 1024 {
		t.Fatalf("tail should be bounded by 1024 bytes, got %d", len(got))
	}
	if len(got) == 0 {
		t.Fatal("tail read returned nothing")
	}
	// The first byte must start a record, not a fragment.
	if got[0] != '{' {
		t.Fatalf("tail should start on a record boundary, got %q", got[0])
	}
}
