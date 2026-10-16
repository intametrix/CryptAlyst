package api

import (
	"context"
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/flow"
	"pumpdump/backend/internal/forecast"
	"pumpdump/backend/internal/levels"
)

// handleOutlook serves the live assessment of one symbol: whether an event is
// in progress, how long it may run before reverting, the next-few-seconds
// distribution, and the structural levels above and below price.
//
// It is deliberately a separate endpoint from /api/klines: the outlook depends
// on live flow state that the chart does not need, and the chart must keep
// rendering even when the outlook cannot be computed.
func (s *Server) handleOutlook(w http.ResponseWriter, r *http.Request) {
	if s.deps.Market == nil || s.deps.Outlook == nil {
		http.Error(w, "outlook unavailable", http.StatusServiceUnavailable)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	interval := strings.TrimSpace(r.URL.Query().Get("interval"))
	if interval == "" {
		interval = "1m"
	}
	if symbol == "" || len(symbol) > 24 {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()

	var klines []binance.Kline
	var err error
	if s.deps.KlinesCache != nil {
		klines, err = s.deps.KlinesCache.Get(ctx, s.deps.Market, klinesKey{symbol, interval, 300})
	} else {
		klines, err = s.deps.Market.Klines(ctx, symbol, interval, 300)
	}
	if err != nil {
		http.Error(w, "klines unavailable: "+err.Error(), http.StatusBadGateway)
		return
	}

	now := time.Now().UnixMilli()

	// Live flow state for this symbol, if it is tracked.
	var row flow.Candidate
	if s.deps.Engine != nil {
		for _, candidate := range s.deps.Engine.Snapshot(0, now) {
			if candidate.Symbol == symbol {
				row = candidate
				break
			}
		}
	}

	intervalMS := intervalMillis[interval]
	supports, resistances := levels.Compute(klines, intervalMS, levels.DefaultParams())
	fc := forecast.Compute(klines, intervalMS, forecast.DefaultParams())

	result := s.deps.Outlook.Compute(symbol, now, row, klines, intervalMS, supports, resistances, fc)
	writeJSON(w, http.StatusOK, map[string]any{
		"type":     "outlook",
		"symbol":   symbol,
		"interval": interval,
		"ts_ms":    now,
		"outlook":  result,
		"forecast": fc,
		"model":    s.deps.Outlook.ModelStatus(),
	})
}

// moveOfCandidate returns the best available short-horizon move, preferring the
// window the onset engine actually acts on.
func moveOfCandidate(row flow.Candidate) (float64, bool) {
	if row.Change10s != nil {
		return *row.Change10s, true
	}
	if row.Change60s != nil {
		return *row.Change60s, true
	}
	return 0, false
}
