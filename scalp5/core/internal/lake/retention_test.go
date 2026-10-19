package lake

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSegmentedRotates(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenSegmented(RetentionConfig{Dir: dir, SegmentSize: 2048, MaxAge: time.Hour})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 200; i++ {
		if err := s.Write(benchRecord(i)); err != nil {
			t.Fatal(err)
		}
	}
	writes, rotations, _, _, segments := s.Stats()
	if writes != 200 {
		t.Fatalf("writes = %d", writes)
	}
	if rotations == 0 || segments < 2 {
		t.Fatalf("no rotation: rotations=%d segments=%d", rotations, segments)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	total, err := ReplaySegments(dir, func(r Record) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if total != 200 {
		t.Fatalf("replayed %d, want 200", total)
	}
}

func TestSegmentedCompressesClosedSegments(t *testing.T) {
	dir := t.TempDir()
	s, _ := OpenSegmented(RetentionConfig{Dir: dir, SegmentSize: 1024, MaxAge: time.Hour, Compress: true})
	for i := 0; i < 120; i++ {
		_ = s.Write(benchRecord(i))
	}
	// age the closed segments so the sweep treats them as closed
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		p := filepath.Join(dir, e.Name())
		old := time.Now().Add(-2 * time.Minute)
		_ = os.Chtimes(p, old, old)
	}
	s.ExpireOnce()
	_, _, compressed, _, _ := s.Stats()
	if compressed == 0 {
		t.Fatal("nothing compressed")
	}
	gz := 0
	entries, _ = os.ReadDir(dir)
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".gz") {
			gz++
		}
	}
	if gz == 0 {
		t.Fatal("no .gz segment on disk")
	}
	s.Close()
	if n, err := ReplaySegments(dir, func(r Record) error { return nil }); err != nil || n < 100 {
		t.Fatalf("replay across compressed segments failed: n=%d err=%v", n, err)
	}
}

func TestSegmentedDeletesExpired(t *testing.T) {
	dir := t.TempDir()
	s, _ := OpenSegmented(RetentionConfig{Dir: dir, SegmentSize: 512, MaxAge: time.Minute})
	for i := 0; i < 80; i++ {
		_ = s.Write(benchRecord(i))
	}
	entries, _ := os.ReadDir(dir)
	before := len(entries)
	for _, e := range entries[:before-1] {
		p := filepath.Join(dir, e.Name())
		old := time.Now().Add(-10 * time.Minute)
		_ = os.Chtimes(p, old, old)
	}
	s.ExpireOnce()
	_, _, _, deleted, _ := s.Stats()
	if deleted == 0 {
		t.Fatal("nothing expired")
	}
	s.Close()
}
