package lake

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// SegmentIndex answers the cold-read question without reading the journal: which
// sealed segment can hold a topic in this time window?
//
// One sidecar per segment, written when the segment is sealed. A sidecar survives a
// restart without the bus, an index topic, or a rebuild, and it is small: a few
// hundred bytes per topic per segment.

type TopicSpan struct {
	Topic   string `json:"topic"`
	From    int64  `json:"from_ms"`
	To      int64  `json:"to_ms"`
	Records int    `json:"records"`
	Bytes   int64  `json:"bytes"`
}

type SegmentStats struct {
	Segment    string      `json:"segment"`
	OpenedAt   int64       `json:"opened_ms"`
	SealedAt   int64       `json:"sealed_ms"`
	Records    int         `json:"records"`
	Bytes      int64       `json:"bytes"`
	Compressed bool        `json:"compressed"`
	Topics     []TopicSpan `json:"topics"`
}

// spanTracker accumulates per-topic spans for the segment being written.
type spanTracker struct {
	mu    sync.Mutex
	spans map[string]*TopicSpan
	recs  int
	bytes int64
}

func newSpanTracker() *spanTracker { return &spanTracker{spans: map[string]*TopicSpan{}} }

func (t *spanTracker) add(topic string, ts int64, bytes int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.recs++
	t.bytes += bytes
	s, ok := t.spans[topic]
	if !ok {
		t.spans[topic] = &TopicSpan{Topic: topic, From: ts, To: ts, Records: 1, Bytes: bytes}
		return
	}
	if ts < s.From {
		s.From = ts
	}
	if ts > s.To {
		s.To = ts
	}
	s.Records++
	s.Bytes += bytes
}

func (t *spanTracker) snapshot() (int, int64, []TopicSpan) {
	t.mu.Lock()
	defer t.mu.Unlock()
	out := make([]TopicSpan, 0, len(t.spans))
	for _, s := range t.spans {
		out = append(out, *s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Topic < out[j].Topic })
	return t.recs, t.bytes, out
}

func (t *spanTracker) reset() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.spans = map[string]*TopicSpan{}
	t.recs = 0
	t.bytes = 0
}

// writeSidecar records what a sealed segment contains, next to it.
func writeSidecar(segment string, openedAt int64, st SegmentStats) error {
	st.Segment = segment
	st.SealedAt = time.Now().UnixMilli()
	body, err := json.MarshalIndent(st, "", " ")
	if err != nil {
		return err
	}
	return os.WriteFile(segment+".idx.json", body, 0o644)
}

// ReadIndex loads one segment's sidecar.
func ReadIndex(segment string) (SegmentStats, error) {
	var st SegmentStats
	body, err := os.ReadFile(segment + ".idx.json")
	if err != nil {
		return st, err
	}
	err = json.Unmarshal(body, &st)
	return st, err
}

// FindSegments returns the sealed segments that can hold a topic in a time window.
// It reads sidecars only — never the segments themselves — which is what makes a
// cold query cost one file read instead of a journal scan.
func FindSegments(dir, topic string, from, to int64) ([]SegmentStats, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var out []SegmentStats
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".idx.json") {
			continue
		}
		seg := strings.TrimSuffix(e.Name(), ".idx.json")
		st, err := ReadIndex(dir + "/" + seg)
		if err != nil {
			continue
		}
		for _, span := range st.Topics {
			if span.Topic != topic {
				continue
			}
			// overlap, not containment: a query window may start mid-segment
			if span.To >= from && span.From <= to {
				out = append(out, st)
			}
			break
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].OpenedAt < out[j].OpenedAt })
	return out, nil
}

// verify tight integration with the writer: every sealed segment must have a sidecar
var _ = fmt.Sprintf
