// Package store gives the platform three specialised stores behind one interface,
// while the journal stays the record.
//
//	journal (files)  the record: every envelope, append only, replayable
//	kv (bbolt)       latest by (topic, key): the hot "what is true now" view
//	sql (sqlite)     books and trades: queryable, joinable, survivable restarts
//	docs (mongo)     envelope objects: searchable documents, optional
//
// The interface is deliberately small. A store that cannot answer these four
// questions is not a store this platform needs.
package store

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// Envelope is the unit every store persists.
type Envelope struct {
	ID       string          `json:"id"`
	Topic    string          `json:"topic"`
	Key      string          `json:"key"`
	Plane    string          `json:"plane"`
	Producer string          `json:"producer"`
	TS       int64           `json:"ts"`
	Data     json.RawMessage `json:"data"`
}

// Store is what each backend implements.
type Store interface {
	Name() string
	Put(e Envelope) error
	Latest(topic, key string) (Envelope, bool, error)
	Get(id string) (Envelope, bool, error)
	Count() (int, error)
	Close() error
}

// NewID is stable for an envelope: topic, key and timestamp in milliseconds.
func NewID(topic, key string, ts int64) string {
	return fmt.Sprintf("%s|%s|%d", topic, key, ts)
}

// Multi fans a write out to every configured store. A failing backend is recorded
// and skipped: the record must not be lost because a cache is unhappy.
type Multi struct {
	stores []Store
	mu     sync.Mutex
	fails  map[string]int
}

func NewMulti(stores ...Store) *Multi { return &Multi{stores: stores, fails: map[string]int{}} }

func (m *Multi) Put(e Envelope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	var firstErr error
	for _, s := range m.stores {
		if err := s.Put(e); err != nil {
			m.fails[s.Name()]++
			if firstErr == nil {
				firstErr = fmt.Errorf("%s: %w", s.Name(), err)
			}
		}
	}
	return firstErr
}

func (m *Multi) Failures() map[string]int {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make(map[string]int, len(m.fails))
	for k, v := range m.fails {
		out[k] = v
	}
	return out
}

func (m *Multi) Close() error {
	for _, s := range m.stores {
		_ = s.Close()
	}
	return nil
}

// ---------------------------------------------------------------- journal
// Journal is the record: one JSON line per envelope, appended, never rewritten.

type Journal struct {
	mu   sync.Mutex
	path string
	file *os.File
	n    int
}

func OpenJournal(path string) (*Journal, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, err
	}
	return &Journal{path: path, file: f}, nil
}

func (j *Journal) Name() string { return "journal" }

func (j *Journal) Put(e Envelope) error {
	line, err := json.Marshal(e)
	if err != nil {
		return err
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	if _, err := j.file.Write(append(line, '\n')); err != nil {
		return err
	}
	j.n++
	return nil
}

// Latest and Get are answered from the journal by scanning backwards; the
// specialised stores exist precisely so this is rarely needed.
func (j *Journal) Latest(topic, key string) (Envelope, bool, error) {
	all, err := j.readAll()
	if err != nil {
		return Envelope{}, false, err
	}
	for i := len(all) - 1; i >= 0; i-- {
		if all[i].Topic == topic && (key == "" || all[i].Key == key) {
			return all[i], true, nil
		}
	}
	return Envelope{}, false, nil
}

func (j *Journal) Get(id string) (Envelope, bool, error) {
	all, err := j.readAll()
	if err != nil {
		return Envelope{}, false, err
	}
	for _, e := range all {
		if e.ID == id {
			return e, true, nil
		}
	}
	return Envelope{}, false, nil
}

func (j *Journal) Count() (int, error) { return j.n, nil }

func (j *Journal) Close() error { return j.file.Close() }

func (j *Journal) readAll() ([]Envelope, error) {
	data, err := os.ReadFile(j.path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	var out []Envelope
	for _, line := range splitLines(data) {
		var e Envelope
		if json.Unmarshal(line, &e) == nil {
			out = append(out, e)
		}
	}
	sort.Slice(out, func(a, b int) bool { return out[a].TS < out[b].TS })
	return out, nil
}

func splitLines(data []byte) [][]byte {
	var out [][]byte
	start := 0
	for i, b := range data {
		if b == '\n' {
			if i > start {
				out = append(out, data[start:i])
			}
			start = i + 1
		}
	}
	if start < len(data) {
		out = append(out, data[start:])
	}
	return out
}

// Now is injectable so tests never depend on the wall clock.
var Now = func() time.Time { return time.Now() }
