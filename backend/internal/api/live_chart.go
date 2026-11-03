package api

import (
	"context"

	"net/http"
	"pumpdump/backend/internal/binance"
	"strings"
	"time"
)

// Live chart data: the recent price, the next-step forecast, and the funnel.
//
// The optimiser panel shows a model being chosen. This is the other question a
// reader has — "so what happens next?" — so the recent bars, the forecast past
// the last one, and the uncertainty cone around it are served together, small
// enough to poll every few seconds.
func (s *Server) handleLiveChart(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := r.URL.Query().Get("interval")
	if interval == "" {
		interval = "1h"
	}
	method := strings.TrimSpace(r.URL.Query().Get("method"))
	if method == "" {
		if cfg, ok := savedFor(symbol); ok {
			method = cfg.Method
		} else {
			method = "vwap"
		}
	}
	lookback := queryInt(r, "lookback", 2)
	horizon := queryInt(r, "horizon", 6)
	history := queryInt(r, "bars", 90)
	if horizon < 1 || horizon > 60 {
		horizon = 6
	}
	if history < 30 || history > 400 {
		history = 90
	}

	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	if s.deps.Market == nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "market client unavailable"})
		return
	}
	// The store answers first; five and thirty second frames are aggregated there.
	bars, err := s.barsFor(ctx, symbol, interval, history+120)
	// A thirty-second view can only reach as far back as the one-second feed
	// allows, which is several minutes — not the 120 bars the wider intervals get.
	minBars := 60
	if interval == "30s" {
		minBars = 25
	}
	if err != nil || len(bars) < minBars {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "not enough bars"})
		return
	}

	closes := make([]float64, len(bars))
	for i, b := range bars {
		closes[i] = b.Close
	}

	// The forecast past the newest candle.
	path, ok := pathForLookback(method, bars, horizon, lookback)
	if !ok || len(path) != horizon {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "model produced no path"})
		return
	}

	// The funnel: resampled futures from this symbol's own one-bar returns.
	p10, _, p50, _, p90, fanOK := bootstrapFan(closes, horizon, 400)

	step := intervalMS(interval)
	last := bars[len(bars)-1]
	shown := history
	if shown > len(bars)-horizon {
		shown = len(bars) - horizon
	}
	if shown < 20 {
		shown = len(bars)
	}
	times := make([]int64, shown)
	actual := make([]float64, shown)
	for i := 0; i < shown; i++ {
		b := bars[len(bars)-shown+i]
		times[i] = b.OpenTime
		actual[i] = b.Close
	}
	fTimes := make([]int64, horizon)
	fVals := make([]float64, horizon)
	for i := 0; i < horizon; i++ {
		fTimes[i] = last.OpenTime + int64(i+1)*step
		fVals[i] = path[i]
	}

	out := map[string]any{
		"ok": true, "symbol": symbol, "interval": interval,
		"method": method, "lookback": lookback, "horizon": horizon,
		"last_close": last.Close, "last_t": last.OpenTime,
		"times": times, "actual": actual,
		"forecast_times": fTimes, "forecast": fVals,
	}
	if fanOK {
		out["fan"] = map[string]any{"p10": p10, "p50": p50, "p90": p90}
	}
	// Individual branches, so the projection fans out visibly instead of being a
	// single line with a shaded band around it.
	if branches := samplePaths(closes, horizon, 12); len(branches) > 0 {
		out["branches"] = branches
	}
	writeJSON(w, http.StatusOK, out)
}

// aggregateBars rolls one-second bars into wider buckets, so the live chart can
// offer five and thirty second candles that the feed itself does not publish.
func aggregateBars(in []binance.Kline, stepMS int64) []binance.Kline {
	if stepMS <= 0 || len(in) == 0 {
		return in
	}
	out := make([]binance.Kline, 0, len(in)/int(stepMS/1000)+1)
	var cur *binance.Kline
	for _, b := range in {
		bucket := (b.OpenTime / stepMS) * stepMS
		if cur == nil || cur.OpenTime != bucket {
			if cur != nil {
				out = append(out, *cur)
			}
			nb := b
			nb.OpenTime = bucket
			cur = &nb
			continue
		}
		if b.High > cur.High {
			cur.High = b.High
		}
		if b.Low < cur.Low {
			cur.Low = b.Low
		}
		cur.Close = b.Close
		cur.Volume += b.Volume
		cur.CloseTime = b.CloseTime
	}
	if cur != nil {
		out = append(out, *cur)
	}
	return out
}
