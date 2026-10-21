// Retention: a journal that rotates, compresses what it closed, and deletes what
// has aged out. Recording everything is only affordable if what is recorded has
// a lifetime, so the policy lives with the storage rather than in a cron job.
package lake

import (
	"compress/gzip"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// RetentionConfig is per-tier policy.
type RetentionConfig struct {
	Dir         string
	SegmentSize int64         // rotate at this size
	MaxAge      time.Duration // delete segments older than this
	Compress    bool
}

// Segmented is a rotating journal with compression and expiry.
type Segmented struct {
	cfg        RetentionConfig
	mu         sync.Mutex
	file       *os.File
	size       int64
	segments   []string
	writes     int64
	rotations  int64
	compressed int64
	deleted    int64
	stop       chan struct{}
	once       sync.Once
	tracker    *spanTracker
	openedAt   int64
}

func OpenSegmented(cfg RetentionConfig) (*Segmented, error) {
	if cfg.SegmentSize <= 0 {
		cfg.SegmentSize = 64 << 20
	}
	if err := os.MkdirAll(cfg.Dir, 0o755); err != nil {
		return nil, err
	}
	s := &Segmented{cfg: cfg, stop: make(chan struct{}), tracker: newSpanTracker()}
	if err := s.rotate(); err != nil {
		return nil, err
	}
	go s.expireLoop()
	return s, nil
}

func (s *Segmented) Name() string { return "segmented-journal" }

func (s *Segmented) Write(r Record) error {
	line, err := marshalRecord(r)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.size+int64(len(line))+1 > s.cfg.SegmentSize {
		if err := s.rotateLocked(); err != nil {
			return err
		}
	}
	if _, err := s.file.Write(append(line, '\n')); err != nil {
		return err
	}
	s.size += int64(len(line)) + 1
	s.tracker.add(r.Topic, r.Key, r.TS, int64(len(line))+1)
	atomic.AddInt64(&s.writes, 1)
	return nil
}

func (s *Segmented) rotate() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.rotateLocked()
}

func (s *Segmented) rotateLocked() error {
	if s.file != nil {
		// seal: write the sidecar before closing, so a reader never meets a
		// segment without its index
		if recs, bytes, spans := s.tracker.snapshot(); recs > 0 {
			_ = writeSidecar(s.segments[len(s.segments)-1],
				SegmentStats{OpenedAt: s.openedAt, SealedAt: time.Now().UnixMilli(), Records: recs, Bytes: bytes, Topics: spans})
		}
		_ = s.file.Close()
	}
	s.tracker.reset()
	s.openedAt = time.Now().UnixMilli()
	name := filepath.Join(s.cfg.Dir, fmt.Sprintf("seg-%d.jsonl", time.Now().UnixNano()))
	f, err := os.OpenFile(name, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	s.file = f
	s.size = 0
	s.segments = append(s.segments, name)
	if len(s.segments) > 1 {
		atomic.AddInt64(&s.rotations, 1)
	}
	return nil
}

// expireLoop compresses closed segments and deletes the ones past their lifetime.
func (s *Segmented) expireLoop() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-ticker.C:
			s.ExpireOnce()
		}
	}
}

// ExpireOnce is exported so tests and operators can force a sweep.
func (s *Segmented) ExpireOnce() {
	s.mu.Lock()
	var current string
	if len(s.segments) > 0 {
		current = s.segments[len(s.segments)-1]
	}
	segs := append([]string(nil), s.segments...)
	s.mu.Unlock()

	now := time.Now()
	keep := make([]string, 0, len(segs))
	for _, seg := range segs {
		info, err := os.Stat(seg)
		if err != nil {
			continue
		}
		// never compress or delete the segment being written
		if seg == current {
			keep = append(keep, seg)
			continue
		}
		if now.Sub(info.ModTime()) > s.cfg.MaxAge {
			if err := os.Remove(seg); err == nil {
				atomic.AddInt64(&s.deleted, 1)
			}
			continue
		}
		if s.cfg.Compress && !strings.HasSuffix(seg, ".gz") {
			if err := compressFile(seg); err == nil {
				atomic.AddInt64(&s.compressed, 1)
				seg = seg + ".gz"
			}
		}
		keep = append(keep, seg)
	}
	s.mu.Lock()
	s.segments = keep
	s.mu.Unlock()
}

func compressFile(path string) error {
	in, err := os.Open(path)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(path + ".gz")
	if err != nil {
		return err
	}
	gz := gzip.NewWriter(out)
	if _, err := copyAll(gz, in); err != nil {
		gz.Close()
		out.Close()
		os.Remove(path + ".gz")
		return err
	}
	if err := gz.Close(); err != nil {
		out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	return os.Remove(path)
}

func (s *Segmented) Stats() (writes, rotations, compressed, deleted int64, segments int) {
	s.mu.Lock()
	n := len(s.segments)
	s.mu.Unlock()
	return atomic.LoadInt64(&s.writes), atomic.LoadInt64(&s.rotations),
		atomic.LoadInt64(&s.compressed), atomic.LoadInt64(&s.deleted), n
}

func (s *Segmented) Close() error {
	var err error
	s.once.Do(func() {
		close(s.stop)
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.file != nil {
			// the last segment is sealed like any other: a reader must never meet
			// a segment without its index
			if recs, bytes, spans := s.tracker.snapshot(); recs > 0 && len(s.segments) > 0 {
				_ = writeSidecar(s.segments[len(s.segments)-1],
					SegmentStats{OpenedAt: s.openedAt, SealedAt: time.Now().UnixMilli(), Records: recs, Bytes: bytes, Topics: spans})
			}
			err = s.file.Close()
		}
	})
	return err
}

// ReplaySegments reads every segment in order, including compressed ones.
func ReplaySegments(dir string, fn func(Record) error) (int, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0, err
	}
	files := make([]string, 0, len(entries))
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "seg-") {
			files = append(files, filepath.Join(dir, e.Name()))
		}
	}
	sort.Strings(files)
	total := 0
	for _, f := range files {
		n, err := replayFile(f, fn)
		total += n
		if err != nil {
			return total, err
		}
	}
	return total, nil
}
