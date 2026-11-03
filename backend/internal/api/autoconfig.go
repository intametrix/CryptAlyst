package api

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"time"

	"pumpdump/backend/internal/binance"
)

// Per-symbol model selection.
//
// The optimiser showed the best lookback differs by symbol — 12 bars on BTC, 100
// on XRP and DOGE. A single global default therefore cannot be right everywhere,
// so each symbol gets the configuration its own backtest chose, cached because
// the walk is expensive and the answer is stable for a while.

type autoPick struct {
	method   string
	lookback int
	r2       float64
	mape     float64
	at       time.Time
}

var (
	autoMu    sync.Mutex
	autoCache = map[string]autoPick{}
)

const autoTTL = 30 * time.Minute

// autoConfig returns the best configuration for a symbol, computing and caching
// it when stale.
func (s *Server) autoConfig(symbol, interval string, bars []binance.Kline, horizon int) (string, int, bool) {
	key := symbol + "|" + interval

	autoMu.Lock()
	if p, ok := autoCache[key]; ok && time.Since(p.at) < autoTTL {
		autoMu.Unlock()
		return p.method, p.lookback, true
	}
	autoMu.Unlock()

	type cand struct {
		method   string
		lookback int
	}
	cands := []cand{}
	for _, m := range []string{"naive", "hybrid", "vwap", "vwtrend", "vwdrift", "arima", "analog10", "trend50"} {
		if m == "vwap" || m == "vwtrend" || m == "vwdrift" {
			for _, lb := range []int{12, 24, 50, 100} {
				cands = append(cands, cand{m, lb})
			}
			continue
		}
		cands = append(cands, cand{m, 24})
	}

	best := autoPick{r2: -1e9, at: time.Now()}
	for _, c := range cands {
		_, m, ok := walkBacktest(bars, c.method, horizon, c.lookback)
		if !ok {
			continue
		}
		r2 := toF(m["r2"])
		if r2 > best.r2 {
			best = autoPick{method: c.method, lookback: c.lookback, r2: r2, mape: toF(m["mape_pct"]), at: time.Now()}
		}
	}
	if best.method == "" {
		return "", 0, false
	}

	autoMu.Lock()
	autoCache[key] = best
	autoMu.Unlock()
	return best.method, best.lookback, true
}

// handleAutoConfig reports what each symbol selected, so the choice is visible
// rather than buried inside a forecast.
func (s *Server) handleAutoConfig(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 240*time.Second)
	defer cancel()
	if s.deps.Market == nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "market client unavailable"})
		return
	}
	raw := strings.TrimSpace(r.URL.Query().Get("symbols"))
	if raw == "" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "symbols required"})
		return
	}
	interval := r.URL.Query().Get("interval")
	if interval == "" {
		interval = "1h"
	}
	horizon := queryInt(r, "horizon", 24)

	symbols := strings.Split(raw, ",")
	out := make([]map[string]any, 0, len(symbols))
	for _, sym := range symbols {
		sym = strings.ToUpper(strings.TrimSpace(sym))
		if sym == "" {
			continue
		}
		bars, err := s.barsFor(ctx, sym, interval, 320)
		if err != nil || len(bars) < 140+horizon {
			continue
		}
		m, lb, ok := s.autoConfig(sym, interval, bars, horizon)
		if !ok {
			continue
		}
		key := sym + "|" + interval
		autoMu.Lock()
		p := autoCache[key]
		autoMu.Unlock()
		out = append(out, map[string]any{
			"symbol": sym, "method": m, "lookback": lb,
			"r2": p.r2, "mape_pct": p.mape,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "interval": interval, "picks": out})
}
