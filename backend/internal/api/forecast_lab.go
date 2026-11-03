package api

import (
	"context"
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/forecastlab"
)

// handleForecastLab grades every forecasting model against real bars and
// returns the leaderboard plus the forecast-vs-real windows the UI charts.
//
// This is the endpoint that keeps the forecast honest: the chart shows the
// model's line next to the closes that actually followed, and the ranking is
// computed on data the models never saw.
func (s *Server) handleForecastLab(w http.ResponseWriter, r *http.Request) {
	if s.deps.Market == nil {
		http.Error(w, "market client unavailable", http.StatusServiceUnavailable)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" || len(symbol) > 24 {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := strings.TrimSpace(r.URL.Query().Get("interval"))
	if interval == "" {
		interval = "1h"
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}
	limit := queryInt(r, "limit", 400)
	if limit < 40 {
		limit = 40
	}
	if limit > 1000 {
		limit = 1000
	}
	horizon := queryInt(r, "horizon", 12)
	if horizon < 1 {
		horizon = 1
	}
	if horizon > 720 {
		horizon = 720
	}
	trainBars := queryInt(r, "train", 0)
	if trainBars > 1000 {
		trainBars = 1000
	}
	windows := queryInt(r, "windows", 8)
	if windows < 1 {
		windows = 1
	}
	if windows > 40 {
		windows = 40
	}

	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()

	// The same bars the background sweeper uses, so a page refresh during a
	// search does not force another download.
	series, fetched := s.labSeries(ctx, symbol, interval, limit)
	if len(series) == 0 {
		http.Error(w, "klines unavailable", http.StatusBadGateway)
		return
	}

	study := forecastlab.EvaluateWindow(symbol, interval, series, horizon, windows, 40, trainBars)
	track := s.recordStudyWith(study, horizon, fetched)
	s.startLabSweeper()

	writeJSON(w, http.StatusOK, map[string]any{
		"symbol": study.Symbol, "interval": study.Interval, "horizon": study.Horizon,
		"points": study.Points, "best": study.Best, "windows": study.Windows,
		"scores": study.Scores, "tracker": track, "train_bars": trainBars,
	})
}
