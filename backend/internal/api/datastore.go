package api

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"pumpdump/backend/internal/binance"
)

// A local bar store.
//
// Fetching history from the exchange on every forecast is wasteful and makes the
// same request dozens of times a minute. Bars are written once to disk, keyed by
// symbol and interval, and read back from there; the network is only touched
// when the store cannot answer.
//
// Five and thirty second frames are aggregated from the one-second feed as they
// are ingested, because the exchange does not publish them.

type storedBar struct {
	T int64   `json:"t"`
	O float64 `json:"o"`
	H float64 `json:"h"`
	L float64 `json:"l"`
	C float64 `json:"c"`
	V float64 `json:"v"`
}

var (
	storeMu   sync.Mutex
	storeRoot = "data/history"
)

func storePath(symbol, interval string) string {
	return filepath.Join(storeRoot, fmt.Sprintf("%s-%s.jsonl", strings.ToUpper(symbol), interval))
}

// readStore returns the stored bars, oldest first.
func readStore(symbol, interval string) []storedBar {
	f, err := os.Open(storePath(symbol, interval))
	if err != nil {
		return nil
	}
	defer f.Close()
	out := []storedBar{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1<<20)
	for sc.Scan() {
		var b storedBar
		if json.Unmarshal(sc.Bytes(), &b) == nil {
			out = append(out, b)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].T < out[j].T })
	return out
}

// mergeStore writes bars, keeping one row per timestamp.
func mergeStore(symbol, interval string, add []storedBar) (ingested, kept int, err error) {
	storeMu.Lock()
	defer storeMu.Unlock()

	if err := os.MkdirAll(storeRoot, 0o755); err != nil {
		return 0, 0, err
	}
	byTime := map[int64]storedBar{}
	for _, b := range readStore(symbol, interval) {
		byTime[b.T] = b
	}
	before := len(byTime)
	for _, b := range add {
		if b.T == 0 || b.C <= 0 {
			continue
		}
		byTime[b.T] = b
	}
	merged := make([]storedBar, 0, len(byTime))
	for _, b := range byTime {
		merged = append(merged, b)
	}
	sort.Slice(merged, func(i, j int) bool { return merged[i].T < merged[j].T })

	tmp := storePath(symbol, interval) + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return 0, 0, err
	}
	w := bufio.NewWriter(f)
	for _, b := range merged {
		raw, _ := json.Marshal(b)
		if _, werr := w.Write(append(raw, '\n')); werr != nil {
			f.Close()
			return 0, 0, werr
		}
	}
	if err := w.Flush(); err != nil {
		f.Close()
		return 0, 0, err
	}
	f.Close()
	if err := os.Rename(tmp, storePath(symbol, interval)); err != nil {
		return 0, 0, err
	}
	return len(byTime) - before, len(merged), nil
}

// barsFor answers from the store, fetching only when the store is short.
func (s *Server) barsFor(ctx context.Context, symbol, interval string, want int) ([]binance.Kline, error) {
	stored := readStore(symbol, interval)
	if len(stored) >= want {
		stored = stored[len(stored)-want:]
		return toKlines(stored), nil
	}
	if s.deps.Market == nil {
		return toKlines(stored), nil
	}
	fetched := s.fetchAndStore(ctx, symbol, interval, want)
	if len(fetched) == 0 {
		return toKlines(stored), nil
	}
	return fetched, nil
}

// fetchAndStore pulls bars from the feed and writes them to the store.
func (s *Server) fetchAndStore(ctx context.Context, symbol, interval string, want int) []binance.Kline {
	if interval == "5s" || interval == "30s" {
		step := int64(5000)
		if interval == "30s" {
			step = 30000
		}
		n := want * int(step/1000)
		if n > 1000 {
			n = 1000
		}
		raw, err := s.deps.Market.Klines(ctx, symbol, "1s", n)
		if err != nil || len(raw) == 0 {
			return nil
		}
		bars := aggregateBars(raw, step)
		add := make([]storedBar, 0, len(bars))
		for _, b := range bars {
			add = append(add, storedBar{T: b.OpenTime, O: b.Open, H: b.High, L: b.Low, C: b.Close, V: b.Volume})
		}
		_, _, _ = mergeStore(symbol, interval, add)
		return bars
	}
	if !allowedIntervals[interval] {
		return nil
	}
	n := want
	if cap := IntervalBarCap(interval); n > cap {
		n = cap
	}
	raw, err := s.deps.Market.Klines(ctx, symbol, interval, n)
	if err != nil || len(raw) == 0 {
		return nil
	}
	add := make([]storedBar, 0, len(raw))
	for _, b := range raw {
		add = append(add, storedBar{T: b.OpenTime, O: b.Open, H: b.High, L: b.Low, C: b.Close, V: b.Volume})
	}
	_, _, _ = mergeStore(symbol, interval, add)
	return raw
}

func toKlines(in []storedBar) []binance.Kline {
	out := make([]binance.Kline, len(in))
	for i, b := range in {
		out[i] = binance.Kline{
			OpenTime: b.T, Open: b.O, High: b.H, Low: b.L, Close: b.C, Volume: b.V,
			CloseTime: b.T,
		}
	}
	return out
}

// handleDataLoad ingests bars for a symbol and interval.
func (s *Server) handleDataLoad(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	interval := strings.TrimSpace(r.URL.Query().Get("interval"))
	bars := queryInt(r, "bars", 1000)
	if symbol == "" || interval == "" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "symbol and interval required"})
		return
	}
	if s.deps.Market == nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "market client unavailable"})
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*1e9)
	defer cancel()
	got := s.fetchAndStore(ctx, symbol, interval, bars)
	stored := readStore(symbol, interval)
	out := map[string]any{
		"ok": true, "symbol": symbol, "interval": interval,
		"fetched": len(got), "stored": len(stored),
	}
	if len(stored) > 0 {
		out["from_ms"] = stored[0].T
		out["to_ms"] = stored[len(stored)-1].T
	}
	writeJSON(w, http.StatusOK, out)
}

// handleDataInventory reports what is on disk, so nobody re-downloads what is
// already there.
func (s *Server) handleDataInventory(w http.ResponseWriter, r *http.Request) {
	entries, err := os.ReadDir(storeRoot)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "datasets": []any{}, "root": storeRoot})
		return
	}
	type ds struct {
		Symbol   string `json:"symbol"`
		Interval string `json:"interval"`
		Bars     int    `json:"bars"`
		FromMS   int64  `json:"from_ms"`
		ToMS     int64  `json:"to_ms"`
		Bytes    int64  `json:"bytes"`
		File     string `json:"file"`
	}
	out := make([]ds, 0, len(entries))
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		base := strings.TrimSuffix(e.Name(), ".jsonl")
		parts := strings.SplitN(base, "-", 2)
		if len(parts) != 2 {
			continue
		}
		bars := readStore(parts[0], parts[1])
		info, _ := e.Info()
		d := ds{Symbol: parts[0], Interval: parts[1], Bars: len(bars), File: storeRoot + "/" + e.Name()}
		if len(bars) > 0 {
			d.FromMS = bars[0].T
			d.ToMS = bars[len(bars)-1].T
		}
		if info != nil {
			d.Bytes = info.Size()
		}
		out = append(out, d)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Symbol != out[j].Symbol {
			return out[i].Symbol < out[j].Symbol
		}
		return out[i].Interval < out[j].Interval
	})
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "root": storeRoot, "datasets": out})
}

// handleDataPrune removes a dataset from the store.
//
// The inventory is a list of things that cost disk and time to fetch; a list you
// cannot shorten is a hoard. Deleting is explicit and per dataset.
func (s *Server) handleDataPrune(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost && r.Method != http.MethodDelete {
		http.Error(w, "POST or DELETE only", http.StatusMethodNotAllowed)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	interval := strings.TrimSpace(r.URL.Query().Get("interval"))
	if symbol == "" || interval == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "symbol and interval required"})
		return
	}
	path := storePath(symbol, interval)
	if err := os.Remove(path); err != nil {
		if os.IsNotExist(err) {
			writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "not stored"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "symbol": symbol, "interval": interval})
}
