// lakebench measures the hot write path: records per second, bytes per second, and
// caller-visible latency percentiles, with the harness kept out of the measurement.
//
//	./bin/lakebench -dir /tmp/bench -rate 3150 -seconds 20
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"runtime"
	"time"

	"cryptalyst/core/internal/lake"
)

// histogram is a preallocated log-bucket counter: no allocation on the write path.
type histogram struct {
	buckets []int64
	count   int64
	sum     time.Duration
}

func newHistogram() *histogram { return &histogram{buckets: make([]int64, 64)} }

func bucketOf(ns int64) int {
	b := 0
	for ns > 1<<uint(b) && b < 63 {
		b++
	}
	return b
}

func (h *histogram) add(d time.Duration) {
	h.buckets[bucketOf(int64(d))]++
	h.count++
	h.sum += d
}

func (h *histogram) pct(p float64) time.Duration {
	if h.count == 0 {
		return 0
	}
	target := int64(float64(h.count) * p)
	var seen int64
	for i, c := range h.buckets {
		seen += c
		if seen >= target {
			return time.Duration(1 << uint(i))
		}
	}
	return time.Duration(1 << 40)
}

func main() {
	dir := flag.String("dir", "/tmp/lakebench", "directory to write into")
	rate := flag.Int("rate", 3150, "records per second")
	seconds := flag.Int("seconds", 20, "duration")
	payload := flag.Int("payload", 300, "payload bytes per record")
	flag.Parse()

	_ = os.RemoveAll(*dir)
	s, err := lake.OpenSealed(lake.RetentionConfig{Dir: *dir},
		lake.HourlyOrCeiling{Age: time.Hour, Bytes: 1 << 30})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer s.Close()

	// (1) records are marshalled once and reused: json.Marshal is not part of the path
	pad := make([]byte, *payload)
	for i := range pad {
		pad[i] = 'x'
	}
	body, _ := json.Marshal(map[string]any{"symbol": "BTCUSDT", "pad": string(pad)})
	env, _ := json.Marshal(map[string]any{"data": json.RawMessage(body), "meta": map[string]any{"topic": "market.depth"}})
	rec := lake.Record{Topic: "market.depth", Key: "BTCUSDT", TS: 1, Payload: body, Envelope: env}

	h := newHistogram()
	deadline := time.Now().Add(time.Duration(*seconds) * time.Second)
	interval := time.Second / time.Duration(*rate)
	next := time.Now()
	var written int64
	writtenBytes := int64(len(env) + 32)

	for time.Now().Before(deadline) {
		now := time.Now()
		if now.Before(next) {
			time.Sleep(next.Sub(now))
		}
		rec.TS = next.UnixMilli()
		t0 := time.Now()
		if err := s.Write(rec); err != nil {
			fmt.Fprintln(os.Stderr, "write:", err)
			os.Exit(1)
		}
		h.add(time.Since(t0))
		written++
		next = next.Add(interval)
		if next.Before(time.Now().Add(-interval)) {
			next = time.Now() // fell behind: report the missed rate, do not inflate latency
		}
	}
	if err := s.Flush(); err != nil {
		fmt.Fprintln(os.Stderr, "flush:", err)
	}

	fsyncErrs, shorts, dirty := s.Counters()
	fmt.Printf("lakebench  rate=%d/s  duration=%ds  records=%d\n", *rate, *seconds, written)
	fmt.Printf("throughput  %.0f rec/s  %.1f MB/s\n",
		float64(written)/float64(*seconds), float64(written*writtenBytes)/float64(*seconds)/1e6)
	fmt.Printf("latency     p50=%s p99=%s p999=%s mean=%s\n",
		h.pct(0.50).Round(time.Nanosecond), h.pct(0.99).Round(time.Nanosecond),
		h.pct(0.999).Round(time.Nanosecond), (h.sum / time.Duration(h.count)).Round(time.Nanosecond))
	fmt.Printf("health      fsync_errors=%d short_writes=%d dirty=%v\n", fsyncErrs, shorts, dirty)
	fmt.Printf("machine     %s/%s cores=%d GOMAXPROCS=%d\n", runtime.GOOS, runtime.GOARCH,
		runtime.NumCPU(), runtime.GOMAXPROCS(0))
}
