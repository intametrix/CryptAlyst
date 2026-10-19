package turtle

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Durable session history.
//
// The human's instruction: "Never forget any data as we can learn from this and
// then adjust or introduce new models and signals." That makes this file the
// point of the whole feature rather than a nice-to-have, because a strategy that
// cannot be replayed cannot be improved -- only believed.
//
// WHAT IS RECORDED, and the reasoning for each, because a log that only keeps
// wins is a marketing document:
//
//	session_start   with the FULL config, so a change in behaviour can be
//	                attributed to a rule change rather than guessed at
//	session_stop    with the final equity
//	fill            an entry that executed, with N, units, stop and the alert
//	reject          an entry that did NOT execute, WITH ITS REASON -- the
//	                volatility vetoes, the off-watchlist blocks, the missing
//	                breakouts. These are the rows that teach: a strategy is
//	                improved by understanding what it declined to do
//	add_unit        a pyramid, with the stop move it caused
//	close           a completed round trip with the reason it closed
//	watchlist       what the session decided to trade at start, with scores
//	                and reasons, so selection can be evaluated separately from
//	                entry timing
//	mark            periodic equity, for a PnL curve
//
// APPEND-ONLY, with the same discipline as the alert ledger: the file is opened
// for append, one JSON object per line, flushed per record, and a malformed line
// is counted rather than skipped silently. Rotation is deliberately NOT
// implemented -- losing history to save disk is the opposite of the instruction.
type Store struct {
	mu      sync.Mutex
	path    string
	file    *os.File
	written int
	failed  int
}

// NewStore opens or creates the append-only session log. An empty path disables
// persistence and returns a nil-safe no-op store, so a test or a throwaway run
// does not need a filesystem.
func NewStore(path string) (*Store, error) {
	if path == "" {
		return &Store{}, nil
	}
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("turtle store dir: %w", err)
		}
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, fmt.Errorf("turtle store open: %w", err)
	}
	return &Store{path: path, file: f}, nil
}

// Record appends one event. A write failure is counted and reported, never
// swallowed: a session that silently stopped recording would look identical to
// one that had nothing to record, and the whole instruction is that nothing is
// forgotten.
func (s *Store) Record(kind string, payload any) error {
	if s == nil || s.file == nil {
		return nil
	}
	rec := map[string]any{
		"kind":  kind,
		"ts_ms": time.Now().UnixMilli(),
	}
	// Flatten the payload so every record is queryable with jq rather than
	// needing a schema-aware reader.
	if b, err := json.Marshal(payload); err == nil {
		var m map[string]any
		if json.Unmarshal(b, &m) == nil {
			// The payload's own "kind" must not override the event kind.
			delete(m, "kind")
			for k, v := range m {
				rec[k] = v
			}
		}
	}

	line, err := json.Marshal(rec)
	if err != nil {
		s.mu.Lock()
		s.failed++
		s.mu.Unlock()
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := s.file.Write(append(line, '\n')); err != nil {
		s.failed++
		return err
	}
	s.written++
	return nil
}

// Stats reports what the store has done, so a dashboard can show that recording
// is ALIVE rather than assume it.
func (s *Store) Stats() map[string]any {
	if s == nil {
		return map[string]any{"enabled": false}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return map[string]any{
		"enabled": s.path != "",
		"path":    s.path,
		"written": s.written,
		"failed":  s.failed,
	}
}

// Close flushes and closes the log.
func (s *Store) Close() error {
	if s == nil || s.file == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.file.Close()
}

// ReadSessionLog reads back the append-only log.
//
// Malformed lines are COUNTED and returned rather than dropped in silence, for
// the same reason the alert reader counts them: a truncated tail is a real
// condition (a crash mid-write) and a reader that hides it makes the history look
// complete when it is not.
func ReadSessionLog(path string, limit int) ([]map[string]any, int, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()

	var out []map[string]any
	malformed := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal(line, &m); err != nil {
			malformed++
			continue
		}
		out = append(out, m)
	}
	if err := sc.Err(); err != nil {
		return out, malformed, err
	}
	// Newest first, capped. The cap is applied AFTER reading so the malformed
	// count describes the whole file rather than the returned slice.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, malformed, nil
}
