package api

import (
	"net/http"
	"sort"
	"time"

	"pumpdump/backend/internal/flow"
)

// nowMS is the single clock the API hands the engine. Using time.Now() in one
// place and the engine's own clock in another is how a sampler ends up with a
// window that contains no ticks and reports a confident zero.
func nowMS() int64 { return time.Now().UnixMilli() }

// GET /api/movers           -> greatest 24h increases and decreases
// GET /api/moneyflow?symbol -> multi-interval flow with a BUY/SELL/HOLD conviction
//
// Both read data the engine already holds. The movers needed a struct change to
// carry the 24h fields through to the row; the money flow needed a sampler over
// the taker buffer. Neither fetches anything new from Binance, which is the point
// -- the 24h ticker for every subscribed symbol was already arriving and the
// change percentage was being discarded.
func (s *Server) handleMovers(w http.ResponseWriter, r *http.Request) {
	if s.deps.Engine == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "flow engine not configured"})
		return
	}
	n := queryInt(r, "limit", 10)
	if n < 1 || n > 100 {
		n = 10
	}
	minVol := queryFloat(r, "min_quote_volume", 500000)
	rows := s.deps.Engine.Snapshot(0, time.Now().UnixMilli())
	writeJSON(w, http.StatusOK, flow.RankMovers(rows, n, minVol))
}

// GET /api/impulse -> the volatility / price-action / volume read for every
// tracked symbol, strongest first. Three factors reported separately so a reader
// can see WHICH one fired; the dump side is deliberately more sensitive.
func (s *Server) handleImpulse(w http.ResponseWriter, r *http.Request) {
	if s.deps.Engine == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "flow engine not configured"})
		return
	}
	limit := queryInt(r, "limit", 15)
	rows := s.deps.Engine.Snapshot(0, nowMS())
	cfg := flow.DefaultImpulseConfig()
	all := make([]flow.Impulse, 0, len(rows))
	for _, row := range rows {
		if row.Price <= 0 {
			continue
		}
		all = append(all, flow.ReadImpulse(row, cfg))
	}
	sort.SliceStable(all, func(i, j int) bool { return all[i].Score > all[j].Score })
	fired := make([]flow.Impulse, 0, limit)
	for _, im := range all {
		if !im.Fired {
			continue
		}
		if len(fired) >= limit {
			break
		}
		fired = append(fired, im)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"impulses": fired, "count": len(fired), "scanned": len(all),
		"config": cfg,
		"note":   "dump thresholds are looser than pump thresholds; every dump threshold in the config is lower than its pump counterpart",
	})
}

func (s *Server) handleMoneyFlow(w http.ResponseWriter, r *http.Request) {
	if s.deps.Engine == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "flow engine not configured"})
		return
	}
	symbol := r.URL.Query().Get("symbol")
	if symbol == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "symbol is required"})
		return
	}
	conv, ok := s.deps.Engine.MoneyFlow(symbol, time.Now().UnixMilli())
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "no flow state for " + symbol})
		return
	}
	writeJSON(w, http.StatusOK, conv)
}
