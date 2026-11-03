package api

import (
	"context"
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/binance"
)

// Forward projection: the forecast past the newest candle.
//
// Everything else here grades a model against history. This is the part a reader
// actually wants — where the model says price goes next — so it is returned with
// the timestamps of the bars it claims to predict.

func intervalMS(interval string) int64 {
	switch interval {
	case "5s":
		return 5_000
	case "30s":
		return 30_000
	case "1m":
		return 60_000
	case "5m":
		return 300_000
	case "15m":
		return 900_000
	case "1h":
		return 3_600_000
	case "4h":
		return 14_400_000
	case "1d":
		return 86_400_000
	}
	return 3_600_000
}

func (s *Server) handleForward(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := r.URL.Query().Get("interval")
	if interval == "" {
		interval = "4h"
	}
	method := strings.TrimSpace(r.URL.Query().Get("method"))
	if method == "" {
		method = "vwap"
	}
	lookback := queryInt(r, "lookback", 12)
	horizon := queryInt(r, "horizon", 6)
	if horizon < 1 || horizon > 240 {
		horizon = 6
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	if s.deps.Market == nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "market client unavailable"})
		return
	}
	bars, err := s.barsFor(ctx, symbol, interval, 700)
	if err != nil || len(bars) < 120 {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "not enough bars"})
		return
	}

	path, ok := pathForLookback(method, bars[:], horizon, lookback)
	if !ok || len(path) != horizon {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "model could not produce a forward path"})
		return
	}

	step := intervalMS(interval)
	last := bars[len(bars)-1]
	times := make([]int64, horizon)
	for i := range times {
		times[i] = last.OpenTime + int64(i+1)*step
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "symbol": symbol, "interval": interval,
		"method": method, "lookback": lookback, "horizon": horizon,
		"from_ms": last.OpenTime, "last_close": last.Close,
		"times": times, "values": path,
	})
}

var _ = binance.Kline{}
