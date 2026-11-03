package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/forecastlab"
)

// History coverage and backfill.
//
// A forecast can only be judged where history exists, so the UI has to be able
// to ask two questions: how much data do we actually have for this symbol, and
// can we fetch more before drawing a conclusion from too little.

// historyCoverage is how many bars exist and what period they span.
type historyCoverage struct {
	Symbol     string  `json:"symbol"`
	Interval   string  `json:"interval"`
	Bars       int     `json:"bars"`
	FromMS     int64   `json:"from_ms"`
	ToMS       int64   `json:"to_ms"`
	SpanHours  float64 `json:"span_hours"`
	SpanDays   float64 `json:"span_days"`
	MaxFetch   int     `json:"max_fetch"`
	Sufficient bool    `json:"sufficient"`
}

// handleHistoryCoverage reports the depth of history available for a symbol.
func (s *Server) handleHistoryCoverage(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	interval := strings.TrimSpace(r.URL.Query().Get("interval"))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	if interval == "" {
		interval = "1h"
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}
	limit := queryInt(r, "limit", 400)
	if limit < 10 {
		limit = 10
	}
	if limit > 1000 {
		limit = 1000
	}

	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()

	pts, _ := s.labSeries(ctx, symbol, interval, limit)
	cov := historyCoverage{Symbol: symbol, Interval: interval, MaxFetch: 1000}
	if len(pts) > 0 {
		cov.Bars = len(pts)
		cov.FromMS = pts[0].T
		cov.ToMS = pts[len(pts)-1].T
		hours := float64(cov.ToMS-cov.FromMS) / 3_600_000
		cov.SpanHours = round2(hours)
		cov.SpanDays = round2(hours / 24)
	}
	// "Enough" is deliberately modest: the lab needs 40 training bars plus a
	// holdout, and the panel will warn rather than refuse below that.
	cov.Sufficient = cov.Bars >= 60

	writeJSON(w, http.StatusOK, cov)
}

// handleHistoryDownload fetches a deeper window on request and reports the new
// coverage, so the user can explicitly pull more before forecasting.
func (s *Server) handleHistoryDownload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Symbol   string `json:"symbol"`
		Interval string `json:"interval"`
		Bars     int    `json:"bars"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(body.Symbol))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := body.Interval
	if interval == "" {
		interval = "1h"
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}
	if s.deps.Market == nil {
		http.Error(w, "market client unavailable", http.StatusServiceUnavailable)
		return
	}
	bars := body.Bars
	if bars < 100 {
		bars = 100
	}
	if cap := IntervalBarCap(interval); bars > cap {
		bars = cap
	}
	if bars > 1000 {
		bars = 1000
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	// Bypass the search cache: this is an explicit request for more data, so it
	// must actually go and get it.
	rows, err := s.barsFor(ctx, symbol, interval, bars)
	if err != nil {
		http.Error(w, "download failed: "+err.Error(), http.StatusBadGateway)
		return
	}
	pts := make([]forecastlab.Point, 0, len(rows))
	for _, k := range rows {
		pts = append(pts, forecastlab.Point{T: k.OpenTime, V: k.Close})
	}
	s.storeLabSeries(symbol, interval, pts)

	cov := historyCoverage{Symbol: symbol, Interval: interval, MaxFetch: IntervalBarCap(interval)}
	if len(pts) > 0 {
		cov.Bars = len(pts)
		cov.FromMS = pts[0].T
		cov.ToMS = pts[len(pts)-1].T
		hours := float64(cov.ToMS-cov.FromMS) / 3_600_000
		cov.SpanHours = round2(hours)
		cov.SpanDays = round2(hours / 24)
	}
	cov.Sufficient = cov.Bars >= 60
	writeJSON(w, http.StatusOK, cov)
}
