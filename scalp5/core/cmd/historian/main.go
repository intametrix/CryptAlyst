// historian builds training data out of the lake.
//
// Bronze envelopes are aggregated into fixed time bars per symbol, the advisory
// topics are joined by symbol and time, and the result is written as a
// columnar CSV plus a manifest. A model can then be trained on the same object
// the explorer shows, which is the whole point of recording envelopes.
package main

import (
	"bufio"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type bar struct {
	symbol  string
	start   int64
	open    float64
	high    float64
	low     float64
	close   float64
	ticks   int
	depth   int
	spread  float64
	spreadN int
	imb     float64
	imbN    int
	flow    float64
	flowN   int
	score   float64
	scoreN  int
	fconf   float64
	fconfN  int
	taker   float64
	takerN  int
	oi      float64
	oiN     int
}

func (b *bar) addPrice(p float64) {
	if p <= 0 {
		return
	}
	if b.open == 0 {
		b.open = p
	}
	if b.high == 0 || p > b.high {
		b.high = p
	}
	if b.low == 0 || p < b.low {
		b.low = p
	}
	b.close = p
	b.ticks++
}

type envelope struct {
	Topic   string          `json:"topic"`
	Key     string          `json:"key"`
	TS      int64           `json:"ts"`
	Payload json.RawMessage `json:"payload"`
}

// inner pulls the payload's own envelope wrapper out of the way: producers that
// predate the wire envelope published {topic,data,...} bodies.
func inner(raw json.RawMessage) map[string]any {
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil {
		return nil
	}
	if d, ok := m["data"].(map[string]any); ok {
		return d
	}
	return m
}

func num(m map[string]any, key string) (float64, bool) {
	v, ok := m[key]
	if !ok {
		return 0, false
	}
	switch t := v.(type) {
	case float64:
		return t, true
	case string:
		f, err := strconv.ParseFloat(t, 64)
		return f, err == nil
	}
	return 0, false
}

func floorTo(ms int64, width int64) int64 { return ms - ms%width }

func main() {
	dir := flag.String("dir", "../state/lake4", "lake directory, or a comma-separated list")
	out := flag.String("out", "../datasets", "output directory")
	widthSec := flag.Int("bar", 60, "bar width in seconds")
	flag.Parse()
	log.SetFlags(log.Ltime)

	width := int64(*widthSec) * 1000
	bars := map[string]*bar{}
	var files []string
	for _, d := range strings.Split(*dir, ",") {
		matches, _ := filepath.Glob(filepath.Join(strings.TrimSpace(d), "*.jsonl"))
		files = append(files, matches...)
	}
	sort.Strings(files)

	lines := 0
	for _, f := range files {
		fh, err := os.Open(f)
		if err != nil {
			continue
		}
		sc := bufio.NewScanner(fh)
		sc.Buffer(make([]byte, 1<<20), 32<<20)
		for sc.Scan() {
			lines++
			var e envelope
			if json.Unmarshal(sc.Bytes(), &e) != nil {
				continue
			}
			start := floorTo(e.TS, width)
			getBar := func(symbol string) *bar {
				id := symbol + "|" + strconv.FormatInt(start, 10)
				b := bars[id]
				if b == nil {
					b = &bar{symbol: symbol, start: start}
					bars[id] = b
				}
				return b
			}
			data := inner(e.Payload)
			if data == nil {
				continue
			}
			applyPriceMap := func(m map[string]any) {
				for sym, v := range m {
					if p, ok := v.(float64); ok && p > 0 {
						getBar(sym).addPrice(p)
					}
				}
			}
			if e.Topic == "ticks" || e.Topic == "market.price" {
				if m, ok := data["p"].(map[string]any); ok {
					applyPriceMap(m)
					continue
				}
			}
			key := e.Key
			if key == "" {
				continue
			}
			b := getBar(key)
			switch {
			case e.Topic == "market.price":
				if p, ok := num(data, "price"); ok {
					b.addPrice(p)
				}
			case e.Topic == "market.depth":
				b.depth++
				if v, ok := num(data, "imbalance"); ok {
					b.imb += v
					b.imbN++
				}
			case e.Topic == "intel.flow":
				if v, ok := num(data, "flow_conf"); ok {
					b.fconf += v
					b.fconfN++
				}
				if v, ok := num(data, "score"); ok {
					b.flow += v
					b.flowN++
				}
			case e.Topic == "intel.signal":
				if v, ok := num(data, "score"); ok {
					b.score += v
					b.scoreN++
				}
				if v, ok := num(data, "taker_ratio"); ok {
					b.taker += v
					b.takerN++
				}
				if v, ok := num(data, "open_interest"); ok {
					b.oi += v
					b.oiN++
				}
			}
		}
		fh.Close()
	}

	rows := make([]*bar, 0, len(bars))
	for _, b := range bars {
		if b.ticks == 0 {
			continue
		}
		rows = append(rows, b)
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].symbol != rows[j].symbol {
			return rows[i].symbol < rows[j].symbol
		}
		return rows[i].start < rows[j].start
	})

	if err := os.MkdirAll(*out, 0o755); err != nil {
		log.Fatal(err)
	}
	path := filepath.Join(*out, "features.csv")
	fh, err := os.Create(path)
	if err != nil {
		log.Fatal(err)
	}
	w := csv.NewWriter(fh)
	header := []string{"symbol", "bar_ms", "open", "high", "low", "close",
		"ticks", "depth_events", "imbalance", "spread_bp", "flow", "flow_conf",
		"signal_score", "taker_ratio", "open_interest"}
	w.Write(header)
	for _, b := range rows {
		mean := func(sum float64, n int) float64 {
			if n == 0 {
				return 0
			}
			return sum / float64(n)
		}
		w.Write([]string{
			b.symbol, strconv.FormatInt(b.start, 10),
			fmt.Sprintf("%.8f", b.open), fmt.Sprintf("%.8f", b.high),
			fmt.Sprintf("%.8f", b.low), fmt.Sprintf("%.8f", b.close),
			strconv.Itoa(b.ticks), strconv.Itoa(b.depth),
			fmt.Sprintf("%.5f", mean(b.imb, b.imbN)),
			fmt.Sprintf("%.3f", mean(b.spread, b.spreadN)),
			fmt.Sprintf("%.4f", mean(b.flow, b.flowN)),
			fmt.Sprintf("%.3f", mean(b.fconf, b.fconfN)),
			fmt.Sprintf("%.4f", mean(b.score, b.scoreN)),
			fmt.Sprintf("%.5f", mean(b.taker, b.takerN)),
			fmt.Sprintf("%.2f", mean(b.oi, b.oiN)),
		})
	}
	w.Flush()
	fh.Close()

	symbols := map[string]int{}
	var first, last int64
	for _, b := range rows {
		symbols[b.symbol]++
		if first == 0 || b.start < first {
			first = b.start
		}
		if b.start > last {
			last = b.start
		}
	}
	manifest := map[string]any{
		"dataset": "features", "path": path, "bars": len(rows),
		"bar_seconds": *widthSec, "symbols": len(symbols), "per_symbol": symbols,
		"from_ms": first, "to_ms": last,
		"columns": header, "source_lines": lines,
		"built_at": time.Now().UTC().Format(time.RFC3339),
		"label_hint": "forward return over the next N bars, computed by the trainer",
	}
	mb, _ := json.MarshalIndent(manifest, "", "  ")
	os.WriteFile(filepath.Join(*out, "manifest.json"), mb, 0o644)

	log.Printf("historian: %d lake lines -> %d bars, %d symbols, %s", lines, len(rows), len(symbols), path)
	fmt.Println(string(mb))
}
