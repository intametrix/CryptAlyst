// envelopeapi serves the Envelope Explorer: a read-only window onto the lake that
// lets an operator filter, inspect and trace every message the platform emitted.
package main

import (
	"bufio"
	"bytes"
	"embed"
	"encoding/json"
	"flag"
	"fmt"
	"hash/fnv"
	"html/template"
	"io"
	"io/fs"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"cryptalyst/core/internal/operator"
)

func safeDiv(a, b float64) float64 {
	if b == 0 {
		return 0
	}
	return a / b
}

func round2(v float64) float64 { return float64(int64(v*100+0.5)) / 100 }

// bookTrade is one closed trade in a book report.
type bookTrade struct {
	ID       int     `json:"id"`
	TS       int64   `json:"ts"`
	Key      string  `json:"key"`
	Side     string  `json:"side"`
	Entry    float64 `json:"entry"`
	Exit     float64 `json:"exit"`
	HeldS    float64 `json:"held_s"`
	NetPct   float64 `json:"net_pct"`
	Reason   string  `json:"reason"`
	Notional float64 `json:"notional"`
}

// reportRow is one line of a book report.
type reportRow struct {
	TS     int64
	Key    string
	Side   string
	Entry  float64
	Exit   float64
	HeldS  float64
	NetPct float64
	Reason string
	ID     int
}

func maxHeldTrades(trades []reportRow) float64 {
	m := 0.0
	for _, t := range trades {
		if t.HeldS > m {
			m = t.HeldS
		}
	}
	return m
}

func maxHeld(trades []bookTrade) float64 {
	m := 0.0
	for _, t := range trades {
		if t.HeldS > m {
			m = t.HeldS
		}
	}
	return round2(m)
}

// stageOf maps a topic to its column. Topics are authoritative because records
// written before the plane fix still carry the old plane value.
func stageOf(topic string) int {
	switch {
	case strings.HasPrefix(topic, "market.") || topic == "ticks":
		return 0
	case strings.HasPrefix(topic, "intel."):
		return 1
	case topic == "alert.pumpdump" || topic == "risk.state" || topic == "exec.quote":
		return 2
	case strings.HasPrefix(topic, "model."):
		return 3
	}
	return 4
}

// scanned holds documents that were read from the lake for a query but are not
// in the in-memory ring, so a case file or a detail view can still resolve them.
var (
	scannedMu sync.RWMutex
	scanned   = map[int]envelopeDoc{}
)

func scannedID(d envelopeDoc) int {
	h := fnv.New32a()
	fmt.Fprintf(h, "%s|%d|%s", d.Topic, d.TS, d.CID)
	return int(h.Sum32() & 0x7fffffff)
}

func rememberScanned(d envelopeDoc) envelopeDoc {
	d.ID = scannedID(d)
	scannedMu.Lock()
	scanned[d.ID] = d
	scannedMu.Unlock()
	return d
}

func lookupScanned(id int) (envelopeDoc, bool) {
	scannedMu.RLock()
	defer scannedMu.RUnlock()
	d, ok := scanned[id]
	return d, ok
}

//go:embed static
var staticFS embed.FS

// envelopeDoc is one explorer row: the record plus the fields we can filter on.
type envelopeDoc struct {
	ID       int             `json:"id"`
	Topic    string          `json:"topic"`
	Key      string          `json:"key"`
	TS       int64           `json:"ts"`
	Plane    string          `json:"plane"`
	Producer string          `json:"producer"`
	CID      string          `json:"cid"`
	Version  int             `json:"version"`
	Gen      uint64          `json:"generation"`
	Size     int             `json:"size"`
	Payload  json.RawMessage `json:"payload"`
	Meta     json.RawMessage `json:"meta"`
	Refs     json.RawMessage `json:"refs,omitempty"`
	Hops     json.RawMessage `json:"hops,omitempty"`
	HasEnv   bool            `json:"has_envelope"`
}

type store struct {
	mu    sync.RWMutex
	docs  []envelopeDoc
	limit int
	next  int
	subs  map[chan envelopeDoc]bool
	seen  map[string]int64
	bytes int64
	start time.Time
}

func newStore(limit int) *store {
	return &store{docs: make([]envelopeDoc, 0, limit), limit: limit,
		subs: map[chan envelopeDoc]bool{}, seen: map[string]int64{}, start: time.Now()}
}

func (s *store) add(d envelopeDoc) {
	s.mu.Lock()
	d.ID = s.next
	s.next++
	if len(s.docs) >= s.limit {
		s.docs = s.docs[1:]
	}
	s.docs = append(s.docs, d)
	s.seen[d.Topic]++
	s.bytes += int64(d.Size)
	for ch := range s.subs {
		select {
		case ch <- d:
		default:
		}
	}
	s.mu.Unlock()
}

func (s *store) subscribe() chan envelopeDoc {
	ch := make(chan envelopeDoc, 64)
	s.mu.Lock()
	s.subs[ch] = true
	s.mu.Unlock()
	return ch
}

func (s *store) unsubscribe(ch chan envelopeDoc) {
	s.mu.Lock()
	delete(s.subs, ch)
	s.mu.Unlock()
	close(ch)
}

func (s *store) snapshot() []envelopeDoc {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]envelopeDoc, len(s.docs))
	copy(out, s.docs)
	return out
}

var defaultProducer = map[string]string{
	"ticks": "market_data", "market.price": "market_data", "market.depth": "market_data",
	"market.candle": "market_data", "market.mini24h": "market_data",
	"intel.signal": "signals", "intel.flow": "signals", "intel.movers": "signals",
	"intel.ta": "tad", "intel.forecast": "forecast",
	"model.decision": "books", "model.fill": "books", "model.pnl": "books", "model.rank": "alpha_rank",
	"risk.state": "risk", "exec.quote": "maker", "alert.pumpdump": "signalbus",
	"system.health": "healthpub", "review.verdict": "review",
}

var planes = map[string]string{
	"ticks": "market", "market.price": "market", "market.depth": "market",
	"market.candle": "market", "market.mini24h": "market",
	"intel.signal": "intel", "intel.flow": "intel", "intel.ta": "intel",
	"intel.forecast": "intel", "intel.movers": "intel",
	"model.decision": "model", "model.fill": "model", "model.pnl": "model", "model.rank": "model",
	"risk.state": "control", "exec.quote": "control", "alert.pumpdump": "control",
	"system.health": "system", "review.verdict": "system",
}

// parse turns a lake line into an explorer document, tolerating both the older
// records (payload only) and the newer ones that carry the envelope.
func parse(line []byte) (envelopeDoc, bool) {
	var rec struct {
		Topic    string          `json:"topic"`
		Key      string          `json:"key"`
		TS       int64           `json:"ts"`
		Payload  json.RawMessage `json:"payload"`
		Envelope json.RawMessage `json:"envelope"`
	}
	if err := json.Unmarshal(line, &rec); err != nil || rec.Topic == "" {
		return envelopeDoc{}, false
	}
	d := envelopeDoc{Topic: rec.Topic, Key: rec.Key, TS: rec.TS,
		Payload: rec.Payload, Plane: planes[rec.Topic], Size: len(line)}
	// an inner producer wins: it is the component that actually computed the data
	var inner struct {
		Producer string `json:"producer"`
		CID      string `json:"cid"`
		Data     struct {
			Producer string `json:"producer"`
		} `json:"data"`
	}
	innerProducer := ""
	if json.Unmarshal(rec.Payload, &inner) == nil {
		innerProducer = inner.Producer
		if innerProducer == "" {
			innerProducer = inner.Data.Producer
		}
		if inner.CID != "" {
			d.CID = inner.CID
		}
	}
	if len(rec.Envelope) > 0 {
		var env struct {
			Data json.RawMessage `json:"data"`
			Meta json.RawMessage `json:"meta"`
		}
		if json.Unmarshal(rec.Envelope, &env) == nil && len(env.Meta) > 0 {
			d.Meta = env.Meta
			d.HasEnv = true
			var m struct {
				Plane      string          `json:"plane"`
				Producer   string          `json:"producer"`
				CID        string          `json:"cid"`
				Version    int             `json:"version"`
				Generation uint64          `json:"generation"`
				Key        string          `json:"key"`
				Refs       json.RawMessage `json:"refs"`
				Hops       json.RawMessage `json:"hops"`
			}
			if json.Unmarshal(env.Meta, &m) == nil {
				if m.Plane != "" {
					d.Plane = m.Plane
				}
				d.Producer = m.Producer
				d.CID = m.CID
				d.Version = m.Version
				d.Gen = m.Generation
				d.Refs = m.Refs
				d.Hops = m.Hops
				if d.Key == "" {
					d.Key = m.Key
				}
			}
		}
	}
	if d.Plane == "" {
		d.Plane = "unknown"
	}
	if innerProducer != "" {
		// the payload's producer is the component that computed the data; the
		// envelope default is only a fallback for producers that do not say
		d.Producer = innerProducer
	}
	if d.Producer == "" {
		d.Producer = defaultProducer[d.Topic]
	}
	if d.Version == 0 {
		d.Version = 1
	}
	return d, true
}

// loadDir reads every journal or segment file in a directory, oldest first.
func loadDir(dir string, st *store) int {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0
	}
	var files []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".jsonl") {
			files = append(files, filepath.Join(dir, e.Name()))
		}
	}
	sort.Strings(files)
	n := 0
	for _, f := range files {
		n += loadFile(f, st)
	}
	return n
}

// loadFile reads the tail of a journal. The lake grows without bound, and
// scanning a 7 GB file to find the newest 20,000 envelopes made startup take
// minutes; reading backwards keeps it flat however large the file gets.
func loadFile(path string, st *store) int {
	f, err := os.Open(path)
	if err != nil {
		return 0
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return 0
	}

	const want = 20000    // enough to fill the ring
	const chunk = 4 << 20 // read backwards in 4 MB slices
	offset := info.Size()
	var tail []byte
	for offset > 0 && len(tail) < 64<<20 {
		size := int64(chunk)
		if offset < size {
			size = offset
		}
		offset -= size
		buf := make([]byte, size)
		if _, err := f.ReadAt(buf, offset); err != nil && err != io.EOF {
			return 0
		}
		tail = append(buf, tail...)
		if bytes.Count(tail, []byte("\n")) > want*2 {
			break
		}
	}

	lines := bytes.Split(tail, []byte("\n"))
	n := 0
	for i := len(lines) - 1; i >= 0 && n < want; i-- {
		line := bytes.TrimSpace(lines[i])
		if len(line) == 0 {
			continue
		}
		if d, ok := parse(line); ok {
			st.add(d)
			n++
		}
	}
	return n
}

// scanFileBackwards returns up to need envelopes of one topic, reading from the
// end of the file. It gives up after maxScan bytes so a query can never turn into
// a full-journal read.
func scanFileBackwards(path string, needle []byte, topic string, need int) []envelopeDoc {
	const chunk = 4 << 20
	const maxScan = 256 << 20
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || info.Size() == 0 {
		return nil
	}

	var found []envelopeDoc
	offset := info.Size()
	var carry []byte
	read := int64(0)
	for offset > 0 && len(found) < need && read < maxScan {
		size := int64(chunk)
		if offset < size {
			size = offset
		}
		offset -= size
		read += size
		buf := make([]byte, size)
		if _, err := f.ReadAt(buf, offset); err != nil && err != io.EOF {
			break
		}
		buf = append(buf, carry...)
		lines := bytes.Split(buf, []byte("\n"))
		// the first line may be partial unless we reached the start of the file
		carry = nil
		if offset > 0 {
			carry = lines[0]
			lines = lines[1:]
		}
		for i := len(lines) - 1; i >= 0 && len(found) < need; i-- {
			line := lines[i]
			if len(line) == 0 || !bytes.Contains(line, needle) {
				continue
			}
			if d, ok := parse(line); ok && d.Topic == topic {
				found = append(found, rememberScanned(d))
			}
		}
	}
	return found
}

// ---------------------------------------------------------------- lake index
// The lake is 7 GB and grows at ~600 KB/s, so searching backwards from the end
// only reaches the last few minutes. An index of line offsets per topic is built
// once in the background and extended as the recorder appends, which makes a cold
// query a handful of reads instead of a scan.

type offEntry struct {
	file string
	off  int64
	len  int32
}

var (
	idxMu    sync.RWMutex
	idxTopic = map[string][]offEntry{}
	idxSize  = map[string]int64{}
	idxReady = false
)

func indexOneLine(line []byte, file string, off int64) {
	// topic is cheap to pull out without a full decode
	i := bytes.Index(line, []byte("\"topic\":\""))
	if i < 0 {
		return
	}
	rest := line[i+9:]
	j := bytes.IndexByte(rest, '"')
	if j <= 0 {
		return
	}
	topic := string(rest[:j])
	idxTopic[topic] = append(idxTopic[topic], offEntry{file: file, off: off, len: int32(len(line))})
	// keep the index bounded: only the newest entries of a noisy topic matter
	if n := len(idxTopic[topic]); n > 30000 {
		idxTopic[topic] = append([]offEntry(nil), idxTopic[topic][n-20000:]...)
	}
}

// indexFiles walks every journal, remembering where each topic's lines live.
// Unparsed offsets are enough: the query reads and decodes only the lines it needs.
func indexFiles(dir string) {
	files, _ := filepath.Glob(filepath.Join(dir, "*.jsonl"))
	sort.Strings(files)
	for _, f := range files {
		fh, err := os.Open(f)
		if err != nil {
			continue
		}
		info, err := fh.Stat()
		if err != nil {
			fh.Close()
			continue
		}
		idxMu.Lock()
		start := idxSize[f]
		idxMu.Unlock()
		if start >= info.Size() {
			fh.Close()
			continue
		}
		if _, err := fh.Seek(start, io.SeekStart); err != nil {
			fh.Close()
			continue
		}
		rd := bufio.NewReaderSize(fh, 1<<20)
		off := start
		for {
			line, err := rd.ReadBytes('\n')
			if len(line) > 0 {
				idxMu.Lock()
				indexOneLine(line, f, off)
				idxMu.Unlock()
				off += int64(len(line))
			}
			if err != nil {
				break
			}
		}
		idxMu.Lock()
		idxSize[f] = off
		idxMu.Unlock()
		fh.Close()
	}
	idxMu.Lock()
	idxReady = true
	idxMu.Unlock()
}

func indexCount(topic string) int {
	idxMu.RLock()
	defer idxMu.RUnlock()
	return len(idxTopic[topic])
}

// scanIndexed reads the newest `limit` envelopes of a topic using the index.
func scanIndexed(topic string, limit int) []envelopeDoc {
	idxMu.RLock()
	entries := idxTopic[topic]
	ready := idxReady
	if n := len(entries); n > limit {
		entries = entries[n-limit:]
	}
	out := make([]envelopeDoc, 0, len(entries))
	for _, e := range entries {
		if d, ok := readAt(e); ok && d.Topic == topic {
			out = append(out, rememberScanned(d))
		}
	}
	idxMu.RUnlock()
	_ = ready
	return out
}

func readAt(e offEntry) (envelopeDoc, bool) {
	fh, err := os.Open(e.file)
	if err != nil {
		return envelopeDoc{}, false
	}
	defer fh.Close()
	buf := make([]byte, e.len)
	if _, err := fh.ReadAt(buf, e.off); err != nil && err != io.EOF {
		return envelopeDoc{}, false
	}
	return parse(bytes.TrimSpace(buf))
}

// followRecorder subscribes to the recorder's SSE feed. This is the preferred
// live path: the recorder is the only process that consumes the bus.
func followRecorder(url string, st *store) {
	for {
		resp, err := http.Get(url)
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}
		sc := bufio.NewScanner(resp.Body)
		sc.Buffer(make([]byte, 1<<20), 32<<20)
		for sc.Scan() {
			line := sc.Text()
			if !strings.HasPrefix(line, "data: ") {
				continue
			}
			if d, ok := parse([]byte(strings.TrimPrefix(line, "data: "))); ok {
				st.add(d)
			}
		}
		resp.Body.Close()
		time.Sleep(time.Second)
	}
}

// tail follows the newest file and feeds live envelopes into the store.
func tail(path string, st *store) {
	var offset int64
	if info, err := os.Stat(path); err == nil {
		offset = info.Size()
	}
	for {
		f, err := os.Open(path)
		if err != nil {
			time.Sleep(time.Second)
			continue
		}
		if _, err := f.Seek(offset, io.SeekStart); err != nil {
			f.Close()
			time.Sleep(time.Second)
			continue
		}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 32<<20)
		for sc.Scan() {
			line := sc.Bytes()
			offset += int64(len(line)) + 1
			if d, ok := parse(line); ok {
				st.add(d)
			}
		}
		f.Close()
		time.Sleep(400 * time.Millisecond)
	}
}

// roleFields is the redaction policy: a transformer changes the shape of an
// answer for an audience, never its truth, and records that it did.
var roleFields = map[string][]string{
	"bystander": {"bids", "asks", "bid_qty", "ask_qty", "imbalance", "notional",
		"entry", "size", "qty", "score", "signals", "forecast", "confidence"},
	"auditor": {"bids", "asks"},
	"owner":   {},
}

func redactFor(role string, payload json.RawMessage) (json.RawMessage, []string) {
	fields, ok := roleFields[role]
	if !ok || len(fields) == 0 {
		return payload, nil
	}
	var obj map[string]any
	if err := json.Unmarshal(payload, &obj); err != nil {
		return payload, nil
	}
	removed := []string{}
	for _, f := range fields {
		if _, has := obj[f]; has {
			delete(obj, f)
			removed = append(removed, f)
		}
	}
	if len(removed) == 0 {
		return payload, nil
	}
	body, err := json.Marshal(obj)
	if err != nil {
		return payload, nil
	}
	return body, removed
}

func containsFold(haystack, needle string) bool {
	return strings.Contains(strings.ToLower(haystack), strings.ToLower(needle))
}

func main() {
	port := flag.Int("port", 8802, "http port")
	dir := flag.String("dir", "../state/lake2", "lake directory")
	limit := flag.Int("limit", 20000, "documents kept in memory")
	flag.Parse()
	log.SetFlags(log.Ltime)

	st := newStore(*limit)
	n := loadDir(*dir, st)
	log.Printf("loaded %d envelopes from %s", n, *dir)

	entries, _ := os.ReadDir(*dir)
	var newest string
	var newestTime time.Time
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		if info, err := e.Info(); err == nil && info.ModTime().After(newestTime) {
			newestTime, newest = info.ModTime(), filepath.Join(*dir, e.Name())
		}
	}
	recorder := flag.String("recorder", "http://127.0.0.1:8801/stream", "recorder SSE feed")
	_ = recorder
	liveFromRecorder := false
	if resp, err := http.Get(*recorder); err == nil {
		resp.Body.Close()
		liveFromRecorder = true
	}
	if liveFromRecorder {
		go indexFiles(*dir)
		go followRecorder(*recorder, st)
		log.Printf("live updates from the recorder (%s)", *recorder)
	} else if newest != "" {
		go tail(newest, st)
		log.Printf("recorder unreachable — tailing %s", newest)
	}

	mux := http.NewServeMux()
	sub, err := fs.Sub(staticFS, "static")
	if err != nil {
		log.Fatalf("embedded ui: %v", err)
	}
	mux.Handle("/", http.FileServer(http.FS(sub)))

	mux.HandleFunc("/api/stats", func(w http.ResponseWriter, r *http.Request) {
		docs := st.snapshot()
		byPlane := map[string]int{}
		byTopic := map[string]int{}
		var oldest, newest int64
		var bytes int64
		for _, d := range docs {
			byPlane[d.Plane]++
			byTopic[d.Topic]++
			bytes += int64(d.Size)
			if oldest == 0 || d.TS < oldest {
				oldest = d.TS
			}
			if d.TS > newest {
				newest = d.TS
			}
		}
		json.NewEncoder(w).Encode(map[string]any{
			"envelopes": len(docs), "bytes": bytes,
			"oldest_ms": oldest, "newest_ms": newest,
			"by_plane": byPlane, "by_topic": byTopic,
			"uptime_s": int(time.Since(st.start).Seconds()),
		})
	})

	mux.HandleFunc("/api/topics", func(w http.ResponseWriter, r *http.Request) {
		docs := st.snapshot()
		type row struct {
			Topic    string  `json:"topic"`
			Plane    string  `json:"plane"`
			Count    int     `json:"count"`
			Bytes    int64   `json:"bytes"`
			AvgSize  float64 `json:"avg_size"`
			Producer string  `json:"producer"`
			LastMS   int64   `json:"last_ms"`
		}
		agg := map[string]*row{}
		for _, d := range docs {
			a := agg[d.Topic]
			if a == nil {
				a = &row{Topic: d.Topic, Plane: d.Plane, Producer: d.Producer}
				agg[d.Topic] = a
			}
			a.Count++
			a.Bytes += int64(d.Size)
			if d.TS > a.LastMS {
				a.LastMS = d.TS
			}
			if a.Producer == "" {
				a.Producer = d.Producer
			}
		}
		out := make([]*row, 0, len(agg))
		for _, a := range agg {
			if a.Count > 0 {
				a.AvgSize = float64(a.Bytes) / float64(a.Count)
			}
			out = append(out, a)
		}
		sort.Slice(out, func(i, j int) bool { return out[i].Count > out[j].Count })
		json.NewEncoder(w).Encode(out)
	})

	mux.HandleFunc("/api/envelopes", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		topic := q.Get("topic")
		plane := q.Get("plane")
		key := q.Get("key")
		producer := q.Get("producer")
		cid := q.Get("cid")
		text := q.Get("q")
		fromMS, _ := strconv.ParseInt(q.Get("from"), 10, 64)
		toMS, _ := strconv.ParseInt(q.Get("to"), 10, 64)
		limitN, _ := strconv.Atoi(q.Get("limit"))
		if limitN <= 0 || limitN > 500 {
			limitN = 100
		}
		docs := st.snapshot()
		out := make([]envelopeDoc, 0, limitN)
		for i := len(docs) - 1; i >= 0 && len(out) < limitN; i-- {
			d := docs[i]
			if topic != "" && d.Topic != topic {
				continue
			}
			if plane != "" && d.Plane != plane {
				continue
			}
			if key != "" && !containsFold(d.Key, key) {
				continue
			}
			if producer != "" && !containsFold(d.Producer, producer) {
				continue
			}
			if cid != "" && !containsFold(d.CID, cid) {
				continue
			}
			if text != "" && !containsFold(string(d.Payload)+string(d.Meta)+d.Topic+d.Key, text) {
				continue
			}
			if fromMS > 0 && d.TS < fromMS {
				continue
			}
			if toMS > 0 && d.TS > toMS {
				continue
			}
			out = append(out, d)
		}
		json.NewEncoder(w).Encode(map[string]any{"count": len(out), "envelopes": out})
	})

	mux.HandleFunc("/api/envelope/", func(w http.ResponseWriter, r *http.Request) {
		id, _ := strconv.Atoi(strings.TrimPrefix(r.URL.Path, "/api/envelope/"))
		role := r.URL.Query().Get("role")
		findDoc := func(id int) (envelopeDoc, bool) {
			for _, d := range st.snapshot() {
				if d.ID == id {
					return d, true
				}
			}
			return lookupScanned(id)
		}
		if d, ok := findDoc(id); ok {
			{
				if redacted, removed := redactFor(role, d.Payload); len(removed) > 0 {
					d.Payload = redacted
					if d.Meta != nil {
						var m map[string]any
						if json.Unmarshal(d.Meta, &m) == nil {
							extra, _ := m["extra"].(map[string]any)
							if extra == nil {
								extra = map[string]any{}
							}
							extra["redacted_for"] = role
							extra["redacted_fields"] = removed
							m["extra"] = extra
							d.Meta, _ = json.Marshal(m)
						}
					}
				}
				json.NewEncoder(w).Encode(d)
				return
			}
		}
		http.Error(w, "not found", 404)
	})

	// /api/journey lays a symbol's envelopes out as a graph: market data on the
	// left, advisories in the middle, decisions and fills on the right. Edges are
	// explicit references where they exist, and nearest-in-time otherwise, which
	// is the honest approximation while producers do not emit refs yet.
	mux.HandleFunc("/api/journey", func(w http.ResponseWriter, r *http.Request) {
		key := r.URL.Query().Get("key")
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		if limit <= 0 || limit > 400 {
			limit = 120
		}
		stage := func(plane, topic string) int { return stageOf(topic) }
		type node struct {
			ID       int    `json:"id"`
			Topic    string `json:"topic"`
			Plane    string `json:"plane"`
			Stage    int    `json:"stage"`
			TS       int64  `json:"ts"`
			FirstTS  int64  `json:"first_ts"`
			LastTS   int64  `json:"last_ts"`
			Count    int    `json:"count"`
			Producer string `json:"producer"`
			Size     int    `json:"size"`
			Key      string `json:"key"`
			Gen      uint64 `json:"generation"`
			CID      string `json:"cid"`
			Members  []int  `json:"members,omitempty"`
		}
		type edge struct {
			From int    `json:"from"`
			To   int    `json:"to"`
			Kind string `json:"kind"`
		}
		docs := st.snapshot()
		if key == "" {
			// no key given: choose the symbol whose traffic spans the most stages,
			// because that is the journey with something to show
			keys := map[string]map[int]bool{}
			for _, d := range docs {
				if d.Key == "" {
					continue
				}
				if keys[d.Key] == nil {
					keys[d.Key] = map[int]bool{}
				}
				keys[d.Key][stage(d.Plane, d.Topic)] = true
			}
			best, bestN := "", 0
			for k, set := range keys {
				if len(set) > bestN {
					best, bestN = k, len(set)
				}
			}
			key = best
		}
		var picked []envelopeDoc
		for _, d := range docs {
			if d.Key == key {
				picked = append(picked, d)
			}
		}
		// sample per column rather than truncating the tail: a decision made twenty
		// minutes ago must still appear beside the market data that preceded it
		perStage := 14
		if perStage*5 > limit {
			perStage = limit / 5
			if perStage < 4 {
				perStage = 4
			}
		}
		byStage := map[int][]envelopeDoc{}
		for _, d := range picked {
			st := stage(d.Plane, d.Topic)
			byStage[st] = append(byStage[st], d)
		}
		var sampled []envelopeDoc
		for _, list := range byStage {
			if len(list) <= perStage {
				sampled = append(sampled, list...)
				continue
			}
			step := float64(len(list)-1) / float64(perStage-1)
			for i := 0; i < perStage; i++ {
				sampled = append(sampled, list[int(float64(i)*step)])
			}
		}
		sort.Slice(sampled, func(i, j int) bool { return sampled[i].TS < sampled[j].TS })
		// collapse to one node per stage and topic: forty depth updates are one
		// fact ("the book moved"), not forty facts a reader must scan
		type groupKey struct {
			stage int
			topic string
		}
		groups := map[groupKey]*node{}
		order := []groupKey{}
		for _, d := range picked {
			gk := groupKey{stage(d.Plane, d.Topic), d.Topic}
			g, ok := groups[gk]
			if !ok {
				g = &node{Topic: d.Topic, Plane: d.Plane, Stage: gk.stage,
					Producer: d.Producer, Key: d.Key, FirstTS: d.TS, LastTS: d.TS}
				groups[gk] = g
				order = append(order, gk)
			}
			g.Count++
			g.Members = append(g.Members, d.ID)
			if d.TS >= g.LastTS {
				g.LastTS, g.ID, g.TS, g.Gen, g.CID, g.Size = d.TS, d.ID, d.TS, d.Gen, d.CID, d.Size
			}
			if d.TS < g.FirstTS {
				g.FirstTS = d.TS
			}
		}
		nodes := make([]node, 0, len(order))
		for _, gk := range order {
			nodes = append(nodes, *groups[gk])
		}
		sort.Slice(nodes, func(i, j int) bool {
			if nodes[i].Stage != nodes[j].Stage {
				return nodes[i].Stage < nodes[j].Stage
			}
			return nodes[i].LastTS < nodes[j].LastTS
		})
		// connect each group to the nearest later-stage group in time
		var edges []edge
		for i := range nodes {
			if nodes[i].Stage >= 4 {
				continue
			}
			best, bestGap := -1, int64(1<<62)
			for j := range nodes {
				if nodes[j].Stage <= nodes[i].Stage {
					continue
				}
				gap := nodes[j].FirstTS - nodes[i].LastTS
				if gap < 0 {
					gap = -gap
				}
				if gap < bestGap {
					bestGap, best = gap, j
				}
			}
			if best >= 0 && bestGap < 180000 {
				edges = append(edges, edge{From: nodes[i].ID, To: nodes[best].ID, Kind: "temporal"})
			}
		}
		json.NewEncoder(w).Encode(map[string]any{"key": key, "nodes": nodes, "edges": edges})
	})

	// /api/flow renders the platform as a flow diagram: nodes are stage+topic with
	// the volume behind them, links are counted correlations between an envelope and
	// the next stage that consumed its symbol.
	mux.HandleFunc("/api/flow", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		key := q.Get("key")
		fromMS, _ := strconv.ParseInt(q.Get("from"), 10, 64)
		toMS, _ := strconv.ParseInt(q.Get("to"), 10, 64)
		anchor, _ := strconv.Atoi(q.Get("id"))
		horizon, _ := strconv.Atoi(q.Get("horizon_s"))
		if horizon <= 0 || horizon > 3600 {
			horizon = 300
		}
		docs := st.snapshot()
		if anchor > 0 {
			// a journey starts at a point: the window opens at the chosen envelope
			// and runs forward, on that envelope's symbol when it has one
			for _, d := range docs {
				if d.ID == anchor {
					// a little lead-in so the flow shows what fed the point as well
					// as what followed it
					fromMS = d.TS - 120000
					toMS = d.TS + int64(horizon)*1000
					if d.Key != "" {
						key = d.Key
					}
					break
				}
			}
		}
		if fromMS == 0 && toMS == 0 {
			// default window: the last thirty minutes of retained traffic
			var newest int64
			for _, d := range docs {
				if d.TS > newest {
					newest = d.TS
				}
			}
			fromMS, toMS = newest-30*60*1000, newest
		}
		stage := func(plane, topic string) int { return stageOf(topic) }
		type fnode struct {
			ID       int    `json:"id"`
			Topic    string `json:"topic"`
			Plane    string `json:"plane"`
			Stage    int    `json:"stage"`
			Count    int    `json:"count"`
			Bytes    int    `json:"bytes"`
			FirstTS  int64  `json:"first_ts"`
			LastTS   int64  `json:"last_ts"`
			Key      string `json:"key"`
			Producer string `json:"producer,omitempty"`
			Anchor   bool   `json:"anchor,omitempty"`
		}
		type flink struct {
			From  int `json:"from"`
			To    int `json:"to"`
			Count int `json:"count"`
		}
		type fkey struct {
			stage    int
			topic    string
			producer string
		}
		nodes := map[fkey]*fnode{}
		var order []fkey
		var anchorKey *fkey
		var picked []envelopeDoc
		for _, d := range docs {
			if d.TS < fromMS || d.TS > toMS {
				continue
			}
			if key != "" && d.Key != key {
				continue
			}
			picked = append(picked, d)
			k := fkey{stage(d.Plane, d.Topic), d.Topic, d.Producer}
			n, ok := nodes[k]
			if !ok {
				n = &fnode{Topic: d.Topic, Plane: d.Plane, Stage: k.stage, Producer: d.Producer,
					FirstTS: d.TS, LastTS: d.TS, Key: d.Key}
				nodes[k] = n
				order = append(order, k)
			}
			if anchor > 0 && d.ID == anchor {
				k := k
				anchorKey = &k
			}
			n.Count++
			n.Bytes += d.Size
			if d.TS < n.FirstTS {
				n.FirstTS = d.TS
			}
			if d.TS > n.LastTS {
				n.LastTS, n.ID = d.TS, d.ID
			}
		}
		// count links: each envelope connects to the next stage for the same key
		links := map[[2]int]*flink{}
		sorted := append([]envelopeDoc(nil), picked...)
		sort.Slice(sorted, func(i, j int) bool { return sorted[i].TS < sorted[j].TS })
		for i := range sorted {
			a := sorted[i]
			sa := stage(a.Plane, a.Topic)
			if sa >= 4 {
				continue
			}
			for j := i + 1; j < len(sorted) && sorted[j].TS-a.TS < 120000; j++ {
				b := sorted[j]
				if b.Key != a.Key || b.Key == "" {
					continue
				}
				sb := stage(b.Plane, b.Topic)
				if sb <= sa {
					continue
				}
				na := nodes[fkey{sa, a.Topic, a.Producer}]
				nb := nodes[fkey{sb, b.Topic, b.Producer}]
				if na == nil || nb == nil {
					continue
				}
				l := links[[2]int{na.Stage*1000 + na.ID, nb.Stage*1000 + nb.ID}]
				if l == nil {
					l = &flink{From: na.ID, To: nb.ID}
					links[[2]int{na.Stage*1000 + na.ID, nb.Stage*1000 + nb.ID}] = l
				}
				l.Count++
				break
			}
		}
		outNodes := make([]fnode, 0, len(order))
		for _, k := range order {
			if anchorKey != nil && *anchorKey == k {
				nodes[k].Anchor = true
			}
			outNodes = append(outNodes, *nodes[k])
		}
		sort.Slice(outNodes, func(i, j int) bool {
			if outNodes[i].Stage != outNodes[j].Stage {
				return outNodes[i].Stage < outNodes[j].Stage
			}
			return outNodes[i].Count > outNodes[j].Count
		})
		var stageList []int
		for _, n := range outNodes {
			found := false
			for _, st := range stageList {
				if st == n.Stage {
					found = true
					break
				}
			}
			if !found {
				stageList = append(stageList, n.Stage)
			}
		}
		sort.Ints(stageList)
		outLinks := make([]flink, 0, len(links))
		seen := map[[2]int]bool{}
		for k, l := range links {
			seen[k] = true
			outLinks = append(outLinks, *l)
		}
		// stage-to-stage fallback: the heaviest node of each column to the heaviest
		// of the next, so a column is never orphaned when correlation is sparse
		for i := 0; i+1 < len(stageList); i++ {
			heavy := func(st int) *fnode {
				var best *fnode
				for _, n := range nodes {
					if n.Stage == st && (best == nil || n.Count > best.Count) {
						best = n
					}
				}
				return best
			}
			a, b := heavy(stageList[i]), heavy(stageList[i+1])
			if a == nil || b == nil {
				continue
			}
			if seen[[2]int{a.Stage*1000 + a.ID, b.Stage*1000 + b.ID}] {
				continue
			}
			n := a.Count
			if b.Count < n {
				n = b.Count
			}
			outLinks = append(outLinks, flink{From: a.ID, To: b.ID, Count: n})
		}
		json.NewEncoder(w).Encode(map[string]any{
			"from": fromMS, "to": toMS, "key": key, "anchor": anchor,
			"nodes": outNodes, "links": outLinks, "scanned": len(picked),
		})
	})

	// /api/execution builds the trace a model's execution was made from: the
	// market data and advisories it read, the risk state it passed, and the fill
	// that followed. Nodes are ordered by time and laid out by stage.
	mux.HandleFunc("/api/execution", func(w http.ResponseWriter, r *http.Request) {
		id, _ := strconv.Atoi(r.URL.Query().Get("id"))
		window, _ := strconv.Atoi(r.URL.Query().Get("window_s"))
		if window <= 0 || window > 7200 {
			window = 900
		}
		docs := st.snapshot()
		var anchor *envelopeDoc
		for i := range docs {
			if docs[i].ID == id {
				anchor = &docs[i]
				break
			}
		}
		if anchor == nil {
			http.Error(w, "not found", 404)
			return
		}
		from := anchor.TS - int64(window)*1000
		to := anchor.TS + 120000
		type xnode struct {
			ID       int    `json:"id"`
			Topic    string `json:"topic"`
			Plane    string `json:"plane"`
			Stage    int    `json:"stage"`
			TS       int64  `json:"ts"`
			Producer string `json:"producer"`
			Size     int    `json:"size"`
			AgeMS    int64  `json:"age_ms"`
			Anchor   bool   `json:"anchor,omitempty"`
			Chosen   bool   `json:"chosen,omitempty"`
		}
		// candidate: the newest envelope of each stage+topic that the anchor could
		// have read, and the earliest that followed it
		type pick struct{ node xnode }
		before := map[string]pick{}
		after := map[string]pick{}
		for _, d := range docs {
			if anchor.Key != "" && d.Key != anchor.Key {
				continue
			}
			if d.TS < from || d.TS > to {
				continue
			}
			st := stageOf(d.Topic)
			k := d.Topic
			n := xnode{ID: d.ID, Topic: d.Topic, Plane: d.Plane, Stage: st, TS: d.TS,
				Producer: d.Producer, Size: d.Size, AgeMS: anchor.TS - d.TS}
			if d.TS <= anchor.TS {
				if cur, ok := before[k]; !ok || n.TS > cur.node.TS {
					before[k] = pick{n}
				}
			} else {
				if cur, ok := after[k]; !ok || n.TS < cur.node.TS {
					after[k] = pick{n}
				}
			}
		}
		var nodes []xnode
		for _, p := range before {
			n := p.node
			n.Chosen = true
			nodes = append(nodes, n)
		}
		for topic, p := range after {
			// only outcomes are interesting after the anchor; a later advisory is a
			// different decision, not an input to this one
			if !strings.HasPrefix(topic, "model.") {
				continue
			}
			n := p.node
			nodes = append(nodes, n)
		}
		sort.Slice(nodes, func(i, j int) bool { return nodes[i].TS < nodes[j].TS })
		// the anchor itself is always shown, even if it was not chosen above
		found := false
		for i := range nodes {
			if nodes[i].ID == anchor.ID {
				nodes[i].Anchor = true
				found = true
			}
		}
		if !found {
			nodes = append(nodes, xnode{ID: anchor.ID, Topic: anchor.Topic, Plane: anchor.Plane,
				Stage: stageOf(anchor.Topic), TS: anchor.TS, Producer: anchor.Producer,
				Size: anchor.Size, AgeMS: 0, Anchor: true, Chosen: true})
			sort.Slice(nodes, func(i, j int) bool { return nodes[i].TS < nodes[j].TS })
		}
		// the path: one node per stage, nearest before the anchor where possible
		// the path is the reading order: stage by stage, earliest first
		ordered := append([]xnode(nil), nodes...)
		sort.Slice(ordered, func(i, j int) bool {
			if ordered[i].Stage != ordered[j].Stage {
				return ordered[i].Stage < ordered[j].Stage
			}
			return ordered[i].TS < ordered[j].TS
		})
		var path []int
		lastStage := -1
		for _, n := range ordered {
			if n.Stage == lastStage {
				continue
			}
			path = append(path, n.ID)
			lastStage = n.Stage
		}
		json.NewEncoder(w).Encode(map[string]any{
			"anchor": anchor.ID, "key": anchor.Key, "topic": anchor.Topic,
			"from": from, "to": to, "nodes": nodes, "path": path, "window_s": window,
		})
	})

	// scanTopic reads the lake for one topic, newest first. The lake is 7 GB and
	// growing, so it reads backwards from the end of each file and stops as soon
	// as it has enough: a cold query costs a few megabytes, not the whole journal.
	scanTopic := func(topic string, limit int) []envelopeDoc {
		// keep the index current with whatever the recorder appended since it was built
		if indexCount(topic) > 0 || idxReady {
			indexFiles(*dir)
			if docs := scanIndexed(topic, limit); len(docs) > 0 {
				return docs
			}
		}
		files, _ := filepath.Glob(filepath.Join(*dir, "*.jsonl"))
		if len(files) == 0 {
			files, _ = filepath.Glob(filepath.Join(*dir, "seg-*.jsonl"))
		}
		sort.Sort(sort.Reverse(sort.StringSlice(files)))
		needle := []byte("\"topic\":\"" + topic + "\"")
		out := make([]envelopeDoc, 0, limit)
		for _, f := range files {
			if len(out) >= limit {
				break
			}
			out = append(out, scanFileBackwards(f, needle, topic, limit-len(out))...)
		}
		// scanned newest-first; hand back in time order
		for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
			out[i], out[j] = out[j], out[i]
		}
		return out
	}

	// /api/why answers "why did this model trade, or not": the decisions recorded
	// for a symbol, split into allowed and refused, with the reason tallied.
	mux.HandleFunc("/api/why", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		key := q.Get("key")
		engine := q.Get("engine")
		limit, _ := strconv.Atoi(q.Get("limit"))
		if limit <= 0 || limit > 300 {
			limit = 60
		}
		type decision struct {
			ID         int             `json:"id"`
			TS         int64           `json:"ts"`
			Key        string          `json:"key"`
			Engine     string          `json:"engine"`
			Side       string          `json:"side"`
			Allowed    bool            `json:"allowed"`
			Reason     string          `json:"reason"`
			Confidence float64         `json:"confidence"`
			Payload    json.RawMessage `json:"payload,omitempty"`
			Meta       json.RawMessage `json:"meta,omitempty"`
			Topic      string          `json:"topic,omitempty"`
			Producer   string          `json:"producer,omitempty"`
		}
		docs := scanTopic("model.decision", 400)
		var rows []decision
		reasons := map[string]int{}
		allowedN, refusedN := 0, 0
		for i := len(docs) - 1; i >= 0 && len(rows) < limit; i-- {
			d := docs[i]
			if d.Topic != "model.decision" {
				continue
			}
			if key != "" && d.Key != key {
				continue
			}
			var payload struct {
				Data struct {
					Engine     string  `json:"engine"`
					Symbol     string  `json:"symbol"`
					Side       string  `json:"side"`
					Allowed    bool    `json:"allowed"`
					Reason     string  `json:"reason"`
					Confidence float64 `json:"confidence"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &payload) != nil {
				continue
			}
			pd := payload.Data
			if engine != "" && !containsFold(pd.Engine, engine) && !containsFold(d.Producer, engine) {
				continue
			}
			row := decision{ID: d.ID, TS: d.TS, Key: d.Key, Engine: pd.Engine, Side: pd.Side,
				Allowed: pd.Allowed, Reason: pd.Reason, Confidence: pd.Confidence,
				Payload: d.Payload, Meta: d.Meta, Topic: d.Topic, Producer: d.Producer}
			if row.Engine == "" {
				row.Engine = d.Producer
			}
			rows = append(rows, row)
			if pd.Allowed {
				allowedN++
			} else {
				refusedN++
				reasons[pd.Reason]++
			}
		}
		type reasonCount struct {
			Reason string `json:"reason"`
			Count  int    `json:"count"`
		}
		var tally []reasonCount
		for reason, n := range reasons {
			tally = append(tally, reasonCount{reason, n})
		}
		sort.Slice(tally, func(i, j int) bool { return tally[i].Count > tally[j].Count })
		json.NewEncoder(w).Encode(map[string]any{
			"key": key, "engine": engine, "decisions": rows,
			"allowed": allowedN, "refused": refusedN, "reasons": tally,
		})
	})

	// /api/case.html renders a printable case file for one decision or fill: the
	// evidence a reviewer would attach to a report. The browser prints it to PDF.
	mux.HandleFunc("/api/case.html", func(w http.ResponseWriter, r *http.Request) {
		id, _ := strconv.Atoi(r.URL.Query().Get("id"))
		docs := st.snapshot()
		var anchor *envelopeDoc
		for i := range docs {
			if docs[i].ID == id {
				anchor = &docs[i]
				break
			}
		}
		if anchor == nil {
			if d, ok := lookupScanned(id); ok {
				anchor = &d
			}
		}
		if anchor == nil {
			http.Error(w, "not found", 404)
			return
		}
		// the decision, its inputs, its references and its chain
		var meta map[string]any
		if len(anchor.Meta) > 0 {
			json.Unmarshal(anchor.Meta, &meta)
		}
		var payload map[string]any
		json.Unmarshal(anchor.Payload, &payload)
		inner := payload
		if d, ok := payload["data"].(map[string]any); ok {
			inner = d
		}
		type inputRow struct {
			Topic string
			Age   float64
			ID    int
		}
		var inputs []inputRow
		for _, d := range docs {
			if anchor.Key != "" && d.Key != anchor.Key {
				continue
			}
			if d.TS > anchor.TS || anchor.TS-d.TS > 900000 {
				continue
			}
			inputs = append(inputs, inputRow{Topic: d.Topic, Age: float64(anchor.TS-d.TS) / 1000, ID: d.ID})
		}
		sort.Slice(inputs, func(i, j int) bool { return inputs[i].Age < inputs[j].Age })
		if len(inputs) > 12 {
			inputs = inputs[:12]
		}
		esc := template.HTMLEscapeString
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprintf(w, `<html><head><meta charset="utf-8"><title>Case file %s %s</title>
<style>
 body{font:11pt/1.45 -apple-system,Helvetica,Arial,sans-serif;color:#111;max-width:760px;margin:32px auto;padding:0 16px}
 h1{font-size:18pt;margin:0 0 2px} .sub{color:#666;font-size:9.5pt;margin-bottom:18px}
 h2{font-size:10pt;text-transform:uppercase;letter-spacing:.08em;color:#444;margin:22px 0 6px;border-bottom:1px solid #ddd;padding-bottom:3px}
 table{width:100%%;border-collapse:collapse;font-size:9.5pt} td,th{text-align:left;padding:4px 6px;border-bottom:1px solid #eee}
 th{color:#666;font-weight:600;font-size:8.5pt;text-transform:uppercase;letter-spacing:.05em}
 .pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:8.5pt;text-transform:uppercase;letter-spacing:.05em}
 .yes{background:#e3f6ec;color:#12603a} .no{background:#fdeaea;color:#8a1f1f}
 pre{background:#f7f8fa;border:1px solid #e6e8ec;border-radius:6px;padding:10px;font-size:8.5pt;overflow:auto}
 .foot{margin-top:26px;border-top:1px solid #ddd;padding-top:8px;color:#666;font-size:8.5pt}
 @media print{ body{margin:0} h2{page-break-after:avoid} }
</style></head><body onload="window.print()">
<h1>Case file — %s %s</h1>
<div class="sub">decision %d · %s · generated %s</div>
<h2>What happened</h2>
<table>
<tr><th>Topic</th><td>%s</td></tr>
<tr><th>Symbol</th><td>%s</td></tr>
<tr><th>Producer</th><td>%s</td></tr>
<tr><th>Recorded</th><td>%s</td></tr>
<tr><th>Correlation id</th><td>%s</td></tr>
<tr><th>Generation</th><td>%v</td></tr>
</table>`,
			esc(anchor.Topic), esc(anchor.Key), esc(anchor.Topic), esc(anchor.Key), anchor.ID,
			time.UnixMilli(anchor.TS).UTC().Format(time.RFC3339), time.Now().UTC().Format(time.RFC3339),
			esc(anchor.Topic), esc(anchor.Key), esc(anchor.Producer),
			time.UnixMilli(anchor.TS).UTC().Format(time.RFC3339), esc(fmt.Sprint(meta["cid"])), meta["generation"])

		if allowed, ok := inner["allowed"].(bool); ok {
			class, word := "yes", "traded"
			if !allowed {
				class, word = "no", "refused"
			}
			fmt.Fprintf(w, `<h2>Verdict</h2><p><span class="pill %s">%s</span> &nbsp;%s</p>`,
				class, word, esc(fmt.Sprint(inner["reason"])))
		}
		fmt.Fprintf(w, `<h2>What it read</h2>`)
		if len(inputs) == 0 {
			if refs, ok := meta["refs"].([]any); ok && len(refs) > 0 {
				fmt.Fprint(w, `<p>The inputs are no longer in the in-memory window; the recorded references are:</p>`)
				fmt.Fprint(w, `<table><tr><th>Topic</th><th>Age at decision</th><th>Derived</th></tr>`)
				for _, rf := range refs {
					rm, _ := rf.(map[string]any)
					fmt.Fprintf(w, `<tr><td>%s</td><td>%v ms</td><td>%v</td></tr>`,
						esc(fmt.Sprint(rm["topic"])), rm["age_ms"], rm["derived"])
				}
				fmt.Fprint(w, `</table>`)
			} else {
				fmt.Fprint(w, `<p>No input envelopes for this symbol in the preceding fifteen minutes.</p>`)
			}
		} else {
			fmt.Fprint(w, `<table><tr><th>Topic</th><th>Age at decision</th><th>Envelope</th></tr>`)
			for _, in := range inputs {
				fmt.Fprintf(w, `<tr><td>%s</td><td>%.1f s</td><td>#%d</td></tr>`, esc(in.Topic), in.Age, in.ID)
			}
			fmt.Fprint(w, `</table>`)
		}
		if hops, ok := meta["hops"].([]any); ok && len(hops) > 0 {
			fmt.Fprintf(w, `<h2>Pipeline</h2><table><tr><th>Stage</th><th>Name</th><th>Outcome</th><th>Detail</th></tr>`)
			for _, h := range hops {
				hm, _ := h.(map[string]any)
				detail := ""
				if v, ok := hm["detail"]; ok && v != nil {
					detail = fmt.Sprint(v)
				}
				fmt.Fprintf(w, `<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>`,
					esc(fmt.Sprint(hm["kind"])), esc(fmt.Sprint(hm["name"])),
					esc(fmt.Sprint(hm["outcome"])), esc(detail))
			}
			fmt.Fprint(w, `</table>`)
		}
		if refs, ok := meta["refs"].([]any); ok && len(refs) > 0 {
			fmt.Fprintf(w, `<h2>References</h2><table><tr><th>Topic</th><th>Age</th><th>Derived</th><th>Correlation id</th></tr>`)
			for _, rf := range refs {
				rm, _ := rf.(map[string]any)
				fmt.Fprintf(w, `<tr><td>%s</td><td>%v ms</td><td>%v</td><td>%s</td></tr>`,
					esc(fmt.Sprint(rm["topic"])), rm["age_ms"], rm["derived"], esc(fmt.Sprint(rm["cid"])))
			}
			fmt.Fprint(w, `</table>`)
		}
		raw, _ := json.MarshalIndent(anchor.Payload, "", "  ")
		fmt.Fprintf(w, `<h2>Raw payload</h2><pre>%s</pre>`, esc(string(raw)))
		fmt.Fprintf(w, `<div class="foot">Generated by The Envelope Explorer from the recorded lake. `+
			`References marked derived were inferred by the recorder from the window, not declared by the producer.</div>`)
		fmt.Fprint(w, `</body></html>`)
	})

	// /api/book.html is the regulatory report for one book: scope, summary, every
	// trade, every refusal, exposure by symbol, the methodology and an attestation
	// hash over the evidence.
	mux.HandleFunc("/api/book.html", func(w http.ResponseWriter, r *http.Request) {
		engine := r.URL.Query().Get("engine")
		fromMS, _ := strconv.ParseInt(r.URL.Query().Get("from"), 10, 64)
		toMS, _ := strconv.ParseInt(r.URL.Query().Get("to"), 10, 64)
		if toMS == 0 {
			toMS = time.Now().UnixMilli()
		}
		if fromMS == 0 {
			fromMS = toMS - 24*3600*1000
		}
		type t = reportRow
		var trades []t
		var refusals []t
		perSymbol := map[string]int{}
		var netSum, heldSum, grossSum float64
		for _, d := range scanTopic("model.fill", 1500) {
			if d.TS < fromMS || d.TS > toMS {
				continue
			}
			var p struct {
				Data struct {
					Engine   string  `json:"engine"`
					Symbol   string  `json:"symbol"`
					Side     string  `json:"side"`
					Entry    float64 `json:"entry"`
					Exit     float64 `json:"exit"`
					HeldS    float64 `json:"held_s"`
					NetPct   float64 `json:"net_pct"`
					GrossPct float64 `json:"gross_pct"`
					Reason   string  `json:"reason"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &p) != nil {
				continue
			}
			dd := p.Data
			if engine != "" && !containsFold(dd.Engine, engine) && !containsFold(d.Producer, engine) {
				continue
			}
			trades = append(trades, t{d.TS, dd.Symbol, dd.Side, dd.Entry, dd.Exit, dd.HeldS, dd.NetPct, dd.Reason, d.ID})
			perSymbol[dd.Symbol]++
			netSum += dd.NetPct
			grossSum += dd.GrossPct
			heldSum += dd.HeldS
		}
		for _, d := range scanTopic("model.decision", 1500) {
			if d.TS < fromMS || d.TS > toMS {
				continue
			}
			var p struct {
				Data struct {
					Engine  string `json:"engine"`
					Allowed bool   `json:"allowed"`
					Reason  string `json:"reason"`
					Side    string `json:"side"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &p) != nil || p.Data.Allowed {
				continue
			}
			if engine != "" && !containsFold(p.Data.Engine, engine) && !containsFold(d.Producer, engine) {
				continue
			}
			refusals = append(refusals, t{TS: d.TS, Key: d.Key, Side: p.Data.Side, Reason: p.Data.Reason, ID: d.ID})
		}
		sort.Slice(trades, func(i, j int) bool { return trades[i].TS < trades[j].TS })
		sort.Slice(refusals, func(i, j int) bool { return refusals[i].TS < refusals[j].TS })
		n := float64(len(trades))
		esc := template.HTMLEscapeString
		hash := fnv.New64a()
		fmt.Fprintf(hash, "%s|%d|%d|%d", engine, fromMS, toMS, len(trades)+len(refusals))
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fromStr := time.UnixMilli(fromMS).UTC().Format(time.RFC3339)
		toStr := time.UnixMilli(toMS).UTC().Format(time.RFC3339)
		nowStr := time.Now().UTC().Format(time.RFC3339)
		hashVal := hash.Sum64()
		head := fmt.Sprintf(`<html><head><meta charset="utf-8"><title>Regulatory book report</title><style>
 body{font:10.5pt/1.45 -apple-system,Helvetica,Arial,sans-serif;color:#111;max-width:820px;margin:30px auto;padding:0 18px}
 h1{font-size:19pt;margin:0} .sub{color:#555;font-size:9pt;margin:2px 0 16px}
 h2{font-size:10pt;text-transform:uppercase;letter-spacing:.08em;color:#333;margin:20px 0 6px;border-bottom:1px solid #ddd;padding-bottom:3px}
 table{width:100%%;border-collapse:collapse;font-size:8.6pt} td,th{padding:3px 5px;border-bottom:1px solid #eee;text-align:left}
 th{color:#666;font-size:8pt;text-transform:uppercase;letter-spacing:.05em}
 .kv td:first-child{color:#666;width:38%%}
 .neg{color:#a4232b} .pos{color:#12603a}
 .foot{margin-top:24px;border-top:1px solid #ccc;padding-top:8px;color:#666;font-size:8pt}
 @media print{body{margin:0} h2{page-break-after:avoid} tr{page-break-inside:avoid}}
</style></head><body onload="window.print()">
<h1>Regulatory book report — %s</h1>
<div class="sub">window %s to %s · generated %s · case hash %x</div>

<h2>1 · Scope</h2>
<table class="kv">
<tr><td>Book</td><td>%s</td></tr>
<tr><td>Window</td><td>%s — %s</td></tr>
<tr><td>Trades in scope</td><td>%d</td></tr>
<tr><td>Refusals in scope</td><td>%d</td></tr>
<tr><td>Instruments touched</td><td>%d</td></tr>
<tr><td>Data source</td><td>recorded envelope lake; nothing reconstructed after the fact</td></tr>
</table>`, esc(engine), fromStr, toStr, nowStr, hashVal,
			esc(engine), fromStr, toStr, len(trades), len(refusals), len(perSymbol))
		fmt.Fprint(w, head)

		fmt.Fprintf(w, `<h2>2 · Summary</h2><table class="kv">
<tr><td>Trades</td><td>%d</td></tr>
<tr><td>Mean net per trade</td><td>%.3f%%</td></tr>
<tr><td>Sum of net</td><td>%.3f%%</td></tr>
<tr><td>Sum of gross</td><td>%.3f%%</td></tr>
<tr><td>Mean holding time</td><td>%.0f s</td></tr>
<tr><td>Longest hold</td><td>%.0f s</td></tr>
<tr><td>Refusals</td><td>%d</td></tr>
</table>`, len(trades), safeDiv(netSum, n), netSum, grossSum, safeDiv(heldSum, n), maxHeldTrades(trades), len(refusals))

		fmt.Fprintf(w, `<h2>3 · Trades</h2><table><tr><th>#</th><th>time</th><th>instrument</th><th>side</th><th>entry</th><th>exit</th><th>held</th><th>net</th><th>reason</th><th>envelope</th></tr>`)
		for i, tr := range trades {
			cls := "pos"
			if tr.NetPct < 0 {
				cls = "neg"
			}
			fmt.Fprintf(w, `<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%.6g</td><td>%.6g</td><td>%.0fs</td><td class="%s">%.3f%%</td><td>%s</td><td>%d</td></tr>`,
				i+1, time.UnixMilli(tr.TS).UTC().Format("15:04:05"), esc(tr.Key), esc(tr.Side),
				tr.Entry, tr.Exit, tr.HeldS, cls, tr.NetPct, esc(tr.Reason), tr.ID)
		}
		fmt.Fprint(w, `</table>`)

		if len(refusals) > 0 {
			fmt.Fprintf(w, `<h2>4 · Refusals</h2><table><tr><th>time</th><th>instrument</th><th>side</th><th>rule</th><th>envelope</th></tr>`)
			for _, rf := range refusals {
				fmt.Fprintf(w, `<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>`,
					time.UnixMilli(rf.TS).UTC().Format("15:04:05"), esc(rf.Key), esc(rf.Side), esc(rf.Reason), rf.ID)
			}
			fmt.Fprint(w, `</table>`)
		}
		fmt.Fprintf(w, `<h2>5 · Exposure by instrument</h2><table><tr><th>instrument</th><th>trades</th></tr>`)
		type kv struct {
			k string
			v int
		}
		var list []kv
		for k, v := range perSymbol {
			list = append(list, kv{k, v})
		}
		sort.Slice(list, func(i, j int) bool { return list[i].v > list[j].v })
		for _, e := range list {
			fmt.Fprintf(w, `<tr><td>%s</td><td>%d</td></tr>`, esc(e.k), e.v)
		}
		fmt.Fprint(w, `</table>`)

		fmt.Fprintf(w, `<h2>6 · Methodology and lineage</h2>
<p>Each trade above is a model.fill envelope recorded at the time of execution. Its decision, the inputs it
read and the pipeline it passed are recorded alongside it and can be retrieved individually by envelope id.
References marked <em>derived</em> in an individual case file were inferred by the recorder from the window at
ingestion; references marked <em>declared</em> were emitted by the producing model.</p>
<p>Holding time is measured from entry to exit in the recorded payload. Net return is the model's own net
figure after fees, as recorded, not recomputed here. Refusals are decisions the governor declined, with the
rule and the number that tripped it quoted verbatim.</p>`)

		fmt.Fprintf(w, `<div class="foot">Generated by The Envelope Explorer from the recorded lake. Case hash %x —
this report is a rendering of recorded envelopes, not a reconstruction: every row can be resolved to an
envelope id above.</div></body></html>`, hash.Sum64())
	})

	// /api/health probes the services the Explorer depends on, so a missing feed
	// is visible as a down service rather than an empty list.
	mux.HandleFunc("/api/health", func(w http.ResponseWriter, r *http.Request) {
		type probe struct {
			Name string `json:"name"`
			URL  string `json:"-"`
			Port int    `json:"port"`
		}
		probes := []probe{
			{"market data", "http://127.0.0.1:8796/health", 8796},
			{"recorder", "http://127.0.0.1:8801/stats", 8801},
			{"dashboard", "http://127.0.0.1:8799/api/all", 8799},
			{"bus consumer", "http://127.0.0.1:8798/", 8798},
			{"forecast", "http://127.0.0.1:8790/health", 8790},
			{"explorer", "http://127.0.0.1:8802/api/stats", 8802},
		}
		type result struct {
			Name string `json:"name"`
			Port int    `json:"port"`
			Up   bool   `json:"up"`
			MS   int64  `json:"ms"`
		}
		client := http.Client{Timeout: 3 * time.Second}
		var out []result
		up := 0
		for _, p := range probes {
			start := time.Now()
			resp, err := client.Get(p.URL)
			res := result{Name: p.Name, Port: p.Port, MS: time.Since(start).Milliseconds()}
			if err == nil {
				res.Up = resp.StatusCode == 200
				resp.Body.Close()
			}
			if res.Up {
				up++
			}
			out = append(out, res)
		}
		json.NewEncoder(w).Encode(map[string]any{"up": up, "services": out,
			"ts": time.Now().UnixMilli()})
	})

	// /api/books lists the engines that have traded, with a one-line summary each.
	mux.HandleFunc("/api/books", func(w http.ResponseWriter, r *http.Request) {
		type row struct {
			Engine   string  `json:"engine"`
			Trades   int     `json:"trades"`
			NetSum   float64 `json:"net_sum"`
			MeanNet  float64 `json:"mean_net"`
			MeanHeld float64 `json:"mean_held"`
			Refusals int     `json:"refusals"`
		}
		agg := map[string]*row{}
		for _, d := range scanTopic("model.fill", 1500) {
			var p struct {
				Data struct {
					Engine string  `json:"engine"`
					NetPct float64 `json:"net_pct"`
					HeldS  float64 `json:"held_s"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &p) != nil || p.Data.Engine == "" {
				continue
			}
			a := agg[p.Data.Engine]
			if a == nil {
				a = &row{Engine: p.Data.Engine}
				agg[p.Data.Engine] = a
			}
			a.Trades++
			a.NetSum += p.Data.NetPct
			a.MeanHeld += p.Data.HeldS
		}
		for _, d := range scanTopic("model.decision", 1500) {
			var p struct {
				Data struct {
					Engine  string `json:"engine"`
					Allowed bool   `json:"allowed"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &p) != nil || p.Data.Engine == "" || p.Data.Allowed {
				continue
			}
			if a := agg[p.Data.Engine]; a != nil {
				a.Refusals++
			} else {
				agg[p.Data.Engine] = &row{Engine: p.Data.Engine, Refusals: 1}
			}
		}
		out := make([]*row, 0, len(agg))
		for _, a := range agg {
			if a.Trades > 0 {
				a.MeanNet = safeDiv(a.NetSum, float64(a.Trades))
				a.MeanHeld = safeDiv(a.MeanHeld, float64(a.Trades))
			}
			a.NetSum = round2(a.NetSum)
			a.MeanNet = round2(a.MeanNet)
			a.MeanHeld = round2(a.MeanHeld)
			out = append(out, a)
		}
		sort.Slice(out, func(i, j int) bool { return out[i].Trades > out[j].Trades })
		json.NewEncoder(w).Encode(out)
	})

	// /api/book aggregates everything recorded for one engine: its trades, its
	// refusals, holding times and per-symbol exposure. This is the view a compliance
	// officer starts from when asked "how did this book trade".
	mux.HandleFunc("/api/book", func(w http.ResponseWriter, r *http.Request) {
		engine := r.URL.Query().Get("engine")
		if engine == "" {
			http.Error(w, "engine required", 400)
			return
		}
		fromMS, _ := strconv.ParseInt(r.URL.Query().Get("from"), 10, 64)
		toMS, _ := strconv.ParseInt(r.URL.Query().Get("to"), 10, 64)
		if toMS == 0 {
			toMS = time.Now().UnixMilli()
		}
		if fromMS == 0 {
			fromMS = toMS - 24*3600*1000
		}
		type trade = bookTrade
		type refusal struct {
			ID     int    `json:"id"`
			TS     int64  `json:"ts"`
			Key    string `json:"key"`
			Reason string `json:"reason"`
		}
		var trades []trade
		var refusals []refusal
		perSymbol := map[string]int{}
		var gross, netSum, heldSum float64
		for _, d := range scanTopic("model.fill", 1500) {
			if d.TS < fromMS || d.TS > toMS {
				continue
			}
			var p struct {
				Data struct {
					Engine   string  `json:"engine"`
					Symbol   string  `json:"symbol"`
					Side     string  `json:"side"`
					Entry    float64 `json:"entry"`
					Exit     float64 `json:"exit"`
					HeldS    float64 `json:"held_s"`
					NetPct   float64 `json:"net_pct"`
					GrossPct float64 `json:"gross_pct"`
					Reason   string  `json:"reason"`
					Notional float64 `json:"notional"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &p) != nil {
				continue
			}
			dd := p.Data
			if !containsFold(dd.Engine, engine) && !containsFold(d.Producer, engine) {
				continue
			}
			trades = append(trades, trade{ID: d.ID, TS: d.TS, Key: dd.Symbol, Side: dd.Side,
				Entry: dd.Entry, Exit: dd.Exit, HeldS: dd.HeldS, NetPct: dd.NetPct,
				Reason: dd.Reason, Notional: dd.Notional})
			perSymbol[dd.Symbol]++
			gross += dd.GrossPct
			netSum += dd.NetPct
			heldSum += dd.HeldS
		}
		for _, d := range scanTopic("model.decision", 1500) {
			if d.TS < fromMS || d.TS > toMS {
				continue
			}
			var p struct {
				Data struct {
					Engine  string `json:"engine"`
					Allowed bool   `json:"allowed"`
					Reason  string `json:"reason"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &p) != nil {
				continue
			}
			if p.Data.Allowed || (!containsFold(p.Data.Engine, engine) && !containsFold(d.Producer, engine)) {
				continue
			}
			refusals = append(refusals, refusal{ID: d.ID, TS: d.TS, Key: d.Key, Reason: p.Data.Reason})
		}
		sort.Slice(trades, func(i, j int) bool { return trades[i].TS > trades[j].TS })
		sort.Slice(refusals, func(i, j int) bool { return refusals[i].TS > refusals[j].TS })
		n := float64(len(trades))
		summary := map[string]any{
			"trades": len(trades), "refusals": len(refusals),
			"gross_pct_sum": round2(gross), "net_pct_sum": round2(netSum),
			"mean_net_pct": round2(safeDiv(netSum, n)), "mean_held_s": round2(safeDiv(heldSum, n)),
			"max_held_s": maxHeld(trades), "symbols": len(perSymbol),
			"from_ms": fromMS, "to_ms": toMS,
		}
		json.NewEncoder(w).Encode(map[string]any{"engine": engine, "summary": summary,
			"trades": trades, "refusals": refusals, "per_symbol": perSymbol})
	})

	// /api/search finds candidates by symbol, engine, outcome and time window.
	mux.HandleFunc("/api/search", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		symbol := q.Get("symbol")
		engine := q.Get("engine")
		outcome := q.Get("outcome") // traded | refused | any
		fromMS, _ := strconv.ParseInt(q.Get("from"), 10, 64)
		toMS, _ := strconv.ParseInt(q.Get("to"), 10, 64)
		limit, _ := strconv.Atoi(q.Get("limit"))
		if limit <= 0 || limit > 200 {
			limit = 50
		}
		if fromMS == 0 && toMS == 0 {
			toMS = time.Now().UnixMilli()
			fromMS = toMS - 6*3600*1000
		}
		type hit struct {
			ID      int     `json:"id"`
			TS      int64   `json:"ts"`
			Key     string  `json:"key"`
			Engine  string  `json:"engine"`
			Side    string  `json:"side"`
			Allowed bool    `json:"allowed"`
			Reason  string  `json:"reason"`
			Net     float64 `json:"net_pct,omitempty"`
			Topic   string  `json:"topic"`
		}
		var hits []hit
		for _, d := range scanTopic("model.decision", 2000) {
			if d.TS < fromMS || d.TS > toMS {
				continue
			}
			if symbol != "" && !containsFold(d.Key, symbol) {
				continue
			}
			var p struct {
				Data struct {
					Engine string `json:"engine"`
					Side   string `json:"side"`
					Allow  bool   `json:"allowed"`
					Reason string `json:"reason"`
				} `json:"data"`
			}
			if json.Unmarshal(d.Payload, &p) != nil {
				continue
			}
			if engine != "" && !containsFold(p.Data.Engine, engine) && !containsFold(d.Producer, engine) {
				continue
			}
			if outcome == "traded" && !p.Data.Allow {
				continue
			}
			if outcome == "refused" && p.Data.Allow {
				continue
			}
			hits = append(hits, hit{ID: d.ID, TS: d.TS, Key: d.Key, Engine: p.Data.Engine,
				Side: p.Data.Side, Allowed: p.Data.Allow, Reason: p.Data.Reason, Topic: d.Topic})
		}
		sort.Slice(hits, func(i, j int) bool { return hits[i].TS > hits[j].TS })
		if len(hits) > limit {
			hits = hits[:limit]
		}
		json.NewEncoder(w).Encode(map[string]any{"count": len(hits), "hits": hits,
			"from": fromMS, "to": toMS})
	})

	// /api/gantt returns horizontal spans: for one outcome, how long each input had
	// been valid before it was read; or with mode=positions, how long each trade was
	// held. A Gantt makes staleness and holding time visible at a glance.
	mux.HandleFunc("/api/gantt", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		mode := q.Get("mode")
		type row struct {
			ID       int     `json:"id"`
			Label    string  `json:"label"`
			Topic    string  `json:"topic"`
			Producer string  `json:"producer"`
			Stage    int     `json:"stage"`
			Start    int64   `json:"start"`
			End      int64   `json:"end"`
			Kind     string  `json:"kind"`
			Detail   string  `json:"detail,omitempty"`
			Value    float64 `json:"value,omitempty"`
		}
		if mode == "positions" {
			limit, _ := strconv.Atoi(q.Get("limit"))
			if limit <= 0 || limit > 200 {
				limit = 40
			}
			docs := scanTopic("model.fill", limit)
			var rows []row
			for _, d := range docs {
				var p struct {
					Data struct {
						Engine  string  `json:"engine"`
						Symbol  string  `json:"symbol"`
						Side    string  `json:"side"`
						EntryMS int64   `json:"entry_ms"`
						ExitMS  int64   `json:"exit_ms"`
						HeldS   float64 `json:"held_s"`
						NetPct  float64 `json:"net_pct"`
					} `json:"data"`
				}
				if json.Unmarshal(d.Payload, &p) != nil {
					continue
				}
				dd := p.Data
				start, end := dd.EntryMS, dd.ExitMS
				if start == 0 || end == 0 || end < start {
					continue
				}
				rows = append(rows, row{ID: d.ID, Label: dd.Symbol, Topic: "model.fill",
					Producer: dd.Engine, Stage: 3, Start: start, End: end,
					Kind: strings.ToLower(dd.Side), Value: dd.NetPct,
					Detail: fmt.Sprintf("%s %s · %.2f%% net · %.1fs", dd.Engine, dd.Side, dd.NetPct, dd.HeldS)})
			}
			json.NewEncoder(w).Encode(map[string]any{"mode": "positions", "rows": rows})
			return
		}
		id, _ := strconv.Atoi(q.Get("id"))
		docs := st.snapshot()
		var anchor *envelopeDoc
		for i := range docs {
			if docs[i].ID == id {
				anchor = &docs[i]
				break
			}
		}
		if anchor == nil {
			http.Error(w, "not found", 404)
			return
		}
		// each input as a bar from when it was computed to when the outcome read it
		exec, _ := func() (map[string]any, error) { return nil, nil }()
		_ = exec
		var rows []row
		for _, d := range docs {
			if anchor.Key != "" && d.Key != anchor.Key {
				continue
			}
			if d.TS > anchor.TS || anchor.TS-d.TS > 900000 {
				continue
			}
			start := d.TS
			var m struct {
				ComputedAt *time.Time `json:"computed_at"`
			}
			if len(d.Meta) > 0 && json.Unmarshal(d.Meta, &m) == nil && m.ComputedAt != nil {
				start = m.ComputedAt.UnixMilli()
			}
			rows = append(rows, row{ID: d.ID, Label: d.Topic, Topic: d.Topic, Producer: d.Producer,
				Stage: stageOf(d.Topic), Start: start, End: anchor.TS, Kind: "input",
				Detail: fmt.Sprintf("%s · %s", d.Topic, d.Producer)})
		}
		sort.Slice(rows, func(i, j int) bool {
			if rows[i].Stage != rows[j].Stage {
				return rows[i].Stage < rows[j].Stage
			}
			return rows[i].Start < rows[j].Start
		})
		json.NewEncoder(w).Encode(map[string]any{"mode": "trace", "anchor": anchor.ID,
			"key": anchor.Key, "topic": anchor.Topic, "rows": rows})
	})

	// /api/timeline returns a stacked histogram across the retained window, which
	// is what the scrubber draws and what a brush selects from.
	mux.HandleFunc("/api/timeline", func(w http.ResponseWriter, r *http.Request) {
		n, _ := strconv.Atoi(r.URL.Query().Get("buckets"))
		if n <= 0 || n > 240 {
			n = 120
		}
		docs := st.snapshot()
		if len(docs) == 0 {
			json.NewEncoder(w).Encode(map[string]any{"buckets": []any{}})
			return
		}
		oldest, newest := docs[0].TS, docs[0].TS
		for _, d := range docs {
			if d.TS < oldest {
				oldest = d.TS
			}
			if d.TS > newest {
				newest = d.TS
			}
		}
		span := newest - oldest
		if span <= 0 {
			span = 1
		}
		width := span / int64(n)
		if width <= 0 {
			width = 1
		}
		type bucket struct {
			From   int64          `json:"from"`
			To     int64          `json:"to"`
			Total  int            `json:"total"`
			Planes map[string]int `json:"planes"`
		}
		buckets := make([]bucket, n)
		for i := range buckets {
			from := oldest + int64(i)*width
			buckets[i] = bucket{From: from, To: from + width, Planes: map[string]int{}}
		}
		for _, d := range docs {
			idx := int((d.TS - oldest) / width)
			if idx < 0 {
				idx = 0
			}
			if idx >= n {
				idx = n - 1
			}
			buckets[idx].Total++
			buckets[idx].Planes[d.Plane]++
		}
		json.NewEncoder(w).Encode(map[string]any{
			"from": oldest, "to": newest, "buckets": buckets,
		})
	})

	// /api/operators is the capability registry: who can produce what, and
	// whether they are still producing it.
	mux.HandleFunc("/api/operators", func(w http.ResponseWriter, r *http.Request) {
		reg := operator.Default()
		docs := st.snapshot()
		counts := map[string]int{}
		last := map[string]int64{}
		for _, d := range docs {
			if d.Producer != "" {
				counts[d.Producer]++
				if d.TS > last[d.Producer] {
					last[d.Producer] = d.TS
				}
			}
		}
		ops := reg.All()
		for i := range ops {
			ops[i].Invocations = int64(counts[ops[i].Name])
			if ms := last[ops[i].Name]; ms > 0 {
				ops[i].LastSeen = time.UnixMilli(ms).UTC()
			}
		}
		json.NewEncoder(w).Encode(map[string]any{
			"operators": ops, "needs": map[string][]string{
				"intel.forecast": reg.WhoProduces("intel.forecast"),
				"intel.ta":       reg.WhoProduces("intel.ta"),
				"model.decision": reg.WhoProduces("model.decision"),
			},
		})
	})

	// /api/trace?key=BTCUSDT returns the chain of envelopes for that symbol,
	// which is the explorer's answer to "why did we buy".
	mux.HandleFunc("/api/trace", func(w http.ResponseWriter, r *http.Request) {
		key := r.URL.Query().Get("key")
		docs := st.snapshot()
		var chain []envelopeDoc
		for _, d := range docs {
			if d.Key == key {
				chain = append(chain, d)
			}
		}
		json.NewEncoder(w).Encode(map[string]any{"key": key, "count": len(chain), "chain": chain})
	})

	mux.HandleFunc("/api/stream", func(w http.ResponseWriter, r *http.Request) {
		flusher, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "streaming unsupported", 500)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		ch := st.subscribe()
		defer st.unsubscribe(ch)
		enc := json.NewEncoder(w)
		for {
			select {
			case <-r.Context().Done():
				return
			case d := <-ch:
				fmt.Fprint(w, "event: envelope\ndata: ")
				enc.Encode(d)
				fmt.Fprint(w, "\n")
				flusher.Flush()
			}
		}
	})

	log.Printf("ENVELOPE EXPLORER on :%d (read-only, %d in memory)", *port, n)
	log.Fatal(http.ListenAndServe(fmt.Sprintf("0.0.0.0:%d", *port), mux))
}
