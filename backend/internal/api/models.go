package api

import "net/http"

// GET /api/models          -> per-model health, so a STOPPED model is visible
// GET /api/models/signals  -> recent signals across every model, newest first
//
// Health is the more important of the two. A model that has silently stopped
// evaluating and a model that is finding nothing produce identical empty signal
// lists, and that ambiguity is what let an empty taker buffer go unnoticed while
// two features read confident zeroes.
func (s *Server) handleModels(w http.ResponseWriter, r *http.Request) {
	if s.deps.Models == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "model runner not configured"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"models": s.deps.Models.Health()})
}

func (s *Server) handleModelSignals(w http.ResponseWriter, r *http.Request) {
	if s.deps.Models == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "model runner not configured"})
		return
	}
	limit := queryInt(r, "limit", 50)
	sigs := s.deps.Models.Signals(limit)
	writeJSON(w, http.StatusOK, map[string]any{"signals": sigs, "count": len(sigs)})
}
