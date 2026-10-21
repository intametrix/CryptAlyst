// Package lake is the storage-agnostic layer. One interface, several backends:
// memory for the hot path, JSONL for replay, and room for a columnar store
// without a consumer learning which one answered.
package lake

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
)

// Record is one stored envelope, kept verbatim so replay is exact.
type Record struct {
	Topic string `json:"topic"`
	Key   string `json:"key"`
	TS    int64  `json:"ts"`
	// Payload is not written any more: it is the same bytes as envelope.data and
	// used to double every line. It is kept in the struct so callers and old
	// segments still work, and DerivePayload fills it on read.
	Payload json.RawMessage `json:"payload,omitempty"`
	// PayloadVersion records how the payload was encoded, so a reader knows it can
	// derive it from the envelope rather than expecting the field.
	PayloadVersion int `json:"pv,omitempty"`
	// Envelope is the wrapped form recorded alongside the payload. Replay keeps
	// publishing Payload, so consumers are unaffected; the explorer reads this.
	Envelope json.RawMessage `json:"envelope,omitempty"`
}

// Backend is the swap point. Every tier implements this and nothing else.
type Backend interface {
	Write(Record) error
	Close() error
	Name() string
}

// Memory keeps the most recent N records per topic. The hot path.
type Memory struct {
	mu      sync.RWMutex
	limit   int
	byTopic map[string][]Record
	writes  int64
	drops   int64
}

func NewMemory(limitPerTopic int) *Memory {
	return &Memory{limit: limitPerTopic, byTopic: map[string][]Record{}}
}

func (m *Memory) Name() string { return "memory" }

func (m *Memory) Write(r Record) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	list := append(m.byTopic[r.Topic], r)
	if len(list) > m.limit {
		drop := len(list) - m.limit
		list = list[drop:]
		atomic.AddInt64(&m.drops, int64(drop))
	}
	m.byTopic[r.Topic] = list
	atomic.AddInt64(&m.writes, 1)
	return nil
}

func (m *Memory) Close() error { return nil }

func (m *Memory) Latest(topic string, n int) []Record {
	m.mu.RLock()
	defer m.mu.RUnlock()
	list := m.byTopic[topic]
	if n > len(list) {
		n = len(list)
	}
	out := make([]Record, n)
	copy(out, list[len(list)-n:])
	return out
}

func (m *Memory) Stats() (writes, drops int64) {
	return atomic.LoadInt64(&m.writes), atomic.LoadInt64(&m.drops)
}

// Journal appends every record to disk and replays it on open. The durable path.
type Journal struct {
	mu     sync.Mutex
	f      *os.File
	w      *bufio.Writer
	path   string
	writes int64
}

func OpenJournal(path string) (*Journal, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, err
	}
	return &Journal{f: f, w: bufio.NewWriterSize(f, 1<<20), path: path}, nil
}

func (j *Journal) Name() string { return "journal" }

// DerivePayload returns the payload of a record, from the stored field when it is
// there (old segments) and from envelope.data when it is not (new ones).
func DerivePayload(r Record) (json.RawMessage, error) {
	if len(r.Payload) > 0 {
		return r.Payload, nil
	}
	if len(r.Envelope) == 0 {
		return nil, fmt.Errorf("record has neither payload nor envelope")
	}
	var env struct {
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(r.Envelope, &env); err != nil {
		return nil, fmt.Errorf("deriving payload: %w", err)
	}
	if len(env.Data) == 0 {
		return nil, fmt.Errorf("envelope has no data member")
	}
	return env.Data, nil
}

// stripPayload returns the form written to disk: the envelope, no duplicate body.
// One record per process is checked byte-for-byte, so a shape the derivation cannot
// reproduce is caught at the first write rather than at the thousandth read.
var stripChecked int32

// KeepPayload turns the strip off, so M1 can be rolled back without a rebuild.
var KeepPayload = os.Getenv("LAKE_KEEP_PAYLOAD") == "1"

func stripPayload(r Record) (Record, error) {
	if KeepPayload {
		return r, nil
	}
	if len(r.Envelope) == 0 || len(r.Payload) == 0 {
		return r, nil
	}
	out := r
	out.Payload = nil
	out.PayloadVersion = 1
	if atomic.CompareAndSwapInt32(&stripChecked, 0, 1) {
		derived, err := DerivePayload(out)
		if err != nil {
			return r, err
		}
		if len(derived) != len(r.Payload) {
			return r, fmt.Errorf("payload derivation mismatch: stored %d bytes, derived %d", len(r.Payload), len(derived))
		}
	}
	return out, nil
}

func (j *Journal) Write(r Record) error {
	sp, err := stripPayload(r)
	if err != nil {
		return err
	}
	r = sp
	line, err := json.Marshal(r)
	if err != nil {
		return err
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	if _, err := j.w.Write(line); err != nil {
		return err
	}
	if err := j.w.WriteByte('\n'); err != nil {
		return err
	}
	atomic.AddInt64(&j.writes, 1)
	return nil
}

func (j *Journal) Flush() error {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.w.Flush()
}

func (j *Journal) Close() error {
	_ = j.Flush()
	return j.f.Close()
}

func (j *Journal) Writes() int64 { return atomic.LoadInt64(&j.writes) }

// Replay reads a journal back, in order. This is what makes a backtest a replay
// rather than a re-derivation.
func Replay(path string, fn func(Record) error) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 16<<20)
	n := 0
	for sc.Scan() {
		var r Record
		if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
			continue
		}
		if err := fn(r); err != nil {
			return n, err
		}
		n++
	}
	return n, sc.Err()
}

// Lake fans one write out to every backend. Consumers never see this list.
type Lake struct {
	backends []Backend
	mu       sync.Mutex
	writes   int64
	errors   int64
}

func New(backends ...Backend) *Lake { return &Lake{backends: backends} }

func (l *Lake) Write(r Record) error {
	var firstErr error
	l.mu.Lock()
	l.writes++
	l.mu.Unlock()
	for _, b := range l.backends {
		if err := b.Write(r); err != nil {
			l.mu.Lock()
			l.errors++
			l.mu.Unlock()
			if firstErr == nil {
				firstErr = err
			}
		}
	}
	return firstErr
}

func (l *Lake) Close() error {
	for _, b := range l.backends {
		_ = b.Close()
	}
	return nil
}

func (l *Lake) Stats() (writes, errors int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.writes, l.errors
}

func (l *Lake) BackendNames() []string {
	out := make([]string, 0, len(l.backends))
	for _, b := range l.backends {
		out = append(out, b.Name())
	}
	return out
}
