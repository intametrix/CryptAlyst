package api

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"time"

	"pumpdump/backend/internal/binance"
)

// klinesCache makes high-frequency chart polling cheap.
//
// The chart wants to refresh as often as possible, but every uncached
// /api/klines call is a Binance REST request costing weight. Caching the raw
// klines for a short TTL means the browser can poll once a second, and several
// browsers or several open overlays on the same symbol still cost Binance
// exactly one request per TTL.
//
// The TTL is deliberately very short. This is a liveness cache, not a
// performance cache: it exists to collapse duplicate concurrent requests, not
// to serve stale prices. At 300ms a chart can poll four times a second and
// still cost Binance only ~200 requests/minute (weight 2 each, against a 6000
// budget), while never showing data older than a third of a second.
const klinesCacheTTL = 300 * time.Millisecond

type klinesKey struct {
	symbol   string
	interval string
	limit    int
}

type klinesEntry struct {
	klines    []binance.Kline
	expiresMS int64
}

type klinesCache struct {
	mu      sync.Mutex
	entries map[klinesKey]klinesEntry
}

// NewKlinesCache creates the shared chart-fetch cache.
func NewKlinesCache() *klinesCache {
	return &klinesCache{entries: make(map[klinesKey]klinesEntry)}
}

// Get returns cached klines, or fetches and stores them.
//
// The fetch happens while NOT holding the lock, so a slow Binance response
// cannot serialise every other symbol's chart behind it. Two simultaneous
// misses for the same key may therefore both fetch; that is the right trade
// here, since a duplicated request is cheaper than a global stall.
func (c *klinesCache) Get(ctx context.Context, client *binance.Client, key klinesKey) ([]binance.Kline, error) {
	now := time.Now().UnixMilli()

	c.mu.Lock()
	if entry, ok := c.entries[key]; ok && entry.expiresMS > now {
		klines := entry.klines
		c.mu.Unlock()
		return klines, nil
	}
	c.mu.Unlock()

	klines, err := client.Klines(ctx, key.symbol, key.interval, key.limit)
	if err != nil {
		return nil, err
	}

	c.mu.Lock()
	// Bound the map: charts are hand-driven, so this stays tiny, but a runaway
	// client cycling symbols should not grow it without limit.
	if len(c.entries) > 256 {
		for k, entry := range c.entries {
			if entry.expiresMS <= now {
				delete(c.entries, k)
			}
		}
	}
	c.entries[key] = klinesEntry{klines: klines, expiresMS: now + int64(klinesCacheTTL/time.Millisecond)}
	c.mu.Unlock()
	return klines, nil
}

// handleKlinesLatest serves the light payload a chart polls at high frequency:
// the forming bar plus the latest value of each indicator, instead of the whole
// history. It is served from the same cache as the full endpoint, so polling it
// costs Binance nothing extra.
func (s *Server) handleKlinesLatest(w http.ResponseWriter, r *http.Request) {
	if s.deps.Market == nil || s.deps.KlinesCache == nil {
		http.Error(w, "market client unavailable", http.StatusServiceUnavailable)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	interval := strings.TrimSpace(r.URL.Query().Get("interval"))
	if interval == "" {
		interval = "1m"
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}
	if symbol == "" || len(symbol) > 24 {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()

	// Enough history for the slowest indicator on the chart (SMA200).
	limit := 260
	klines, err := s.deps.KlinesCache.Get(ctx, s.deps.Market, klinesKey{symbol, interval, limit})
	if err != nil {
		http.Error(w, "klines unavailable: "+err.Error(), http.StatusBadGateway)
		return
	}
	if len(klines) == 0 {
		writeJSON(w, http.StatusOK, map[string]any{
			"type": "bar", "symbol": symbol, "interval": interval,
			"ts_ms": time.Now().UnixMilli(), "bar": nil,
			"indicators": map[string]any{}, "latest": map[string]any{},
		})
		return
	}

	full := buildChart(symbol, interval, klines, s.marksFor(symbol))
	last := len(full.Bars) - 1

	// Only the newest value of each indicator series; the chart already has the
	// history and only needs to extend it by one point.
	indicatorTail := make(map[string]any, len(full.Indicators))
	for name, series := range full.Indicators {
		if len(series) > 0 && series[len(series)-1] != nil {
			indicatorTail[name] = *series[len(series)-1]
		} else {
			indicatorTail[name] = nil
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"type":       "bar",
		"symbol":     symbol,
		"interval":   interval,
		"ts_ms":      time.Now().UnixMilli(),
		"bar":        full.Bars[last],
		"indicators": indicatorTail,
		"marks":      full.Marks,
		"latest":     full.Latest,
	})
}
