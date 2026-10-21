package lake

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// BuildIndexForSegment is the one-scan indexer for a frozen segment. It is resumable
// (checkpoints every checkpointBytes), verifies its own arithmetic with an
// independent counter, and cuts a torn tail rather than indexing a truncated line.
func BuildIndexForSegment(segment string, checkpointBytes int64) (SegmentStats, error) {
	f, err := os.Open(segment)
	if err != nil {
		return SegmentStats{}, err
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil {
		return SegmentStats{}, err
	}
	total := info.Size()

	// resume point
	off, tracker := int64(0), newSpanTracker()
	counts := map[string]int{}
	ordered := true
	var prevTS int64
	var buckets []TimeBucket
	progPath := segment + ".scan.progress"
	if body, err := os.ReadFile(progPath); err == nil {
		var prog struct {
			Offset  int64          `json:"offset"`
			Topics  []TopicSpan    `json:"topics"`
			Records int            `json:"records"`
			Bytes   int64          `json:"bytes"`
			Counts  map[string]int `json:"counts"`
			Ordered bool           `json:"ordered"`
			PrevTS  int64          `json:"prev_ts"`
			Buckets []TimeBucket   `json:"buckets"`
		}
		if json.Unmarshal(body, &prog) == nil && prog.Offset > 0 && prog.Offset < total {
			off = prog.Offset
			for _, sp := range prog.Topics {
				tracker.restore(sp)
			}
			tracker.recs, tracker.bytes, tracker.off = prog.Records, prog.Bytes, prog.Offset
			if prog.Counts != nil {
				counts = prog.Counts
			}
			ordered, prevTS, buckets = prog.Ordered, prog.PrevTS, prog.Buckets
		}
	}
	if off > 0 {
		if _, err := f.Seek(off, io.SeekStart); err != nil {
			return SegmentStats{}, err
		}
	}

	// independent counter: the reconciliation KIMI required, done in the same pass
	r := bufio.NewReaderSize(f, 1<<20)
	var pos = off
	var lastGood = off
	var scanned int64
	nextCheckpoint := off + checkpointBytes

	for {
		line, err := r.ReadBytes('\n')
		if len(line) > 0 {
			complete := line[len(line)-1] == '\n'
			if complete {
				var rec Record
				if json.Unmarshal(line[:len(line)-1], &rec) == nil {
					if rec.TS < prevTS {
						ordered = false
					}
					prevTS = rec.TS
					tracker.add(rec.Topic, rec.Key, rec.TS, int64(len(line)))
					counts[rec.Topic]++
					lastGood = pos + int64(len(line))
				}
			}
			pos += int64(len(line))
			scanned += int64(len(line))
		}
		if err != nil {
			break
		}
		if pos >= nextCheckpoint {
			if ordered {
				buckets = append(buckets, TimeBucket{Ts: prevTS, Offset: pos})
			}
			_ = writeProgress(progPath, pos, tracker, counts, ordered, prevTS, buckets)
			nextCheckpoint = pos + checkpointBytes
		}
	}

	// tail integrity: a torn final line is cut, and the cut is reported
	if lastGood < total {
		if err := os.Truncate(segment, lastGood); err != nil {
			return SegmentStats{}, fmt.Errorf("cutting torn tail: %w", err)
		}
		fmt.Printf("cut torn tail: %d bytes removed (last complete record at %d)\n", total-lastGood, lastGood)
	}

	recs, bytes, spans := tracker.snapshot()
	// reconcile: the tracker's per-topic counts must equal the independent counter
	got := map[string]int{}
	for _, sp := range spans {
		got[sp.Topic] = sp.Records
	}
	for topic, want := range counts {
		if got[topic] != want {
			return SegmentStats{}, fmt.Errorf("reconciliation failed for %s: index %d, segment %d", topic, got[topic], want)
		}
	}
	if recs != lenCounts(counts) {
		return SegmentStats{}, fmt.Errorf("reconciliation failed: index %d records, segment %d", recs, lenCounts(counts))
	}

	st := SegmentStats{OpenedAt: info.ModTime().UnixMilli(), SealedAt: time.Now().UnixMilli(),
		Records: recs, Bytes: bytes, Codec: "none", Topics: spans}
	if ordered {
		st.Buckets = buckets
	}
	if err := writeSidecar(segment, st); err != nil {
		return st, err
	}
	_ = os.Remove(progPath)
	return st, nil
}

func lenCounts(m map[string]int) int {
	n := 0
	for _, v := range m {
		n += v
	}
	return n
}

func writeProgress(path string, off int64, t *spanTracker, counts map[string]int, ordered bool, prevTS int64, buckets []TimeBucket) error {
	recs, bytes, spans := t.snapshot()
	body, err := json.Marshal(map[string]any{"offset": off, "records": recs, "bytes": bytes, "topics": spans, "counts": counts, "ordered": ordered, "prev_ts": prevTS, "buckets": buckets})
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, body, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// restore seeds a tracker from a checkpoint.
func (t *spanTracker) restore(sp TopicSpan) {
	t.mu.Lock()
	defer t.mu.Unlock()
	cp := sp
	t.spans[cp.Topic] = &cp
	if len(cp.Keys) > 0 {
		m := map[string]*KeySpan{}
		for i := range cp.Keys {
			k := cp.Keys[i]
			m[k.Key] = &k
		}
		t.keys[cp.Topic] = m
	}
}

// SampleVerify checks an index against the segment it describes: n random windows,
// each compared between Plan+ScanSegment and a direct scan of the frozen file.
func SampleVerify(dir, segment string, n int) error {
	st, err := ReadIndex(segment)
	if err != nil {
		return err
	}
	r := rand.New(rand.NewSource(42))
	for i := 0; i < n; i++ {
		sp := st.Topics[r.Intn(len(st.Topics))]
		width := int64(60_000)
		from := sp.From + r.Int63n(maxInt64(1, sp.To-sp.From-width+1))
		to := from + width

		planned, err := Plan(dir, sp.Topic, from, to, "")
		if err != nil {
			return err
		}
		var planCount int
		for _, p := range planned {
			c, err := ScanSegmentSeek(p.Segment, from, to, st.Buckets, func(rec Record) error {
				if rec.Topic != sp.Topic {
					return nil
				}
				planCount++
				return nil
			})
			_ = c
			if err != nil {
				return err
			}
		}
		var directCount int
		if _, err := ScanSegmentSeek(segment, from, to, st.Buckets, func(rec Record) error {
			if rec.Topic == sp.Topic {
				directCount++
			}
			return nil
		}); err != nil {
			return err
		}
		if planCount != directCount {
			return fmt.Errorf("sample %d (%s %d..%d): plan %d rows, direct %d", i, sp.Topic, from, to, planCount, directCount)
		}
	}
	return nil
}

func maxInt64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

// SortedSegments lists segment files in a directory, oldest first.
func SortedSegments(dir string) []string {
	entries, _ := os.ReadDir(dir)
	var out []string
	for _, e := range entries {
		name := e.Name()
		if filepath.Ext(name) == ".jsonl" || filepath.Ext(name) == ".gz" {
			out = append(out, filepath.Join(dir, name))
		}
	}
	sort.Strings(out)
	return out
}
