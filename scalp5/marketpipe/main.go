// marketpipe - an async market-data pipe in Go.
//
// One goroutine polls the exchange, a fan-out goroutine broadcasts to every
// subscriber, and handlers only ever read a snapshot. Nothing blocks the refresh
// loop, so a slow client cannot slow the pipe.
//
//	GET /prices?syms=BTC,ETH   -> {"ts":..,"age_ms":..,"p":{...}}
//	GET /stream?symbols=BTCUSDT,ETHUSDT   (SSE, pushes every update)
//	GET /health
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"io"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

type snapshot struct {
	TS    int64              `json:"ts"`
	AgeMS int64              `json:"age_ms"`
	P     map[string]float64 `json:"p"`
}

var (
	cur    atomic.Value // *snapshot
	subs   = map[chan []byte]bool{}
	subsMu sync.Mutex
	client = &http.Client{Timeout: 5 * time.Second}
)

func broadcast(b []byte) {
	subsMu.Lock()
	for ch := range subs {
		select {
		case ch <- b:
		default: // drop for slow clients, never block the pipe
		}
	}
	subsMu.Unlock()
}

func poll(interval time.Duration) {
	for {
		t0 := time.Now()
		body, err := func() ([]byte, error) {
			resp, err := client.Get("https://api.binance.com/api/v3/ticker/price")
			if err != nil {
				return nil, err
			}
			defer resp.Body.Close()
			return io.ReadAll(resp.Body)
		}()
		if err == nil {
			var raw []struct {
				Symbol string `json:"symbol"`
				Price  string `json:"price"`
			}
			if json.Unmarshal(body, &raw) == nil {
				p := make(map[string]float64, len(raw))
				for _, r := range raw {
					var f float64
					fmt.Sscanf(r.Price, "%f", &f)
					p[r.Symbol] = f
				}
				s := &snapshot{TS: time.Now().UnixMilli(), P: p}
				cur.Store(s)
				b, _ := json.Marshal(s)
				broadcast(b)
			}
		} else {
			log.Printf("poll error: %v", err)
		}
		el := time.Since(t0)
		if el < interval {
			time.Sleep(interval - el)
		}
	}
}

func main() {
	addr := flag.String("addr", "127.0.0.1:8791", "listen address")
	interval := flag.Duration("interval", 2*time.Second, "poll interval")
	flag.Parse()
	log.SetFlags(log.Ltime)

	go poll(*interval)

	mux := http.NewServeMux()
	cors := func(w http.ResponseWriter) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
	}
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		s, _ := cur.Load().(*snapshot)
		n := 0
		if s != nil {
			n = len(s.P)
		}
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "service": "marketpipe",
			"symbols": n, "interval": interval.String(), "ts": time.Now().UnixMilli()})
	})
	mux.HandleFunc("/prices", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		s, _ := cur.Load().(*snapshot)
		if s == nil {
			w.WriteHeader(503)
			json.NewEncoder(w).Encode(map[string]string{"error": "warming up"})
			return
		}
		out := snapshot{TS: s.TS, AgeMS: time.Now().UnixMilli() - s.TS, P: s.P}
		// optional filter: ?syms=BTCUSDT,ETHUSDT
		q := r.URL.Query().Get("syms")
		if q != "" {
			f := map[string]float64{}
			for _, sym := range split(q) {
				if v, ok := s.P[sym]; ok {
					f[sym] = v
				}
			}
			out.P = f
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(out)
	})
	mux.HandleFunc("/stream", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		fl, ok := w.(http.Flusher)
		if !ok {
			w.WriteHeader(500)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Connection", "keep-alive")
		w.WriteHeader(200)
		ch := make(chan []byte, 8)
		subsMu.Lock()
		subs[ch] = true
		subsMu.Unlock()
		defer func() {
			subsMu.Lock()
			delete(subs, ch)
			subsMu.Unlock()
		}()
		if s, ok2 := cur.Load().(*snapshot); ok2 {
			b, _ := json.Marshal(s)
			fmt.Fprintf(w, "data: %s\n\n", b)
			fl.Flush()
		}
		keep := time.NewTicker(15 * time.Second)
		defer keep.Stop()
		for {
			select {
			case b := <-ch:
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
	log.Printf("marketpipe on http://%s, polling every %s", *addr, *interval)
	log.Fatal(http.ListenAndServe(*addr, mux))
}

func split(s string) []string {
	out := []string{}
	cur := ""
	for _, c := range s {
		if c == ',' {
			if cur != "" {
				out = append(out, cur)
			}
			cur = ""
			continue
		}
		cur += string(c)
	}
	if cur != "" {
		out = append(out, cur)
	}
	return out
}
