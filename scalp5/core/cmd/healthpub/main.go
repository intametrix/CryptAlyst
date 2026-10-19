// healthpub replaces health_pub.py: probes every service and publishes
// system.health on the bus, once per interval, forever.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"time"

	"cryptalyst/core/internal/stomp"
	"cryptalyst/core/internal/topics"
)

type service struct {
	name string
	url  string
	port int
}

var services = []service{
	{"backend", "http://127.0.0.1:8787/health", 8787},
	{"market_data", "http://127.0.0.1:8796/health", 8796},
	{"forecast", "http://127.0.0.1:8790/health", 8790},
	{"signal_bus", "http://127.0.0.1:8793/health", 8793},
	{"optimiser", "http://127.0.0.1:8795/health", 8795},
	{"master", "http://127.0.0.1:8799/api/health", 8799},
	{"tad", "http://127.0.0.1:8792/health", 8792},
}

type result struct {
	Name   string `json:"name"`
	Up     bool   `json:"up"`
	Ms     int64  `json:"ms"`
	Detail string `json:"detail,omitempty"`
	Port   bool   `json:"port"`
}

func probe(s service) result {
	start := time.Now()
	client := http.Client{Timeout: 6 * time.Second}
	res := result{Name: s.name}
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", s.port), 400*time.Millisecond)
	if err == nil {
		res.Port = true
		conn.Close()
	}
	resp, err := client.Get(s.url)
	res.Ms = time.Since(start).Milliseconds()
	if err != nil {
		return res
	}
	defer resp.Body.Close()
	res.Up = resp.StatusCode == 200
	res.Detail = resp.Status
	return res
}

func main() {
	every := flag.Duration("every", 5*time.Second, "probe interval")
	addr := flag.String("stomp", "127.0.0.1:61613", "broker address")
	flag.Parse()
	log.SetFlags(log.Ltime)

	cl := stomp.New(*addr, "cryptalyst", "cryptalyst")
	spec, _ := topics.Get("system.health")

	for {
		if !cl.Healthy() {
			if err := cl.Connect(); err != nil {
				log.Printf("broker unreachable: %v", err)
				time.Sleep(3 * time.Second)
				continue
			}
		}
		results := make([]result, 0, len(services))
		up := 0
		var worst int64
		for _, s := range services {
			r := probe(s)
			if r.Up {
				up++
			}
			if r.Ms > worst {
				worst = r.Ms
			}
			results = append(results, r)
		}
		payload := map[string]any{
			"services": results, "up": up, "total": len(results),
			"worst_ms": worst, "ts": time.Now().UnixMilli(),
		}
		body, _ := json.Marshal(payload)
		if err := cl.Publish(spec.Exchange, spec.Name, body); err != nil {
			log.Printf("publish failed: %v", err)
		}
		log.Printf("%d/%d up  worst %dms", up, len(results), worst)
		time.Sleep(*every)
	}
}
