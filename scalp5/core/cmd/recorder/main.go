// recorder is the persistence plane: one consumer, every envelope, written once
// to the lake. It is a consumer by design — the feed never waits for a disk write.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"cryptalyst/core/internal/busadmin"
	"cryptalyst/core/internal/correlation"
	"cryptalyst/core/internal/envelope"
	"cryptalyst/core/internal/lake"
	"cryptalyst/core/internal/stomp"
	"cryptalyst/core/internal/store"
	"cryptalyst/core/internal/topics"
)

// seq assigns a monotonic generation per topic, so an envelope can say which
// revision of that topic's stream it belongs to.
var (
	seqMu sync.Mutex
	seq   = map[string]uint64{}
)

func nextGen(topic string) uint64 {
	seqMu.Lock()
	defer seqMu.Unlock()
	seq[topic]++
	return seq[topic]
}

// hub fans recorded envelopes out to readers (the explorer, dashboards). The
// bus is consumed once, here; readers attach to this instead of the broker.
type hub struct {
	mu   sync.RWMutex
	subs map[chan []byte]bool
}

func newHub() *hub { return &hub{subs: map[chan []byte]bool{}} }

func (h *hub) join() chan []byte {
	ch := make(chan []byte, 128)
	h.mu.Lock()
	h.subs[ch] = true
	h.mu.Unlock()
	return ch
}

func (h *hub) leave(ch chan []byte) {
	h.mu.Lock()
	delete(h.subs, ch)
	h.mu.Unlock()
	close(ch)
}

func (h *hub) send(payload []byte) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	for ch := range h.subs {
		select {
		case ch <- payload:
		default:
		}
	}
}

type counters struct {
	received  int64
	written   int64
	refused   int64
	errors    int64
	byTopic   map[string]int64
	mu        sync.Mutex
	lastMsgMs int64
	started   time.Time
}

func (c *counters) bump(topic string, err error) {
	atomic.AddInt64(&c.received, 1)
	atomic.StoreInt64(&c.lastMsgMs, time.Now().UnixMilli())
	c.mu.Lock()
	c.byTopic[topic]++
	c.mu.Unlock()
	if err != nil {
		atomic.AddInt64(&c.errors, 1)
	} else {
		atomic.AddInt64(&c.written, 1)
	}
}

func (c *counters) snapshot() map[string]any {
	c.mu.Lock()
	by := map[string]int64{}
	for k, v := range c.byTopic {
		by[k] = v
	}
	c.mu.Unlock()
	elapsed := time.Since(c.started).Seconds()
	recv := atomic.LoadInt64(&c.received)
	rate := 0.0
	if elapsed > 0 {
		rate = float64(recv) / elapsed
	}
	return map[string]any{
		"received": recv, "written": atomic.LoadInt64(&c.written),
		"refused": atomic.LoadInt64(&c.refused),
		"errors":  atomic.LoadInt64(&c.errors), "by_topic": by,
		"elapsed_s": int(elapsed), "rate_per_s": fmt.Sprintf("%.1f", rate),
		"last_msg_ms": atomic.LoadInt64(&c.lastMsgMs),
	}
}

func main() {
	port := flag.Int("port", 8801, "http port")
	addr := flag.String("stomp", "127.0.0.1:61613", "broker address")
	dataDir := flag.String("data", "../state/lake", "lake directory")
	kvPath := flag.String("kv", "", "key/value store path (bbolt); empty disables it")
	sqlPath := flag.String("sql", "", "sqlite path for envelopes, books and trades; empty disables it")
	docsDir := flag.String("docs", "", "directory for envelope documents; empty disables it")
	mongoURI := flag.String("mongo", "", "mongodb uri for envelope documents; empty disables it")
	flag.Parse()
	log.SetFlags(log.Ltime)

	mem := lake.NewMemory(2000)
	journal, err := lake.OpenJournal(filepath.Join(*dataDir, "envelopes.jsonl"))
	if err != nil {
		log.Fatalf("journal: %v", err)
	}
	lk := lake.New(mem, journal)

	// The journal stays the record. These are projections beside it, and a
	// failure in any of them is counted, never fatal.
	var stores []store.Store
	if *kvPath != "" {
		if kv, err := store.OpenKV(*kvPath); err == nil {
			stores = append(stores, kv)
		} else {
			log.Printf("kv store disabled: %v", err)
		}
	}
	if *sqlPath != "" {
		if sq, err := store.OpenSQL(*sqlPath); err == nil {
			stores = append(stores, sq)
		} else {
			log.Printf("sql store disabled: %v", err)
		}
	}
	if *docsDir != "" {
		if d, err := store.OpenFileDocs(*docsDir); err == nil {
			stores = append(stores, d)
		} else {
			log.Printf("doc store disabled: %v", err)
		}
	}
	if *mongoURI != "" {
		if m, err := store.OpenMongo(*mongoURI, "cryptalyst", "envelopes"); err == nil {
			stores = append(stores, m)
			log.Printf("mongo documents enabled")
		} else {
			log.Printf("mongo disabled: %v", err)
		}
	}
	var proj *store.Multi
	if len(stores) > 0 {
		proj = store.NewMulti(stores...)
		log.Printf("projection stores: %d", len(stores))
	}
	corr := correlation.New()
	ctr := &counters{byTopic: map[string]int64{}, started: time.Now()}
	fan := newHub()

	go func() {
		for {
			time.Sleep(time.Second)
			_ = journal.Flush()
		}
	}()

	for _, t := range topics.All {
		topic := t.Name
		spec := t
		// the recorder owns its own queue: sharing q.<topic> with another consumer
		// would load-balance messages instead of delivering them to both
		if err := busadmin.Ensure("q.rec."+spec.Name, spec.Name, spec.Exchange); err != nil {
			log.Printf("topology %-18s %v", spec.Name, err)
		}
		go func() {
			for {
				cl := stomp.New(*addr, "cryptalyst", "cryptalyst")
				if err := cl.Connect(); err != nil {
					time.Sleep(2 * time.Second)
					continue
				}
				if err := cl.SubscribeQueue("q.rec."+topic, func(m stomp.Message) {
					env := fromHeaders(spec, m)
					// consume-side policy, run one interceptor at a time so each hop
					// lands on the envelope as evidence
					type rule struct {
						name string
						fn   envelope.Interceptor
					}
					rules := []rule{
						{"authority", envelope.Authorised(spec.Producers)},
						{"keying", envelope.RequireKey(keyedTopic(spec.Name))},
						{"correlation", envelope.EnsureCID(correlation.Mint)},
						{"freshness", envelope.MaxAge(staleLimit(spec.Name))},
					}
					refused := false
					for _, r := range rules {
						start := time.Now()
						next, ierr := r.fn(env)
						hop := envelope.Hop{Kind: "interceptor", Name: r.name, Outcome: "pass",
							MS: time.Since(start).Microseconds()}
						if ierr != nil {
							hop.Outcome = "refuse"
							hop.Detail = ierr.Error()
							refused = true
						}
						env = next.WithHop(hop)
						if refused {
							break
						}
					}
					if strings.HasPrefix(spec.Name, "model.") && env.Meta.Key != "" {
						// the producer did not declare its inputs, so the recorder says
						// what they were, marked as derived rather than declared
						env.Meta.Refs = append(env.Meta.Refs, derivedRefs(env.Meta.Key, time.Now().UnixMilli())...)
					}
					rememberRef(env)
					env = env.WithHop(envelope.Hop{Kind: "operator", Name: spec.Producers[0],
						Outcome: "pass", Detail: "produced " + spec.Name})
					env = env.WithHop(envelope.Hop{Kind: "transformer", Name: "record",
						Outcome: "pass", Detail: "stored once in the lake"})
					if refused {
						// a refusal is recorded too: "why not" is evidence
						atomic.AddInt64(&ctr.refused, 1)
						atomic.AddInt64(&ctr.received, 1)
						env.Meta.Extra = map[string]string{"refused": "true"}
						if refusedSeen[spec.Name] < 3 {
							refusedSeen[spec.Name]++
							log.Printf("REFUSED %-18s %s", spec.Name, lastDetail(env.Meta.Hops))
						}
						rememberRef(env)
						wrapped, _ := json.Marshal(env)
						rec := lake.Record{Topic: spec.Name, Key: env.Meta.Key,
							TS: time.Now().UnixMilli(), Payload: m.Body, Envelope: wrapped}
						if err := lk.Write(rec); err == nil {
							if line, mErr := json.Marshal(rec); mErr == nil {
								fan.send(line)
							}
						}
						return
					}
					wrapped, _ := json.Marshal(env)
					rec := lake.Record{
						Topic:    topic,
						Key:      env.Meta.Key,
						TS:       time.Now().UnixMilli(),
						Payload:  m.Body,
						Envelope: wrapped,
					}
					err := lk.Write(rec)
					ctr.bump(topic, err)
					if proj != nil && err == nil {
						_ = proj.Put(store.Envelope{
							ID:       store.NewID(topic, env.Meta.Key, rec.TS),
							Topic:    topic,
							Key:      env.Meta.Key,
							Plane:    string(env.Meta.Plane),
							Producer: env.Meta.Producer,
							TS:       rec.TS,
							Data:     m.Body,
						})
					}
					if line, mErr := json.Marshal(rec); mErr == nil {
						fan.send(line)
					}
					if env.Meta.Key != "" {
						docID := topic + ":" + env.Meta.Key
						if isTraceable(topic) {
							corr.Record(docID, env.Meta.CID, "append", env.Meta.Producer, topic)
						}
						// a decision inherits the advisory chain of its symbol, which
						// is what makes "why did we buy" answerable
						if topic == "model.decision" || topic == "model.fill" {
							for _, parent := range []string{"intel.signal", "intel.flow", "intel.ta", "intel.forecast"} {
								corr.Link(docID, parent+":"+env.Meta.Key)
							}
						}
					}
				}); err != nil {
					time.Sleep(2 * time.Second)
					continue
				}
				log.Printf("recording %-18s (q.%s)", topic, topic)
				for cl.Healthy() {
					time.Sleep(2 * time.Second)
				}
				time.Sleep(2 * time.Second)
			}
		}()
		time.Sleep(40 * time.Millisecond)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		writes, errs := lk.Stats()
		memW, memD := mem.Stats()
		docs, steps, reads := corr.Stats()
		out := ctr.snapshot()
		out["lake_writes"] = writes
		out["lake_errors"] = errs
		out["memory_writes"] = memW
		out["memory_drops"] = memD
		out["journal_writes"] = journal.Writes()
		out["backends"] = lk.BackendNames()
		out["correlation_docs"] = docs
		out["correlation_steps"] = steps
		out["correlation_reads"] = reads
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(out)
	})
	// /stream is an SSE feed of records as they are written. Readers use this
	// rather than subscribing to the broker, so the bus has one consumer.
	mux.HandleFunc("/stream", func(w http.ResponseWriter, r *http.Request) {
		fl, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "streaming unsupported", 500)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		ch := fan.join()
		defer fan.leave(ch)
		for {
			select {
			case <-r.Context().Done():
				return
			case line := <-ch:
				w.Write([]byte("event: record\ndata: "))
				w.Write(line)
				w.Write([]byte("\n\n"))
				fl.Flush()
			}
		}
	})
	mux.HandleFunc("/trace", func(w http.ResponseWriter, r *http.Request) {
		doc := r.URL.Query().Get("doc")
		if doc == "" {
			http.Error(w, "doc required", 400)
			return
		}
		d, ok := corr.Resolve(doc, r.URL.Query().Get("actor"))
		if !ok {
			http.Error(w, "not found", 404)
			return
		}
		json.NewEncoder(w).Encode(d)
	})
	mux.HandleFunc("/latest", func(w http.ResponseWriter, r *http.Request) {
		topic := r.URL.Query().Get("topic")
		json.NewEncoder(w).Encode(mem.Latest(topic, 5))
	})

	go func() {
		log.Printf("RECORDER on :%d → lake %s", *port, *dataDir)
		log.Fatal(http.ListenAndServe(fmt.Sprintf("0.0.0.0:%d", *port), mux))
	}()
	if err := os.MkdirAll(*dataDir, 0o755); err != nil {
		log.Printf("data dir: %v", err)
	}
	select {}
}

var (
	refusedMu   sync.Mutex
	refusedSeen = map[string]int{}
)

// latestRef is the most recent envelope of one topic for one symbol.
type latestRef struct {
	Topic      string
	Key        string
	Generation uint64
	CID        string
	TS         int64
}

var (
	refMu     sync.RWMutex
	refIndex  = map[string]map[string]latestRef{} // key -> topic -> ref
	refTopics = []string{"market.candle", "ticks", "market.depth",
		"intel.signal", "intel.flow", "intel.ta", "intel.forecast"}
)

func rememberRef(env envelope.Envelope[json.RawMessage]) {
	if env.Meta.Key == "" {
		return
	}
	refMu.Lock()
	defer refMu.Unlock()
	if refIndex[env.Meta.Key] == nil {
		refIndex[env.Meta.Key] = map[string]latestRef{}
	}
	refIndex[env.Meta.Key][env.Meta.Topic] = latestRef{
		Topic: env.Meta.Topic, Key: env.Meta.Key, Generation: env.Meta.Generation,
		CID: env.Meta.CID, TS: time.Now().UnixMilli(),
	}
}

// derivedRefs returns the inputs a decision most likely read: the newest envelope
// of each input topic for its symbol at the moment it arrived.
func derivedRefs(key string, at int64) []envelope.Ref {
	refMu.RLock()
	defer refMu.RUnlock()
	var out []envelope.Ref
	for _, topic := range refTopics {
		r, ok := refIndex[key][topic]
		if !ok || at-r.TS > 300000 {
			continue
		}
		out = append(out, envelope.Ref{Topic: r.Topic, Key: r.Key,
			Generation: r.Generation, CID: r.CID, Derived: true, AgeMS: at - r.TS})
	}
	return out
}

// lastDetail returns the refusal text of the final hop, for the log line.
func lastDetail(hops []envelope.Hop) string {
	if len(hops) == 0 {
		return ""
	}
	h := hops[len(hops)-1]
	return h.Name + ": " + h.Detail
}

// keyedTopic reports whether a topic's messages must carry a symbol.
func keyedTopic(topic string) bool {
	switch topic {
	case "market.depth", "market.candle",
		"intel.signal", "intel.flow", "intel.ta", "intel.forecast",
		"model.decision", "model.fill", "model.pnl":
		return true
	}
	return false
}

// staleLimit is the freshness contract per class: market data is ephemeral,
// decisions are still worth recording when they arrive late.
func staleLimit(topic string) time.Duration {
	if strings.HasPrefix(topic, "market.") || topic == "ticks" {
		return 2 * time.Minute
	}
	return 24 * time.Hour
}

// fromHeaders rebuilds the envelope the producer described. The producer owns
// provenance; the recorder validates it rather than inventing it.
func fromHeaders(spec topics.Topic, m stomp.Message) envelope.Envelope[json.RawMessage] {
	// the exchange decides the plane: health is not intelligence, and an alert
	// is control traffic, not an advisory
	plane := envelope.PlaneIntel
	switch spec.Exchange {
	case "market":
		plane = envelope.PlaneMarket
	case "model":
		plane = envelope.PlaneModel
	case "system", "review":
		plane = "system"
	case "risk", "exec", "alert":
		plane = envelope.PlaneControl
	}
	h := m.Headers
	if p := h["plane"]; p != "" {
		plane = envelope.Plane(p)
	}
	geti := func(k string) int64 {
		var v int64
		if s, ok := h[k]; ok {
			fmt.Sscanf(s, "%d", &v)
		}
		return v
	}
	key := h["key"]
	if key == "" {
		var probe struct {
			Symbol string `json:"symbol"`
			S      string `json:"s"`
			Key    string `json:"key"`
		}
		if json.Unmarshal(m.Body, &probe) == nil {
			key = probe.Symbol
			if key == "" {
				key = probe.S
			}
			if key == "" {
				key = probe.Key
			}
		}
	}
	producer := h["producer"]
	if producer == "" {
		producer = spec.Producers[0]
	}
	meta := envelope.Meta{
		Plane: plane, Topic: spec.Name, Key: key, Producer: producer,
		CID: h["cid"], Generation: uint64(geti("gen")), Sequence: uint64(geti("seq")),
	}
	if ts := h["computed_at"]; ts != "" {
		if t, err := time.Parse(time.RFC3339Nano, ts); err == nil {
			meta.ComputedAt = &t
		}
	}
	if meta.ComputedAt == nil {
		now := time.Now().UTC()
		meta.ComputedAt = &now
	}
	env := envelope.Envelope[json.RawMessage]{Data: json.RawMessage(m.Body), Meta: meta}
	env.Meta.Stamp()
	return env
}

func isTraceable(topic string) bool {
	return topic == "model.decision" || topic == "model.fill" || topic == "intel.signal"
}
