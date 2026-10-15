package api

import (
	"encoding/json"
	"net/http"
)

// handleDetectors lists the registered pluggable detection models, and toggles
// them on POST.
//
//   GET  /api/detectors                      -> every detector + its params + counters
//   POST /api/detectors  {"name":"...","enabled":false}
func (s *Server) handleDetectors(w http.ResponseWriter, r *http.Request) {
	if s.deps.Detectors == nil {
		writeJSON(w, http.StatusOK, map[string]any{"type": "detectors", "detectors": []any{}})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusOK, map[string]any{
			"type":      "detectors",
			"detectors": s.deps.Detectors.Describe(),
		})
		return
	}

	var body struct {
		Name    string `json:"name"`
		Enabled *bool  `json:"enabled"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if body.Name == "" || body.Enabled == nil {
		http.Error(w, "name and enabled are required", http.StatusBadRequest)
		return
	}
	if !s.deps.Detectors.SetEnabled(body.Name, *body.Enabled) {
		http.Error(w, "unknown detector: "+body.Name, http.StatusNotFound)
		return
	}
	descriptors := s.deps.Detectors.Describe()
	s.deps.Hub.Broadcast(map[string]any{
		"type":      "detectors",
		"detectors": descriptors,
	})
	writeJSON(w, http.StatusOK, map[string]any{
		"type":      "detectors",
		"detectors": descriptors,
	})
}
