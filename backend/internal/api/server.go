// Package api exposes the scanner over HTTP: a small REST surface plus an SSE
// stream, and it serves the built React dashboard from the same origin so the
// frontend needs no CORS proxy in production.
package api

import (
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"log"
	"mime"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/detect"
	"pumpdump/backend/internal/events"
	"pumpdump/backend/internal/flow"
	"pumpdump/backend/internal/heat"
	"pumpdump/backend/internal/market"
	"pumpdump/backend/internal/outlook"
	"pumpdump/backend/internal/screen"
)

// Alert is a detected onset or reversal, enriched for display.
//
// It embeds the flow candidate that produced it and adds the scoring breakdown,
// mirroring the fields the dashboard expects under `metrics`.
type Alert struct {
	Type string `json:"type"`
	// Detector names the pluggable model that produced this alert, so the UI
	// can render a mixed feed from several models without special-casing.
	Detector   string `json:"detector"`
	Batch      bool   `json:"batch"`
	SnapshotMS int64  `json:"snapshot_ms,omitempty"`
	EventID    string `json:"event_id"`
	TSMS       int64  `json:"ts_ms"`
	Market     string `json:"market"`
	Symbol     string `json:"symbol"`
	Kind       string `json:"kind"`
	// Summary is the detector's one-line rendering, so the UI can show a
	// readable description without reassembling one from metrics.
	Summary string `json:"summary"`
	// Probability is P(reach +target before -stop within the horizon | these
	// features), and ProbabilityBasis MUST be displayed with it. It is omitted
	// entirely when no fitted model is loaded, so the UI cannot render a bare
	// percentage that looks measured when it is not.
	Probability      *float64           `json:"probability,omitempty"`
	ProbabilityBasis string             `json:"probability_basis,omitempty"`
	Severity         string             `json:"severity"`
	Score            float64            `json:"score"`
	Price            float64            `json:"price"`
	Reasons          []string           `json:"reasons"`
	Components       map[string]float64 `json:"components"`
	Metrics          map[string]any     `json:"metrics"`
}

// AlertBuffer is a bounded, newest-first ring of alerts.
type AlertBuffer struct {
	mu    sync.RWMutex
	items []Alert
	limit int
	seen  int64

	// bySymbol keeps a longer per-symbol history than the global ring, because
	// chart markers are drawn per symbol: a symbol that alerted an hour ago
	// should still show its markers when the user opens it, even though the
	// global ring has long since rolled over.
	bySymbol map[string][]Alert
	// path, when set, makes the history durable across restarts and is loaded
	// on construction so "previous" markers survive a restart.
	path string
	// fileMu serialises history writes without holding mu during IO.
	fileMu sync.Mutex
}

// NewAlertBuffer creates a buffer holding at most `limit` alerts.
func NewAlertBuffer(limit int) *AlertBuffer {
	if limit <= 0 {
		limit = 500
	}
	buffer := &AlertBuffer{
		limit:    limit,
		items:    make([]Alert, 0, limit),
		bySymbol: make(map[string][]Alert),
	}
	return buffer
}

// Add prepends an alert, trimming the oldest beyond the limit.
func (b *AlertBuffer) Add(a Alert) {
	b.mu.Lock()
	b.seen++
	b.items = append(b.items, a)
	if len(b.items) > b.limit {
		// Keep the newest `limit` entries.
		b.items = b.items[len(b.items)-b.limit:]
	}

	// Per-symbol history for chart markers, capped independently.
	history := append(b.bySymbol[a.Symbol], a)
	const perSymbol = 400
	if len(history) > perSymbol {
		history = history[len(history)-perSymbol:]
	}
	b.bySymbol[a.Symbol] = history
	path := b.path
	b.mu.Unlock()

	// Disk IO deliberately happens OUTSIDE the lock, and appendToDisk must not
	// take mu again: sync.RWMutex is not reentrant, so re-locking while holding
	// the write lock deadlocks the scanner and blocks every reader behind it.
	b.appendToDisk(path, a)
}

// List returns up to `limit` alerts, newest first.
func (b *AlertBuffer) List(limit int) []Alert {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if limit <= 0 || limit > len(b.items) {
		limit = len(b.items)
	}
	out := make([]Alert, 0, limit)
	for i := len(b.items) - 1; i >= 0 && len(out) < limit; i-- {
		out = append(out, b.items[i])
	}
	return out
}

// Count returns how many alerts have been recorded in total.
func (b *AlertBuffer) Count() int64 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.seen
}

// Clear empties the buffer and reports how many were removed.
func (b *AlertBuffer) Clear() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(b.items)
	b.items = b.items[:0]
	return n
}

// SurveyProvider supplies the latest Turtle survey payload on demand.
type SurveyProvider interface {
	Current() any
	Refresh() error
}

// Deps are the server's collaborators.
type Deps struct {
	Hub      *events.Hub
	Engine   *flow.Engine
	Universe *market.Universe
	Screener *screen.Screener
	Alerts   *AlertBuffer
	Survey   SurveyProvider
	// Detectors is the pluggable-model registry driving detection.
	Detectors *detect.Registry
	// Heat scores how interesting each symbol currently is, for the UI glow.
	Heat *heat.Service
	// KlinesCache collapses duplicate concurrent chart fetches so the browser
	// can poll quickly without multiplying Binance REST weight.
	KlinesCache *klinesCache
	// TickerPump forwards coalesced per-symbol price ticks for live charts.
	TickerPump *TickerPump
	// Outlook reports event state, ETA to reversal and the short-horizon
	// distribution for one symbol.
	Outlook *outlook.Service
	// Market is the REST client used for chart klines. The survey and screen
	// own their own clients, but the chart endpoint is ad-hoc per request, so
	// it borrows this one.
	Market        *binance.Client
	Static        fs.FS
	ConfigSummary map[string]any
	StartedAt     time.Time
	Candidates    func() int64
	Scans         func() int64
	StreamUp      func() bool
}

// Server holds the HTTP handlers.
type Server struct {
	deps Deps
}

// New creates a server.
func New(deps Deps) *Server { return &Server{deps: deps} }

// Routes builds the mux.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/health", s.handleHealth)
	mux.HandleFunc("/api/config", s.handleConfig)
	mux.HandleFunc("/api/universe", s.handleUniverse)
	mux.HandleFunc("/api/flow", s.handleFlow)
	mux.HandleFunc("/api/flow/history", s.handleFlowHistory)
	mux.HandleFunc("/api/alerts", s.handleAlerts)
	mux.HandleFunc("/api/screen", s.handleScreen)
	mux.HandleFunc("/api/screen/refresh", s.handleScreenRefresh)
	mux.HandleFunc("/api/survey", s.handleSurvey)
	mux.HandleFunc("/api/survey/refresh", s.handleSurveyRefresh)
	mux.HandleFunc("/api/symbol", s.handleSymbol)
	mux.HandleFunc("/api/detectors", s.handleDetectors)
	mux.HandleFunc("/api/heat", s.handleHeat)
	mux.HandleFunc("/api/klines", s.handleKlines)
	mux.HandleFunc("/api/klines/latest", s.handleKlinesLatest)
	mux.HandleFunc("/api/ticker", s.handleTicker)
	mux.HandleFunc("/api/outlook", s.handleOutlook)
	mux.HandleFunc("/api/tools", s.handleTools)
	mux.HandleFunc("/api/events", s.handleEvents)
	mux.HandleFunc("/", s.handleStatic)
	return logRequests(mux)
}

func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// The SSE endpoint is long-lived; logging its completion is noise.
		if r.URL.Path != "/api/events" {
			log.Printf("%s %s", r.Method, r.URL.Path)
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		log.Printf("api: encode failed: %v", err)
	}
}

// --------------------------------------------------------------------------
// Handlers
// --------------------------------------------------------------------------

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":     "ok",
		"uptime_sec": int(time.Since(s.deps.StartedAt).Seconds()),
	})
}

func (s *Server) handleConfig(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.deps.ConfigSummary)
}

func (s *Server) handleUniverse(w http.ResponseWriter, r *http.Request) {
	limit := queryInt(r, "limit", 500)
	rows := s.deps.Universe.TopByVolume(limit)
	if rows == nil {
		rows = []market.Context{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"symbols": rows})
}

func (s *Server) handleFlow(w http.ResponseWriter, r *http.Request) {
	limit := queryInt(r, "limit", 25)
	engine := s.deps.Engine
	now := time.Now().UnixMilli()
	rows := engine.Snapshot(limit, now)
	if rows == nil {
		rows = []flow.Candidate{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"type":       "flow",
		"ts_ms":      now,
		"window_sec": engine.Config().WindowSec,
		"rows":       rows,
	})
}

func (s *Server) handleAlerts(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodDelete:
		cleared := s.deps.Alerts.Clear()
		writeJSON(w, http.StatusOK, map[string]any{"cleared": cleared})
	default:
		limit := queryInt(r, "limit", 200)
		writeJSON(w, http.StatusOK, map[string]any{"alerts": s.deps.Alerts.List(limit)})
	}
}

func (s *Server) handleScreen(w http.ResponseWriter, r *http.Request) {
	rows, generatedMS, scanned := s.deps.Screener.Cached()
	if rows == nil {
		rows = []screen.Row{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"type":            "screen",
		"ts_ms":           generatedMS,
		"symbols_scanned": scanned,
		"thresholds":      s.deps.Screener.Thresholds(),
		"rows":            rows,
	})
}

func (s *Server) handleScreenRefresh(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST required", http.StatusMethodNotAllowed)
		return
	}
	go func() {
		if _, _, err := s.deps.Screener.Run(r.Context()); err != nil {
			log.Printf("screen refresh failed: %v", err)
			return
		}
		rows, generatedMS, scanned := s.deps.Screener.Cached()
		s.deps.Hub.Broadcast(map[string]any{
			"type":            "screen",
			"ts_ms":           generatedMS,
			"symbols_scanned": scanned,
			"rows":            rows,
		})
	}()
	writeJSON(w, http.StatusAccepted, map[string]any{"status": "scheduled"})
}

func (s *Server) handleSurvey(w http.ResponseWriter, r *http.Request) {
	if s.deps.Survey == nil {
		writeJSON(w, http.StatusOK, map[string]any{"type": "survey", "setups": []any{}})
		return
	}
	writeJSON(w, http.StatusOK, s.deps.Survey.Current())
}

func (s *Server) handleSurveyRefresh(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST required", http.StatusMethodNotAllowed)
		return
	}
	if s.deps.Survey == nil {
		writeJSON(w, http.StatusAccepted, map[string]any{"status": "unavailable"})
		return
	}
	go func() {
		if err := s.deps.Survey.Refresh(); err != nil {
			log.Printf("survey refresh failed: %v", err)
			return
		}
		s.deps.Hub.Broadcast(s.deps.Survey.Current())
	}()
	writeJSON(w, http.StatusAccepted, map[string]any{"status": "scheduled"})
}

// handleEvents streams Server-Sent Events to the dashboard.
func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache, no-transform")
	w.Header().Set("Connection", "keep-alive")
	// Tell any intermediary not to buffer, which would defeat the point.
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	sub := s.deps.Hub.Subscribe()
	defer sub.Close()

	// Greet the client with server time and effective config so the UI can
	// render thresholds without a second round trip.
	send := func(payload any) bool {
		data, err := json.Marshal(payload)
		if err != nil {
			return false
		}
		if _, err := fmt.Fprintf(w, "data: %s\n\n", data); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}
	if !send(map[string]any{
		"type":           "hello",
		"server_time_ms": time.Now().UnixMilli(),
		"config":         s.deps.ConfigSummary,
	}) {
		return
	}

	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-heartbeat.C:
			// A comment frame keeps the connection alive through proxies
			// without confusing the EventSource parser.
			if _, err := fmt.Fprint(w, ": ping\n\n"); err != nil {
				return
			}
			flusher.Flush()
		case msg, ok := <-sub.C():
			if !ok {
				return
			}
			if _, err := fmt.Fprintf(w, "data: %s\n\n", msg); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

// handleStatic serves the built dashboard, falling back to index.html so
// client-side routes work on refresh.
func (s *Server) handleStatic(w http.ResponseWriter, r *http.Request) {
	if s.deps.Static == nil {
		http.Error(w, "dashboard not built", http.StatusNotFound)
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/")
	if path == "" {
		path = "index.html"
	}
	// fs.File is not an io.ReadSeeker, so http.ServeContent cannot be used
	// directly. Dashboard assets are small, so buffering is fine and keeps the
	// SPA fallback trivial.
	if !serveAsset(w, s.deps.Static, path) {
		if !serveAsset(w, s.deps.Static, "index.html") {
			http.Error(w, "dashboard not built", http.StatusNotFound)
			return
		}
	}
}

func serveAsset(w http.ResponseWriter, fsys fs.FS, name string) bool {
	file, err := fsys.Open(name)
	if err != nil {
		return false
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || info.IsDir() {
		return false
	}
	data, err := io.ReadAll(file)
	if err != nil {
		return false
	}
	contentType := mime.TypeByExtension(filepath.Ext(name))
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	// Vite emits content-hashed asset names, so they are safe to cache hard;
	// index.html must not be, or a redeploy would never be picked up.
	if strings.Contains(name, "/assets/") {
		w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	} else {
		w.Header().Set("Cache-Control", "no-cache")
	}
	if _, err := w.Write(data); err != nil {
		log.Printf("api: static write failed for %s: %v", name, err)
	}
	return true
}

func queryInt(r *http.Request, key string, fallback int) int {
	raw := r.URL.Query().Get(key)
	if raw == "" {
		return fallback
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return fallback
	}
	return n
}
