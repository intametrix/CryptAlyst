package lake

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// KIMI's hard gate: buffered writes must be byte-identical to unbuffered ones.
func TestBufferedWritesMatchUnbuffered(t *testing.T) {
	recs := make([]Record, 0, 5000)
	for i := 0; i < 5000; i++ {
		body := []byte(`{"n":` + string(rune('0'+i%10)) + `}`)
		recs = append(recs, Record{Topic: "model.fill", Key: "BTCUSDT", TS: int64(1_700_000_000_000 + i),
			Payload: body, Envelope: []byte(`{"data":` + string(body) + `}`)})
	}

	// unbuffered: the plain journal
	jdir := t.TempDir()
	j, err := OpenJournal(filepath.Join(jdir, "j.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range recs {
		if err := j.Write(r); err != nil {
			t.Fatal(err)
		}
	}
	j.Close()
	want, err := os.ReadFile(filepath.Join(jdir, "j.jsonl"))
	if err != nil {
		t.Fatal(err)
	}

	// buffered: the sealing writer, sealed on close
	sdir := t.TempDir()
	s, err := OpenSealed(RetentionConfig{Dir: sdir}, HourlyOrCeiling{Age: time.Hour, Bytes: 1 << 30})
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range recs {
		if err := s.Write(r); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.Flush(); err != nil {
		t.Fatal(err)
	}
	s.Close()

	segs, _ := filepath.Glob(filepath.Join(sdir, "seg-*.jsonl"))
	if len(segs) != 1 {
		t.Fatalf("expected one segment, found %d", len(segs))
	}
	got, err := os.ReadFile(segs[0])
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(want, got) {
		t.Fatalf("buffered output differs from unbuffered: %d vs %d bytes", len(want), len(got))
	}
	if strings.Contains(string(got), `"payload":`) {
		t.Fatal("buffered output still contains a payload field")
	}
}

// an fsync that fails is counted, and the writer stays usable
func TestFlushErrorIsCounted(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 1 << 30})
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		// the file is already closed underneath; Close must not panic
		_ = s
	}()
	body := []byte(`{"n":1}`)
	if err := s.Write(Record{Topic: "market.price", Key: "BTCUSDT", TS: 1,
		Payload: body, Envelope: []byte(`{"data":` + string(body) + `}`)}); err != nil {
		t.Fatal(err)
	}
	s.mu.Lock()
	s.file.Close() // simulate the descriptor failing under us
	s.mu.Unlock()
	if err := s.Flush(); err == nil {
		t.Fatal("flush on a closed file should report an error")
	}
	_, shorts, _ := s.Counters()
	if shorts == 0 {
		t.Fatal("short-write counter did not move on a failed flush")
	}
}
