package lake

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func writeN(t *testing.T, s *Sealed, n int, topic string, baseTS int64) {
	t.Helper()
	for i := 0; i < n; i++ {
		body := []byte(`{"symbol":"BTCUSDT","i":` + string(rune('0'+i%10)) + `}`)
		rec := Record{Topic: topic, Key: "BTCUSDT", TS: baseTS + int64(i)*1000,
			Payload: body, Envelope: []byte(`{"data":` + string(body) + `}`)}
		if err := s.Write(rec); err != nil {
			t.Fatal(err)
		}
	}
}

// (a) rotation by size, and every sealed segment has a crc-valid sidecar
func TestSealBySizeWritesSidecars(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 800})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	writeN(t, s, 40, "market.depth", 1_700_000_000_000)

	sealed, _, _ := s.Stats()
	if sealed == 0 {
		t.Fatal("no segment sealed at 800 bytes; the size policy did not fire")
	}
	idx, _ := filepath.Glob(filepath.Join(dir, "*"+idxSuffix))
	if len(idx) == 0 {
		t.Fatal("no sidecar written")
	}
	for _, f := range idx {
		seg := strings.TrimSuffix(f, idxSuffix)
		st, err := ReadIndex(seg)
		if err != nil {
			t.Fatalf("sidecar does not verify: %v", err)
		}
		if st.Records == 0 || len(st.Topics) == 0 {
			t.Fatalf("sidecar has no content: %+v", st)
		}
	}
}

// (c) delete an index: startup rebuild regenerates the same spans
func TestRebuildRecoversDeletedIndex(t *testing.T) {
	dir := t.TempDir()
	s, _ := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 900})
	writeN(t, s, 30, "model.decision", 1_700_000_000_000)
	s.Close()

	segs, _ := filepath.Glob(filepath.Join(dir, "seg-*.jsonl"))
	if len(segs) == 0 {
		t.Skip("no plain segments to damage")
	}
	before, err := ReadIndex(segs[0])
	if err != nil {
		t.Fatal(err)
	}
	os.Remove(segs[0] + idxSuffix)

	s2, err := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 1 << 30})
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	_, _, rebuilt := s2.Stats()
	if rebuilt == 0 {
		t.Fatal("startup did not rebuild the missing sidecar")
	}
	after, err := ReadIndex(segs[0])
	if err != nil {
		t.Fatal(err)
	}
	if before.Records != after.Records || len(before.Topics) != len(after.Topics) {
		t.Fatalf("rebuilt index differs: %d/%d vs %d/%d", before.Records, len(before.Topics), after.Records, len(after.Topics))
	}
}

// (d) a corrupted index is treated as missing and rebuilt
func TestCorruptIndexIsRebuilt(t *testing.T) {
	dir := t.TempDir()
	s, _ := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 700})
	writeN(t, s, 20, "intel.signal", 1_700_000_000_000)
	s.Close()

	idx, _ := filepath.Glob(filepath.Join(dir, "*"+idxSuffix))
	if len(idx) == 0 {
		t.Skip("no sidecar to corrupt")
	}
	body, _ := os.ReadFile(idx[0])
	body[len(body)/2] ^= 0xFF
	os.WriteFile(idx[0], body, 0o644)
	if _, err := ReadIndex(strings.TrimSuffix(idx[0], idxSuffix)); err == nil {
		t.Fatal("checksum did not detect the corruption")
	}

	s2, _ := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 1 << 30})
	defer s2.Close()
	if _, err := ReadIndex(strings.TrimSuffix(idx[0], idxSuffix)); err != nil {
		t.Fatalf("corrupt sidecar was not rebuilt: %v", err)
	}
}

// (e) a leftover .tmp is resumed, not orphaned
func TestLeftoverTmpIsResumed(t *testing.T) {
	dir := t.TempDir()
	s, _ := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 1 << 30})
	writeN(t, s, 5, "market.price", 1_700_000_000_000)
	s.mu.Lock()
	hot := s.hot
	s.mu.Unlock()

	// simulate a crash after the one-second flush: the batch is on disk, the buffer
	// is empty, and the lock is released. A crash BEFORE the flush loses at most one
	// second, which is the durability window KIMI accepted and flagged to the owner.
	if err := s.Flush(); err != nil {
		t.Fatal(err)
	}
	s.mu.Lock()
	s.file.Close()
	s.file = nil
	s.mu.Unlock()
	syscall.Flock(int(s.lock.Fd()), syscall.LOCK_UN)

	s2, err := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 1 << 30})
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	s2.mu.Lock()
	got := s2.hot
	s2.mu.Unlock()
	if filepath.Base(got) != filepath.Base(hot) {
		t.Fatalf("resumed %s, want the leftover %s", got, hot)
	}
	s2.mu.Lock()
	info, _ := s2.file.Stat()
	s2.mu.Unlock()
	if info.Size() == 0 {
		t.Fatal("resumed file is empty; the earlier writes were lost")
	}
}

// (g) a one-hour window plans exactly one segment, and reading it returns the rows
func TestPlanOneHourOpensOneSegment(t *testing.T) {
	dir := t.TempDir()
	s, _ := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: 50 * time.Millisecond, Bytes: 1 << 30})
	base := int64(1_700_000_000_000)
	for i := 0; i < 60; i++ {
		writeN(t, s, 1, "market.depth", base+int64(i)*1000)
		if i%20 == 0 {
			time.Sleep(60 * time.Millisecond) // force an age rotation
		}
	}
	s.Close()

	plan, err := Plan(dir, "market.depth", base, base+59_000, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(plan) == 0 {
		t.Fatal("plan is empty")
	}
	// every planned segment must exist, with its index beside it
	for _, p := range plan {
		if _, err := os.Stat(p.Segment); err != nil {
			t.Fatalf("planned segment missing: %v", err)
		}
		if _, err := ReadIndex(p.Segment); err != nil {
			t.Fatalf("planned segment has no valid index: %v", err)
		}
	}
	n, err := readSegment(plan[len(plan)-1].Segment, base, base+59_000, nil, func(Record) error { return nil })
	if err != nil || n == 0 {
		t.Fatalf("reading the planned segment returned %d rows, err=%v", n, err)
	}
}

// (h) golden replay over a generated journal: zero mismatches
func TestGoldenReplayZeroMismatches(t *testing.T) {
	dir := t.TempDir()
	s, _ := OpenSealed(RetentionConfig{Dir: dir}, HourlyOrCeiling{Age: time.Hour, Bytes: 2000})
	const N = 300
	base := int64(1_700_000_000_000)
	for i := 0; i < N; i++ {
		body := []byte(`{"n":` + string(rune('0'+i%10)) + `}`)
		rec := Record{Topic: "model.fill", Key: "ETHUSDT", TS: base + int64(i),
			Payload: body, Envelope: []byte(`{"data":` + string(body) + `}`)}
		if err := s.Write(rec); err != nil {
			t.Fatal(err)
		}
	}
	s.Close()

	segs, _ := filepath.Glob(filepath.Join(dir, "seg-*.jsonl*"))
	got := 0
	for _, seg := range segs {
		if strings.HasSuffix(seg, idxSuffix) {
			continue
		}
		n, err := readSegment(seg, 0, 1<<62, nil, func(Record) error { return nil })
		if err != nil {
			t.Fatalf("replay %s: %v", seg, err)
		}
		got += n
	}
	if got != N {
		t.Fatalf("replay returned %d records, wrote %d", got, N)
	}
}
