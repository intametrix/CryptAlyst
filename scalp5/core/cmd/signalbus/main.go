// signalbus replaces signal_bus.py: pulls pump/dump alerts from the Go detector
// and republishes each one on the bus the moment it appears.
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

type alert struct {
	Symbol   string   `json:"symbol"`
	Kind     string   `json:"kind"`
	Score    float64  `json:"score"`
	EventID  string   `json:"event_id"`
	Severity string   `json:"severity"`
	Summary  string   `json:"summary"`
	TS       int64    `json:"ts_ms"`
	Detector string   `json:"detector"`
	Reasons  []string `json:"reasons"`
}

type feed struct {
	Alerts []alert `json:"alerts"`
}

func main() {
	every := flag.Duration("every", 2*time.Second, "poll interval")
	backend := flag.String("backend", "http://127.0.0.1:8787", "detector base url")
	addr := flag.String("stomp", "127.0.0.1:61613", "broker address")
	flag.Parse()
	log.SetFlags(log.Ltime)

	spec, _ := topics.Get("alert.pumpdump")
	cl := stomp.New(*addr, "cryptalyst", "cryptalyst")
	seen := map[string]bool{}
	var mu sync.Mutex
	published := 0

	for {
		if !cl.Healthy() {
			if err := cl.Connect(); err != nil {
				time.Sleep(2 * time.Second)
				continue
			}
		}
		resp, err := http.Get(*backend + "/api/alerts?limit=200")
		if err != nil {
			time.Sleep(*every)
			continue
		}
		var f feed
		json.NewDecoder(resp.Body).Decode(&f)
		resp.Body.Close()

		for _, a := range f.Alerts {
			id := a.EventID
			if id == "" {
				id = a.Symbol + "|" + a.Kind + "|" + a.Summary
			}
			mu.Lock()
			if seen[id] {
				mu.Unlock()
				continue
			}
			seen[id] = true
			mu.Unlock()
			body, _ := json.Marshal(a)
			if err := cl.Publish(spec.Exchange, spec.Name, body); err == nil {
				published++
				if published%10 == 1 {
					log.Printf("published %d alerts, latest %s %s score %.0f",
						published, a.Symbol, a.Kind, a.Score)
				}
			}
		}
		if len(seen) > 5000 {
			mu.Lock()
			seen = map[string]bool{}
			mu.Unlock()
		}
		time.Sleep(*every)
	}
}
