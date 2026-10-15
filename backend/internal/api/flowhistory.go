// NOTE(DeepSeek): this handler is written but NOT wired up, because routes
// are hardcoded in server.go's Routes() and must not be modified from this
// change. To enable the endpoint, add this one line in Routes(), next to the
// other /api/flow route (server.go registers "/api/flow" around line 186):
//
//	mux.HandleFunc("/api/flow/history", s.handleFlowHistory)
//
// Until then the function compiles but is unreachable.

package api

import (
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/flow"
)

// FlowHistoryResponse is the /api/flow/history payload.
type FlowHistoryResponse struct {
	Type      string         `json:"type"`
	Symbol    string         `json:"symbol"`
	WindowSec float64        `json:"window_sec"`
	TSMS      int64          `json:"ts_ms"`
	Bins      []flow.FlowBin `json:"bins"`
}

func (s *Server) handleFlowHistory(w http.ResponseWriter, r *http.Request) {
	engine := s.deps.Engine
	if engine == nil {
		http.Error(w, "flow engine unavailable", http.StatusServiceUnavailable)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" || len(symbol) > 24 {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	limit := queryInt(r, "limit", 200)
	if limit < 50 {
		limit = 50
	}
	if limit > 600 {
		limit = 600
	}
	bins := engine.History(symbol, limit)
	if len(bins) == 0 {
		http.Error(w, "no flow data for symbol: "+symbol, http.StatusNotFound)
		return
	}
	now := time.Now().UnixMilli()
	writeJSON(w, http.StatusOK, FlowHistoryResponse{
		Type:      "flow_history",
		Symbol:    symbol,
		WindowSec: engine.Config().WindowSec,
		TSMS:      now,
		Bins:      bins,
	})
}
