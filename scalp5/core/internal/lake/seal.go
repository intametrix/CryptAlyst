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
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// Rotation policy is a strategy, not a branch in the writer.
type RotationPolicy interface {
	ShouldRotate(age time.Duration, bytes int64) bool
}

// HourlyOrCeiling rotates on whichever comes first: one hour of ingest, or the size
// ceiling. Hourly means a one-hour query opens exactly one segment.
type HourlyOrCeiling struct {
	Age   time.Duration
	Bytes int64
}

func (p HourlyOrCeiling) ShouldRotate(age time.Duration, bytes int64) bool {
	return age >= p.Age || bytes >= p.Bytes
}

// Sealed is the rotating journal: a hot .jsonl.tmp that becomes an immutable,
// indexed, compressed segment when it seals.
type Sealed struct {
	cfg    RetentionConfig
	policy RotationPolicy
	dir    string

	mu      sync.Mutex
	file    *os.File
	bw      *bufio.Writer
	hot     string // seg-...jsonl.tmp
	size    int64
	seq     int
	opened  time.Time
	tracker *spanTracker

	lock     *os.File
	compress chan string
	stop     chan struct{}
	once     sync.Once
	wg       sync.WaitGroup

	sealed      int64
	failed      int64
	rebuild     int64
	fsyncErrors int64
	shortWrites int64
	dirty       int32
}

const (
	idxSuffix      = ".idx.json"
	tmpSuffix      = ".tmp"
	flushBuffer    = 256 << 10 // bufio size: the syscall lands once per buffer, not per 64 KB
	flushThreshold = 256 << 10 // flush early above this much buffered
)

func nowMillis() int64 { return time.Now().UnixMilli() }

// OpenSealed takes an exclusive lock on the directory (one writer), resumes a
// leftover .tmp as the hot segment, and repairs any sidecar that is missing or
// checksum-bad by scanning that segment once.
func OpenSealed(cfg RetentionConfig, policy RotationPolicy) (*Sealed, error) {
	if err := os.MkdirAll(cfg.Dir, 0o755); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(cfg.Dir, ".writer.lock"), os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		return nil, fmt.Errorf("another writer holds %s: %w", cfg.Dir, err)
	}
	s := &Sealed{cfg: cfg, policy: policy, dir: cfg.Dir, tracker: newSpanTracker(),
		lock: lock, compress: make(chan string, 8), stop: make(chan struct{})}

	n, err := rebuildSealed(cfg.Dir)
	s.rebuild = int64(n)
	if err != nil {
		lock.Close()
		return nil, err
	}

	// resume a leftover hot file, or open a new one
	hots, _ := filepath.Glob(filepath.Join(cfg.Dir, "seg-*"+tmpSuffix))
	sort.Strings(hots)
	if len(hots) > 0 {
		hot := hots[len(hots)-1]
		f, err := os.OpenFile(hot, os.O_WRONLY|os.O_APPEND, 0o644)
		if err != nil {
			lock.Close()
			return nil, err
		}
		info, _ := f.Stat()
		// s.hot must be the BARE filename (openHotLocked sets it bare, and
		// sealLocked joins s.dir + s.hot). Storing the full glob path here would
		// double the directory on the rename and fail the rotation.
		s.file, s.bw, s.hot, s.size, s.opened = f, bufio.NewWriterSize(f, flushBuffer), filepath.Base(hot), info.Size(), time.Now()
	} else if err := s.openHotLocked(); err != nil {
		lock.Close()
		return nil, err
	}
	s.wg.Add(1)
	go s.compressLoop()
	return s, nil
}

func (s *Sealed) Name() string { return "sealed-journal" }

func (s *Sealed) openHotLocked() error {
	name := fmt.Sprintf("seg-%s-%04d.jsonl%s", time.Now().UTC().Format("20060102T150405"), s.seq, tmpSuffix)
	s.seq++
	f, err := os.OpenFile(filepath.Join(s.dir, name), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	s.file, s.bw, s.hot, s.size, s.opened = f, bufio.NewWriterSize(f, flushBuffer), name, 0, time.Now()
	return nil
}

func (s *Sealed) Write(r Record) error {
	line, err := marshalRecord(r)
	if err != nil {
		return err
	}
	n := int64(len(line)) + 1
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.policy != nil && s.policy.ShouldRotate(time.Since(s.opened), s.size) && s.size > 0 {
		if err := s.sealLocked(); err != nil {
			return err
		}
	}
	if _, err := s.bw.Write(line); err != nil {
		atomic.AddInt64(&s.shortWrites, 1)
		atomic.StoreInt32(&s.dirty, 1)
		return err
	}
	if err := s.bw.WriteByte('\n'); err != nil {
		atomic.AddInt64(&s.shortWrites, 1)
		atomic.StoreInt32(&s.dirty, 1)
		return err
	}
	s.size += n
	s.tracker.add(r.Topic, r.Key, r.TS, n)
	atomic.StoreInt32(&s.dirty, 1)
	if s.bw.Buffered() >= flushThreshold {
		if err := s.bw.Flush(); err != nil {
			atomic.AddInt64(&s.shortWrites, 1)
			return err
		}
	}
	return nil
}

// Flush makes the hot segment durable: buffer to the file, then fsync. The recorder
// calls it once a second; the writer also flushes early above flushThreshold so a
// burst does not wait a full second. The durability window is one second or 256 KB.
func (s *Sealed) Flush() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.flushLocked()
}

func (s *Sealed) flushLocked() error {
	if s.file == nil || s.bw == nil {
		return nil
	}
	if err := s.bw.Flush(); err != nil {
		atomic.AddInt64(&s.shortWrites, 1)
		return err
	}
	if err := s.file.Sync(); err != nil {
		atomic.AddInt64(&s.fsyncErrors, 1)
		return err
	}
	atomic.StoreInt32(&s.dirty, 0)
	return nil
}

// Counters exposes the failure numbers the observer and the sweep read.
func (s *Sealed) Counters() (fsyncErrors, shortWrites int64, dirty bool) {
	return atomic.LoadInt64(&s.fsyncErrors), atomic.LoadInt64(&s.shortWrites),
		atomic.LoadInt32(&s.dirty) == 1
}

// sealLocked closes the hot segment, renames it into place, and writes its index
// before returning: no reader ever meets a sealed segment without a sidecar.
func (s *Sealed) sealLocked() error {
	if s.file != nil {
		_ = s.flushLocked()
		_ = s.file.Close()
		s.file, s.bw = nil, nil
	}
	final := strings.TrimSuffix(s.hot, tmpSuffix)
	if err := os.Rename(filepath.Join(s.dir, s.hot), filepath.Join(s.dir, final)); err != nil {
		return err
	}
	fsyncDir(s.dir)
	recs, bytes, spans := s.tracker.snapshot()
	if recs > 0 {
		st := SegmentStats{OpenedAt: s.opened.UnixMilli(), SealedAt: nowMillis(),
			Records: recs, Bytes: bytes, Codec: "none", Topics: spans}
		if err := writeSidecar(filepath.Join(s.dir, final), st); err != nil {
			return err
		}
		atomic.AddInt64(&s.sealed, 1)
		select {
		case s.compress <- final:
		default:
			// the queue is full: compression is best-effort, the plain segment is
			// already sealed and indexed, so nothing is lost
		}
	}
	s.tracker.reset()
	return s.openHotLocked()
}

// compressLoop seals with gzip, then VERIFIES by streaming the gz back and checking
// the crc before unlinking the plain file.
func (s *Sealed) compressLoop() {
	defer s.wg.Done()
	for {
		select {
		case <-s.stop:
			return
		case name := <-s.compress:
			if err := s.compressOne(name); err != nil {
				atomic.AddInt64(&s.failed, 1)
			}
		}
	}
}

func (s *Sealed) compressOne(name string) error {
	plain := filepath.Join(s.dir, name)
	gzTmp := plain + ".gz" + tmpSuffix
	plainCRC, err := fileCRC(plain)
	if err != nil {
		return err
	}

	in, err := os.Open(plain)
	if err != nil {
		return err
	}
	out, err := os.Create(gzTmp)
	if err != nil {
		in.Close()
		return err
	}
	gz, _ := gzip.NewWriterLevel(out, 5)
	if _, err := io.Copy(gz, in); err != nil {
		gz.Close()
		out.Close()
		in.Close()
		os.Remove(gzTmp)
		return err
	}
	if err := gz.Close(); err != nil {
		out.Close()
		in.Close()
		os.Remove(gzTmp)
		return err
	}
	if err := out.Sync(); err != nil {
		out.Close()
		in.Close()
		os.Remove(gzTmp)
		return err
	}
	out.Close()
	in.Close()

	// verify before trusting: decompress the whole file and compare the crc
	gotCRC, err := gzCRC(gzTmp)
	if err != nil || gotCRC != plainCRC {
		os.Remove(gzTmp)
		if err == nil {
			err = fmt.Errorf("gzip verification failed for %s", name)
		}
		return err
	}

	finalGz := plain + ".gz"
	if err := os.Rename(gzTmp, finalGz); err != nil {
		return err
	}
	info, _ := os.Stat(finalGz)
	// the index follows the file it describes: move it to the .gz name, and drop
	// the plain one so a directory never holds two sidecars for one segment
	if st, err := ReadIndex(plain); err == nil {
		st.Codec = "gzip:5"
		st.Segment = finalGz
		if info != nil {
			st.CompressedBytes = info.Size()
		}
		if err := writeSidecar(finalGz, st); err != nil {
			return err
		}
		_ = os.Remove(plain + idxSuffix)
	}
	if err := os.Remove(plain); err != nil {
		return err
	}
	fsyncDir(s.dir)
	return nil
}

func fileCRC(path string) (uint32, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	h := crc32.NewIEEE()
	if _, err := io.Copy(h, bufio.NewReaderSize(f, 1<<20)); err != nil {
		return 0, err
	}
	return h.Sum32(), nil
}

func gzCRC(path string) (uint32, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return 0, err
	}
	defer gz.Close()
	h := crc32.NewIEEE()
	if _, err := io.Copy(h, gz); err != nil {
		return 0, err
	}
	return h.Sum32(), nil
}

func fsyncDir(dir string) {
	if d, err := os.Open(dir); err == nil {
		_ = d.Sync()
		d.Close()
	}
}

func (s *Sealed) Close() error {
	var err error
	s.once.Do(func() {
		close(s.stop)
		s.mu.Lock()
		if s.file != nil {
			_ = s.flushLocked()
			_ = s.file.Close()
			s.file, s.bw = nil, nil
			// seal the final segment so it gets an index like any other
			final := strings.TrimSuffix(s.hot, tmpSuffix)
			if rerr := os.Rename(filepath.Join(s.dir, s.hot), filepath.Join(s.dir, final)); rerr == nil {
				recs, bytes, spans := s.tracker.snapshot()
				if recs > 0 {
					_ = writeSidecar(filepath.Join(s.dir, final),
						SegmentStats{OpenedAt: s.opened.UnixMilli(), SealedAt: nowMillis(),
							Records: recs, Bytes: bytes, Codec: "none", Topics: spans})
				}
			} else {
				err = rerr
			}
		}
		s.mu.Unlock()
		s.wg.Wait()
		if s.lock != nil {
			syscall.Flock(int(s.lock.Fd()), syscall.LOCK_UN)
			s.lock.Close()
		}
	})
	return err
}

// Stats reports what the writer has done, for the bench tool and the health view.
func (s *Sealed) Stats() (sealed, failed, rebuilt int64) {
	return atomic.LoadInt64(&s.sealed), atomic.LoadInt64(&s.failed), atomic.LoadInt64(&s.rebuild)
}

// ScanSegment is the read-side entry point: scan one planned segment for a window.
// It scans the whole segment (no bucket index); kept for callers with no sidecar.
func ScanSegment(path string, from, to int64, fn func(Record) error) (int, error) {
	return readSegment(path, from, to, nil, fn)
}

// ScanSegmentSeek scans a window using the segment's time buckets to skip the
// front of the file. Absent or out-of-order buckets degrade to a full scan, so a
// pre-change segment still reads correctly.
func ScanSegmentSeek(path string, from, to int64, buckets []TimeBucket, fn func(Record) error) (int, error) {
	return readSegment(path, from, to, buckets, fn)
}

// bucketSeekOffset returns the byte offset to seek to for a window starting at
// `from`: the offset of the last bucket whose Ts <= from. Monotonicity invariant:
// a segment is written in arrival order and record TS is assigned at write time,
// so byte offset is non-decreasing with TS. If the buckets are out of order the
// invariant is broken, and 0 is returned so the caller scans from the front.
func bucketSeekOffset(buckets []TimeBucket, from int64) int64 {
	if len(buckets) == 0 {
		return 0
	}
	for i := 1; i < len(buckets); i++ {
		if buckets[i].Ts < buckets[i-1].Ts {
			return 0
		}
	}
	lo, hi := 0, len(buckets)-1
	best := -1
	for lo <= hi {
		mid := (lo + hi) / 2
		if buckets[mid].Ts < from {
			best = mid
			lo = mid + 1
		} else {
			hi = mid - 1
		}
	}
	if best < 0 {
		return 0
	}
	return buckets[best].Offset
}

// readSegment scans a segment (plain or gzipped) within a byte span, calling fn
// for each record. Buckets enable a seek on a plain segment; a gzipped segment
// has no usable plain offset, so it is decompressed and scanned whole.
func readSegment(path string, from, to int64, buckets []TimeBucket, fn func(Record) error) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	var reader io.Reader
	if strings.HasSuffix(path, ".gz") {
		gz, err := gzip.NewReader(f)
		if err != nil {
			return 0, err
		}
		defer gz.Close()
		reader = gz
	} else {
		if off := bucketSeekOffset(buckets, from); off > 0 {
			if _, err := f.Seek(off, io.SeekStart); err != nil {
				return 0, err
			}
		}
		reader = f
	}
	sc := bufio.NewScanner(reader)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	n := 0
	for sc.Scan() {
		var rec Record
		if json.Unmarshal(sc.Bytes(), &rec) != nil {
			continue
		}
		if rec.TS < from || rec.TS > to {
			continue
		}
		if err := fn(rec); err != nil {
			return n, err
		}
		n++
	}
	return n, sc.Err()
}
