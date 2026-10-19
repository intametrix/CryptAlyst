// envelopeapi serves the Envelope Explorer: a read-only window onto the lake that
// lets an operator filter, inspect and trace every message the platform emitted.
package main

import (
	"bufio"
	"embed"
	"encoding/json"
	"flag"
	"fmt"
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
				if d.Key == "" {
					d.Key = m.Key
				}
			}
		}
	}
	if d.Plane == "" {
		d.Plane = "unknown"
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

func loadFile(path string, st *store) int {
	f, err := os.Open(path)
	if err != nil {
		return 0
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 32<<20)
	n := 0
	for sc.Scan() {
		if d, ok := parse(sc.Bytes()); ok {
			st.add(d)
			n++
		}
	}
	return n
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
		for _, d := range st.snapshot() {
			if d.ID == id {
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
		docs := st.snapshot()
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
			ID      int    `json:"id"`
			Topic   string `json:"topic"`
			Plane   string `json:"plane"`
			Stage   int    `json:"stage"`
			Count   int    `json:"count"`
			Bytes   int    `json:"bytes"`
			FirstTS int64  `json:"first_ts"`
			LastTS  int64  `json:"last_ts"`
			Key     string `json:"key"`
		}
		type flink struct {
			From  int `json:"from"`
			To    int `json:"to"`
			Count int `json:"count"`
		}
		type fkey struct {
			stage int
			topic string
		}
		nodes := map[fkey]*fnode{}
		var order []fkey
		var picked []envelopeDoc
		for _, d := range docs {
			if d.TS < fromMS || d.TS > toMS {
				continue
			}
			if key != "" && d.Key != key {
				continue
			}
			picked = append(picked, d)
			k := fkey{stage(d.Plane, d.Topic), d.Topic}
			n, ok := nodes[k]
			if !ok {
				n = &fnode{Topic: d.Topic, Plane: d.Plane, Stage: k.stage, FirstTS: d.TS, LastTS: d.TS, Key: d.Key}
				nodes[k] = n
				order = append(order, k)
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
				na := nodes[fkey{sa, a.Topic}]
				nb := nodes[fkey{sb, b.Topic}]
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
			"from": fromMS, "to": toMS, "key": key,
			"nodes": outNodes, "links": outLinks, "scanned": len(picked),
		})
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
