package lake

import (
	"bufio"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// The sidecar is a derived cache, not the authority: the journal is. If a sidecar is
// missing or fails its checksum, it is rebuilt by scanning that one segment — once,
// on startup — and never by touching any other segment.

// checksum covers everything in the record except the checksum itself.
type indexDoc struct {
	SegmentStats
	CRC uint32 `json:"crc32"`
}

func indexChecksum(st SegmentStats) uint32 {
	body, _ := json.Marshal(st)
	return crc32.ChecksumIEEE(body)
}

// RebuildIndex scans one segment and produces its stats. It handles both a plain
// segment and a gzipped one, so rebuild works after sealing.
func RebuildIndex(segment string) (SegmentStats, error) {
	var reader io.Reader
	f, err := os.Open(segment)
	if err != nil {
		return SegmentStats{}, err
	}
	defer f.Close()
	if strings.HasSuffix(segment, ".gz") {
		gz, err := gzip.NewReader(f)
		if err != nil {
			return SegmentStats{}, err
		}
		defer gz.Close()
		reader = gz
	} else {
		reader = f
	}

	st := SegmentStats{Segment: segment}
	if info, err := f.Stat(); err == nil {
		st.OpenedAt = info.ModTime().UnixMilli()
	}
	spans := map[string]*TopicSpan{}
	sc := bufio.NewScanner(reader)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		line := sc.Bytes()
		var rec Record
		if json.Unmarshal(line, &rec) != nil {
			continue
		}
		st.Records++
		st.Bytes += int64(len(line)) + 1
		off := st.Bytes - int64(len(line)) - 1
		sp, ok := spans[rec.Topic]
		if !ok {
			sp = &TopicSpan{Topic: rec.Topic, From: rec.TS, To: rec.TS}
			spans[rec.Topic] = sp
		}
		if rec.TS < sp.From {
			sp.From = rec.TS
		}
		if rec.TS > sp.To {
			sp.To = rec.TS
		}
		sp.Records++
		sp.Bytes += int64(len(line)) + 1
		if off < sp.OffsetFrom || sp.Records == 1 {
			sp.OffsetFrom = off
		}
		sp.OffsetTo = off + int64(len(line)) + 1
		if keysWanted(rec.Topic) && rec.Key != "" {
			var found bool
			for i := range sp.Keys {
				if sp.Keys[i].Key == rec.Key {
					found = true
					if rec.TS < sp.Keys[i].From {
						sp.Keys[i].From = rec.TS
					}
					if rec.TS > sp.Keys[i].To {
						sp.Keys[i].To = rec.TS
					}
					sp.Keys[i].Records++
					break
				}
			}
			if !found {
				sp.Keys = append(sp.Keys, KeySpan{Key: rec.Key, From: rec.TS, To: rec.TS, Records: 1})
			}
		}
	}
	if err := sc.Err(); err != nil {
		return st, err
	}
	for _, sp := range spans {
		st.Topics = append(st.Topics, *sp)
	}
	st.SealedAt = time.Now().UnixMilli()
	return st, nil
}

// rebuildSealed repairs every sealed segment whose sidecar is absent or corrupt.
// It is the startup heal: one scan per damaged segment, bounded, idempotent.
func rebuildSealed(dir string) (int, error) { return RebuildMissing(dir) }

// RebuildMissing walks a directory and repairs every sidecar that is absent or does
// not match its checksum. Returns how many it rebuilt.
func RebuildMissing(dir string) (int, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0, err
	}
	rebuilt := 0
	// invariant: exactly one sidecar per segment, named after the file it describes.
	// A plain-named sidecar beside a gzipped segment is stale, and is removed here.
	seen := map[string]bool{}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), idxSuffix) {
			seg := strings.TrimSuffix(filepath.Join(dir, e.Name()), idxSuffix)
			if _, err := os.Stat(seg); err != nil {
				// the segment this describes is gone; the sidecar is a leftover
				_ = os.Remove(filepath.Join(dir, e.Name()))
			}
			seen[seg] = true
		}
	}
	for _, e := range entries {
		name := e.Name()
		if !strings.HasPrefix(name, "seg-") || strings.HasSuffix(name, idxSuffix) {
			continue
		}
		seg := filepath.Join(dir, name)
		if _, err := ReadIndex(seg); err == nil {
			continue // present and checksum-clean
		}
		st, err := RebuildIndex(seg)
		if err != nil {
			return rebuilt, fmt.Errorf("rebuild %s: %w", name, err)
		}
		if err := writeSidecar(seg, st); err != nil {
			return rebuilt, err
		}
		rebuilt++
	}
	return rebuilt, nil
}
