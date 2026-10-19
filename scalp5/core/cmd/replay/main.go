// replay re-emits a recorded lake back onto the bus. This is how a backtest
// stops being a different program: same envelopes, same consumers, new clock.
package main

import (
	"encoding/json"
	"flag"
	"log"
	"time"

	"cryptalyst/core/internal/lake"
	"cryptalyst/core/internal/stomp"
	"cryptalyst/core/internal/topics"
)

func main() {
	dir := flag.String("dir", "../state/lake2", "lake directory")
	file := flag.String("file", "", "single journal file (overrides dir)")
	speed := flag.Float64("speed", 0, "replay speed multiplier (0 = as fast as possible)")
	only := flag.String("topic", "", "replay only this topic")
	addr := flag.String("stomp", "127.0.0.1:61613", "broker address")
	flag.Parse()
	log.SetFlags(log.Ltime)

	cl := stomp.New(*addr, "cryptalyst", "cryptalyst")
	if err := cl.Connect(); err != nil {
		log.Fatalf("broker: %v", err)
	}

	start := time.Now()
	records := 0
	var lastTS int64
	emit := func(r lake.Record) error {
		if *only != "" && r.Topic != *only {
			return nil
		}
		spec, ok := topics.Get(r.Topic)
		if !ok {
			return nil
		}
		if *speed > 0 && lastTS != 0 {
			gap := time.Duration(float64(r.TS-lastTS) * float64(time.Millisecond) / *speed)
			if gap > 0 && gap < 5*time.Second {
				time.Sleep(gap)
			}
		}
		lastTS = r.TS
		body, err := json.Marshal(r.Payload)
		if err != nil {
			return nil
		}
		if err := cl.Publish(spec.Exchange, spec.Name, body); err != nil {
			return err
		}
		records++
		if records%5000 == 0 {
			log.Printf("replayed %d records", records)
		}
		return nil
	}

	var n int
	var err error
	if *file != "" {
		n, err = lake.Replay(*file, emit)
	} else {
		n, err = lake.ReplaySegments(*dir, emit)
	}
	if err != nil {
		log.Fatalf("replay: %v", err)
	}
	elapsed := time.Since(start)
	log.Printf("done: %d scanned, %d published in %s (%.0f/s)",
		n, records, elapsed.Round(time.Millisecond), float64(records)/elapsed.Seconds())
}
