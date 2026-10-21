package lake

import (
	"encoding/json"
	"fmt"
	"hash/crc32"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// SegmentIndex answers the cold-read question without reading the journal: which
// sealed segment can hold a topic in this time window, and where in it to seek.
//
// One sidecar per sealed segment, written when the segment is sealed and updated
// atomically when it is compressed. The sidecar is a derived cache: the journal is
// the authority, and a missing or corrupt sidecar is rebuilt by scanning that one
// segment.

type TopicSpan struct {
	Topic      string `json:"topic"`
	From       int64  `json:"from_ms"`
	To         int64  `json:"to_ms"`
	Records    int    `json:"records"`
	Bytes      int64  `json:"bytes"`
	OffsetFrom int64  `json:"offset_from"`
	OffsetTo   int64  `json:"offset_to"`
	// Keys is populated only for topics with bounded cardinality that humans query
	// (model.*, intel.*). market.* would put 400 keys in every sidecar for no query.
	Keys []KeySpan `json:"keys,omitempty"`
}

type KeySpan struct {
	Key     string `json:"key"`
	From    int64  `json:"from_ms"`
	To      int64  `json:"to_ms"`
	Records int    `json:"records"`
}

type SegmentStats struct {
	V               int         `json:"v"`
	Segment         string      `json:"segment"`
	OpenedAt        int64       `json:"opened_ms"`
	SealedAt        int64       `json:"sealed_ms"`
	Records         int         `json:"records"`
	Bytes           int64       `json:"bytes"`
	CompressedBytes int64       `json:"compressed_bytes"`
	Codec           string      `json:"codec"`
	CRC             uint32      `json:"crc32"`
	Topics          []TopicSpan `json:"topics"`
	// Buckets is a sampled time-to-byte map for seeking. It is emitted only when
	// the segment's records are TS-monotonic in byte order; an out-of-order
	// segment has no buckets and readers fall back to a full scan.
	Buckets []TimeBucket `json:"buckets,omitempty"`
}

// TimeBucket maps a record timestamp to the byte offset of its first byte in the
// segment. Buckets are sorted by Offset (and by Ts, under the monotonicity
// invariant), so a reader binary-searches them to skip the front of a segment.
type TimeBucket struct {
	Ts     int64 `json:"ts_ms"`
	Offset int64 `json:"offset"`
}

// spanTracker accumulates per-topic and per-key spans for the segment being written.
type spanTracker struct {
	mu    sync.Mutex
	spans map[string]*TopicSpan
	keys  map[string]map[string]*KeySpan
	recs  int
	bytes int64
	off   int64
}

func newSpanTracker() *spanTracker {
	return &spanTracker{spans: map[string]*TopicSpan{}, keys: map[string]map[string]*KeySpan{}}
}

// keysWanted decides which topics carry key spans: the bounded, human-queried ones.
func keysWanted(topic string) bool {
	return strings.HasPrefix(topic, "model.") || strings.HasPrefix(topic, "intel.")
}

func (t *spanTracker) add(topic, key string, ts int64, nbytes int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.recs++
	t.bytes += nbytes
	t.off += nbytes
	s, ok := t.spans[topic]
	if !ok {
		s = &TopicSpan{Topic: topic, From: ts, To: ts, OffsetFrom: t.off - nbytes}
		t.spans[topic] = s
	}
	if ts < s.From {
		s.From = ts
	}
	if ts > s.To {
		s.To = ts
	}
	s.Records++
	s.Bytes += nbytes
	s.OffsetTo = t.off
	if keysWanted(topic) && key != "" {
		m, ok := t.keys[topic]
		if !ok {
			m = map[string]*KeySpan{}
			t.keys[topic] = m
		}
		k, ok := m[key]
		if !ok {
			k = &KeySpan{Key: key, From: ts, To: ts}
			m[key] = k
		}
		if ts < k.From {
			k.From = ts
		}
		if ts > k.To {
			k.To = ts
		}
		k.Records++
	}
}

func (t *spanTracker) snapshot() (int, int64, []TopicSpan) {
	t.mu.Lock()
	defer t.mu.Unlock()
	out := make([]TopicSpan, 0, len(t.spans))
	for topic, s := range t.spans {
		cp := *s
		if m, ok := t.keys[topic]; ok {
			cp.Keys = make([]KeySpan, 0, len(m))
			for _, k := range m {
				cp.Keys = append(cp.Keys, *k)
			}
			sort.Slice(cp.Keys, func(i, j int) bool { return cp.Keys[i].Key < cp.Keys[j].Key })
		}
		out = append(out, cp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Topic < out[j].Topic })
	return t.recs, t.bytes, out
}

func (t *spanTracker) reset() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.spans = map[string]*TopicSpan{}
	t.keys = map[string]map[string]*KeySpan{}
	t.recs, t.bytes, t.off = 0, 0, 0
}

// sidecarDoc is the on-disk form: the stats plus a checksum over them.
type sidecarDoc struct {
	SegmentStats
	CRC uint32 `json:"crc32"`
}

func indexCRC(st SegmentStats) uint32 {
	st.CRC = 0
	body, _ := json.Marshal(st)
	return crc32.ChecksumIEEE(body)
}

// writeSidecar writes the index atomically: .tmp, fsync, rename.
func writeSidecar(segment string, st SegmentStats) error {
	st.V = 1
	st.Segment = segment
	st.CRC = indexCRC(st)
	body, err := json.MarshalIndent(st, "", " ")
	if err != nil {
		return err
	}
	tmp := segment + idxSuffix + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := f.Write(body); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, segment+idxSuffix)
}

// ReadIndex loads and verifies one segment's sidecar. A checksum mismatch is an
// error, so callers rebuild instead of trusting a damaged cache.
func ReadIndex(segment string) (SegmentStats, error) {
	var d sidecarDoc
	body, err := os.ReadFile(segment + idxSuffix)
	if err != nil {
		return SegmentStats{}, err
	}
	if err := json.Unmarshal(body, &d); err != nil {
		return SegmentStats{}, err
	}
	st := d.SegmentStats
	want := st
	want.CRC = 0
	if crc32.ChecksumIEEE(mustJSON(want)) != d.CRC {
		return st, fmt.Errorf("sidecar checksum mismatch for %s", segment)
	}
	return st, nil
}

func mustJSON(v any) []byte { b, _ := json.Marshal(v); return b }

// SegmentPlan is what a cold query intends to open, and where.
type SegmentPlan struct {
	Segment    string `json:"segment"`
	From       int64  `json:"from_ms"`
	To         int64  `json:"to_ms"`
	OffsetFrom int64  `json:"offset_from"`
	OffsetTo   int64  `json:"offset_to"`
	Records    int    `json:"records"`
	Codec      string `json:"codec"`
}

// Plan is pure: given a topic, a window and optionally a key, it returns the sealed
// segments that can hold the answer, in time order, with the byte span to read.
func Plan(dir, topic string, from, to int64, key string) ([]SegmentPlan, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var out []SegmentPlan
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, idxSuffix) || strings.HasSuffix(name, ".tmp") {
			continue
		}
		seg := strings.TrimSuffix(name, idxSuffix)
		st, err := ReadIndex(strings.TrimSuffix(dir+"/"+name, idxSuffix))
		if err != nil {
			continue // damaged sidecar: rebuild will fix it, do not plan on it
		}
		for _, sp := range st.Topics {
			if sp.Topic != topic {
				continue
			}
			if sp.To < from || sp.From > to {
				break
			}
			if key != "" && len(sp.Keys) > 0 {
				found := false
				for _, k := range sp.Keys {
					if k.Key == key && k.To >= from && k.From <= to {
						found = true
						break
					}
				}
				if !found {
					break
				}
			}
			out = append(out, SegmentPlan{
				Segment: filepath.Join(dir, seg), From: sp.From, To: sp.To,
				OffsetFrom: sp.OffsetFrom, OffsetTo: sp.OffsetTo,
				Records: sp.Records, Codec: st.Codec,
			})
			break
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].From < out[j].From })
	return out, nil
}
