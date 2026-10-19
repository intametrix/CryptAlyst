// busconsumer replaces bus_consumer.py: one consumer per topic queue, tallies
// per topic, and a small HTTP surface so the dashboard can prove the bus works.
package main

import (
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"sync"
	"time"

	"cryptalyst/core/internal/stomp"
	"cryptalyst/core/internal/topics"
)

type tally struct {
	Count  int64           `json:"count"`
	LastTS int64           `json:"last_ts"`
	Last   json.RawMessage `json:"last,omitempty"`
}

type store struct {
	mu     sync.Mutex
	topics map[string]*tally
}

func (s *store) record(topic string, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.topics[topic]
	if t == nil {
		t = &tally{}
		s.topics[topic] = t
	}
	t.Count++
	t.LastTS = time.Now().UnixMilli()
	if len(body) < 2048 {
		t.Last = append(json.RawMessage{}, body...)
	}
}

func (s *store) snapshot() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := map[string]any{}
	for k, v := range s.topics {
		out[k] = v
	}
	return map[string]any{"topics": out, "count": len(out), "ts": time.Now().UnixMilli()}
}

func main() {
	port := flag.Int("port", 8798, "http port")
	addr := flag.String("stomp", "127.0.0.1:61613", "broker address")
	flag.Parse()
	log.SetFlags(log.Ltime)

	st := &store{topics: map[string]*tally{}}

	for _, t := range topics.All {
		topic := t.Name
		go func() {
			for {
				cl := stomp.New(*addr, "cryptalyst", "cryptalyst")
				if err := cl.Connect(); err != nil {
					time.Sleep(2 * time.Second)
					continue
				}
				queue := "q." + topic
				if err := cl.SubscribeQueue(queue, func(m stomp.Message) {
					st.record(topic, m.Body)
				}); err != nil {
					time.Sleep(2 * time.Second)
					continue
				}
				log.Printf("consuming %-18s (%s)", topic, queue)
				for cl.Healthy() {
					time.Sleep(2 * time.Second)
				}
				log.Printf("consumer %s dropped, reconnecting", topic)
				time.Sleep(2 * time.Second)
			}
		}()
		time.Sleep(50 * time.Millisecond)
	}

	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(st.snapshot())
	})
	log.Printf("BUS CONSUMER (Go) — %d topics on :%d", len(topics.All), *port)
	log.Fatal(http.ListenAndServe("0.0.0.0:"+itoa(*port), nil))
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
