package api

import (
	"net/http"
	"time"
)

// handleHeat serves the current per-symbol interestingness scores.
//
// The same payload is pushed on the SSE stream as a "heat" message roughly
// every two seconds; this endpoint exists so the UI can seed itself on mount
// without waiting for the next tick.
func (s *Server) handleHeat(w http.ResponseWriter, r *http.Request) {
	if s.deps.Heat == nil {
		writeJSON(w, http.StatusOK, map[string]any{"type": "heat", "rows": []any{}})
		return
	}
	limit := queryInt(r, "limit", 200)
	now := time.Now().UnixMilli()
	rows := s.deps.Heat.Compute(now, limit)
	if rows == nil {
		rows = nil
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"type":   "heat",
		"ts_ms":  now,
		"params": s.deps.Heat.Params(),
		"rows":   rows,
	})
}
