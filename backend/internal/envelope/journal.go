package envelope

// The recorder.
//
// One consumer writes packets to an append-only journal; nothing on the hot path
// waits for it. ATLAS's rule is that the recorder is a consumer, not a hook — the
// feed never blocks on a disk write because the write is somebody else's envelope.
//
// The journal is the thing that makes a backtest a replay rather than a different
// program: live and simulated run the same code against the same recorded packets,
// and the only difference between them is the clock.

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
)

// Journal is an append-only packet log, one JSON object per line.
type Journal struct {
	mu   sync.Mutex
	path string
	f    *os.File
}

// OpenJournal opens (or creates) a journal at path, appending to whatever is there.
func OpenJournal(path string) (*Journal, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, err
	}
	return &Journal{path: path, f: f}, nil
}

// Record appends a packet. It is safe for concurrent use and never rewrites.
func (j *Journal) Record(p Packet) error {
	if j == nil {
		return errors.New("journal: not open")
	}
	line, err := json.Marshal(p)
	if err != nil {
		return err
	}
	line = append(line, '\n')
	j.mu.Lock()
	defer j.mu.Unlock()
	_, err = j.f.Write(line)
	return err
}

// Close flushes and closes the journal.
func (j *Journal) Close() error {
	if j == nil {
		return nil
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.f.Close()
}

// Replay reads the journal in order and calls fn for every packet it accepts.
//
// filter may be nil. A filter that returns false skips the packet without stopping
// the replay, which is how "the same window" and "this symbol" are expressed.
func Replay(path string, filter func(Packet) bool, fn func(Packet)) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	// Packets carrying a sweep or a fan are larger than the default 64KB line.
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var p Packet
		if err := json.Unmarshal(line, &p); err != nil {
			// A corrupt line must not stop a replay: skip and continue.
			continue
		}
		if filter != nil && !filter(p) {
			continue
		}
		fn(p)
	}
	return sc.Err()
}

// Count returns how many packets the journal holds, optionally per topic.
func Count(path string) (map[string]int, error) {
	out := map[string]int{}
	err := Replay(path, nil, func(p Packet) {
		out[p.Meta.Topic]++
	})
	return out, err
}
