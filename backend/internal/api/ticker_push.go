package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"pumpdump/backend/internal/events"
)

// TickerPump pushes per-symbol price ticks to the dashboard over the existing
// SSE hub, so a chart can update on every ingested tick instead of waiting for
// a poll. No second websocket or extra REST weight is involved: the data is
// already arriving, and this only forwards it.
//
// Ticks are COALESCED, keeping only the newest sample per symbol, and flushed
// on a short interval. At a few hundred symbols the raw rate is a few hundred
// messages per second; forwarding that unbatched would drown the event stream
// and make the browser the bottleneck. Coalescing bounds the outbound rate
// regardless of how busy the market gets, and the newest sample is the only one
// a chart can use anyway.
const tickerFlushInterval = 250 * time.Millisecond

// TickSample is the wire shape the chart consumes.
type TickSample struct {
	Symbol       string  `json:"symbol"`
	TSMS         int64   `json:"ts_ms"`
	Price        float64 `json:"price"`
	Bid          float64 `json:"bid"`
	Ask          float64 `json:"ask"`
	QuoteVol24h  float64 `json:"quote_vol_24h"`
	ChangePct24h float64 `json:"price_change_pct_24h"`
}

// TickerPump coalesces ticks and broadcasts them.
type TickerPump struct {
	hub      *events.Hub
	interval time.Duration

	mu      sync.Mutex
	pending map[string]TickSample
	latest  map[string]TickSample
	flushes int64
	sent    int64
}

// NewTickerPump creates a pump bound to an event hub.
func NewTickerPump(hub *events.Hub) *TickerPump {
	return &TickerPump{
		hub:      hub,
		interval: tickerFlushInterval,
		pending:  make(map[string]TickSample),
		latest:   make(map[string]TickSample),
	}
}

// Observe accepts one raw @ticker payload.
//
// Keys are extracted by exact name into a map rather than decoded into a tagged
// struct: Binance sends both "e" (event type, string) and "E" (event time,
// number), and Go's encoding/json matches tags case-insensitively, so a tagged
// struct fails to decode every message. That bug already cost this project a
// silent, total ingestion outage once.
func (p *TickerPump) Observe(raw json.RawMessage) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return
	}
	symbol := fieldString(fields, "s")
	if symbol == "" {
		return
	}
	sample := TickSample{
		Symbol:       symbol,
		TSMS:         fieldInt(fields, "E"),
		Price:        fieldFloat(fields, "c"),
		Bid:          fieldFloat(fields, "b"),
		Ask:          fieldFloat(fields, "a"),
		QuoteVol24h:  fieldFloat(fields, "q"),
		ChangePct24h: fieldFloat(fields, "P"),
	}
	if sample.Price <= 0 {
		return
	}
	p.mu.Lock()
	p.pending[symbol] = sample
	p.mu.Unlock()
}

// Run flushes coalesced ticks until the context is cancelled.
func (p *TickerPump) Run(ctx context.Context) {
	ticker := time.NewTicker(p.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			p.flush()
		}
	}
}

func (p *TickerPump) flush() {
	p.mu.Lock()
	if len(p.pending) == 0 {
		p.mu.Unlock()
		return
	}
	batch := p.pending
	p.pending = make(map[string]TickSample, len(batch))
	rows := make([]TickSample, 0, len(batch))
	for symbol, sample := range batch {
		p.latest[symbol] = sample
		rows = append(rows, sample)
	}
	// Bound the latest-price map the same way the engine bounds its own state.
	if len(p.latest) > 4096 {
		p.latest = make(map[string]TickSample, len(batch))
		for symbol, sample := range batch {
			p.latest[symbol] = sample
		}
	}
	p.flushes++
	p.sent += int64(len(rows))
	p.mu.Unlock()

	p.hub.Broadcast(map[string]any{
		"type":  "ticker",
		"ts_ms": time.Now().UnixMilli(),
		"rows":  rows,
	})
}

// Latest returns the most recent sample for one symbol.
func (p *TickerPump) Latest(symbol string) (TickSample, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	sample, ok := p.latest[symbol]
	return sample, ok
}

// Stats reports pump health for the status line.
func (p *TickerPump) Stats() (flushes, ticks int64) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.flushes, p.sent
}

func (p *Server) handleTicker(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" || p.deps.TickerPump == nil {
		writeJSON(w, http.StatusOK, map[string]any{"type": "ticker", "rows": []any{}})
		return
	}
	if sample, ok := p.deps.TickerPump.Latest(symbol); ok {
		writeJSON(w, http.StatusOK, map[string]any{"type": "ticker", "rows": []any{sample}})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"type": "ticker", "rows": []any{}})
}

// --------------------------------------------------------------------------
// exact-key extraction (see the note on Observe)
// --------------------------------------------------------------------------

func fieldString(fields map[string]json.RawMessage, key string) string {
	raw := fields[key]
	if len(raw) == 0 {
		return ""
	}
	if raw[0] == '"' {
		var s string
		if err := json.Unmarshal(raw, &s); err != nil {
			return ""
		}
		return s
	}
	return strings.TrimSpace(string(raw))
}

func fieldFloat(fields map[string]json.RawMessage, key string) float64 {
	text := fieldString(fields, key)
	if text == "" {
		return 0
	}
	f, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return 0
	}
	return f
}

func fieldInt(fields map[string]json.RawMessage, key string) int64 {
	text := fieldString(fields, key)
	if text == "" {
		return 0
	}
	if n, err := strconv.ParseInt(text, 10, 64); err == nil {
		return n
	}
	if f, err := strconv.ParseFloat(text, 64); err == nil {
		return int64(f)
	}
	return 0
}
