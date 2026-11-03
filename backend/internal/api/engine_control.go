package api

import (
	"net/http"
	"strings"
)

// Control the training loop from the UI.
//
// Training is meant to run until a human stops it, so the stop has to be a real
// switch rather than closing a tab: the workers keep their state and resume
// where they were.
func (s *Server) handleEngineControl(w http.ResponseWriter, r *http.Request) {
	want := strings.TrimSpace(r.URL.Query().Get("training"))
	switch want {
	case "on":
		engine.SetPaused(false)
	case "off":
		engine.SetPaused(true)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "training": !engine.Paused(),
	})
}
