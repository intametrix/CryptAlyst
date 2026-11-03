package api

import (
	"net/http"
	"strings"
)

// Add a symbol to the live engine from the UI.
//
// The engine only optimises what it has been told about. Without this, testing a
// symbol meant an API call that produced numbers with no chart anywhere.
func (s *Server) handleLiveSymbols(w http.ResponseWriter, r *http.Request) {
	raw := strings.TrimSpace(r.URL.Query().Get("symbols"))
	if raw == "" {
		writeJSON(w, http.StatusOK, engine.snapshot(s))
		return
	}
	for _, part := range strings.Split(raw, ",") {
		sym := strings.ToUpper(strings.TrimSpace(part))
		if sym == "" {
			continue
		}
		engine.AddSymbol(s, sym)
	}
	writeJSON(w, http.StatusOK, engine.snapshot(s))
}
