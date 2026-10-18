// eventbus - a small in-process pub/sub broker with topics, replay and SSE.
//
//	POST /publish?topic=ticks        body: any JSON
//	GET  /subscribe?topics=ticks,alerts   (SSE, replay from ?since=)
//	GET  /recent?topic=ticks&since=N
//	GET  /topics
//	GET  /health
//
// Publishers never block: each subscriber has a buffered channel and slow
// readers drop frames instead of stalling the bus.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type event struct {
	Seq   int64           `json:"seq"`
	Topic string          `json:"topic"`
	TS    int64           `json:"ts"`
	Data  json.RawMessage `json:"data"`
}

type topic struct {
	mu     sync.RWMutex
	ring   []event
	max    int
	subs   map[chan event]bool
	total  int64
	lastTS int64
}

var (
	topicsMu sync.RWMutex
	topics   = map[string]*topic{}
	seq      int64
	limits   = map[string]int{"ticks": 200, "alerts": 500, "ta": 500,
		"forecast": 300, "fills": 500, "default": 200}
)

func getTopic(name string) *topic {
	topicsMu.RLock()
	t, ok := topics[name]
	topicsMu.RUnlock()
	if ok {
		return t
	}
	topicsMu.Lock()
	defer topicsMu.Unlock()
	if t, ok := topics[name]; ok {
		return t
	}
	n := limits["default"]
	if v, ok := limits[name]; ok {
		n = v
	}
	t = &topic{ring: make([]event, 0, n), max: n, subs: map[chan event]bool{}}
	topics[name] = t
	return t
}

func (t *topic) publish(ev event) {
	t.mu.Lock()
	t.ring = append(t.ring, ev)
	if len(t.ring) > t.max {
		t.ring = t.ring[len(t.ring)-t.max:]
	}
	t.total++
	t.lastTS = ev.TS
	subs := make([]chan event, 0, len(t.subs))
	for c := range t.subs {
		subs = append(subs, c)
	}
	t.mu.Unlock()
	for _, c := range subs {
		select {
		case c <- ev:
		default: // slow subscriber: drop rather than block the pipeline
		}
	}
}

func main() {
	addr := flag.String("addr", "127.0.0.1:8797", "listen address")
	flag.Parse()
	log.SetFlags(log.Ltime)
	mux := http.NewServeMux()
	cors := func(w http.ResponseWriter) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Cache-Control", "no-store")
	}

	mux.HandleFunc("/publish", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		if r.Method != http.MethodPost {
			w.WriteHeader(405)
			return
		}
		name := r.URL.Query().Get("topic")
		if name == "" {
			name = "default"
		}
		body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		if err != nil || len(body) == 0 {
			w.WriteHeader(400)
			return
		}
		ev := event{Seq: atomic.AddInt64(&seq, 1), Topic: name,
			TS: time.Now().UnixMilli(), Data: json.RawMessage(body)}
		getTopic(name).publish(ev)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "seq": ev.Seq})
	})

	mux.HandleFunc("/subscribe", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		fl, ok := w.(http.Flusher)
		if !ok {
			w.WriteHeader(500)
			return
		}
		names := strings.Split(r.URL.Query().Get("topics"), ",")
		if len(names) == 0 || names[0] == "" {
			names = []string{"ticks"}
		}
		since, _ := strconv.ParseInt(r.URL.Query().Get("since"), 10, 64)
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Connection", "keep-alive")
		w.WriteHeader(200)

		ch := make(chan event, 256)
		for _, n := range names {
			t := getTopic(n)
			t.mu.Lock()
			t.subs[ch] = true
			// replay what the subscriber missed
			for _, ev := range t.ring {
				if ev.Seq > since {
					select {
					case ch <- ev:
					default:
					}
				}
			}
			t.mu.Unlock()
		}
		defer func() {
			for _, n := range names {
				t := getTopic(n)
				t.mu.Lock()
				delete(t.subs, ch)
				t.mu.Unlock()
			}
		}()
		keep := time.NewTicker(15 * time.Second)
		defer keep.Stop()
		for {
			select {
			case ev := <-ch:
				b, _ := json.Marshal(ev)
				fmt.Fprintf(w, "data: %s\n\n", b)
				fl.Flush()
			case <-keep.C:
				fmt.Fprint(w, ": keepalive\n\n")
				fl.Flush()
			case <-r.Context().Done():
				return
			}
		}
	})

	mux.HandleFunc("/recent", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		name := r.URL.Query().Get("topic")
		if name == "" {
			name = "default"
		}
		since, _ := strconv.ParseInt(r.URL.Query().Get("since"), 10, 64)
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		if limit <= 0 {
			limit = 100
		}
		t := getTopic(name)
		t.mu.RLock()
		out := []event{}
		for _, ev := range t.ring {
			if ev.Seq > since {
				out = append(out, ev)
			}
		}
		t.mu.RUnlock()
		if len(out) > limit {
			out = out[len(out)-limit:]
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"topic": name, "events": out, "count": len(out)})
	})

	mux.HandleFunc("/topics", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		topicsMu.RLock()
		out := []map[string]any{}
		for name, t := range topics {
			t.mu.RLock()
			out = append(out, map[string]any{"topic": name, "published": t.total,
				"buffered": len(t.ring), "subscribers": len(t.subs), "last_ts": t.lastTS})
			t.mu.RUnlock()
		}
		topicsMu.RUnlock()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"topics": out})
	})

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "service": "eventbus",
			"seq": atomic.LoadInt64(&seq), "ts": time.Now().UnixMilli()})
	})

	log.Printf("eventbus on http://%s", *addr)
	log.Fatal(http.ListenAndServe(*addr, mux))
}
