package api

import (
	"encoding/json"
	"net/http"

	"pumpdump/backend/internal/turtle"
)

// Turtle settings.
//
// GET  /api/turtle/config          -> the live rule set plus the book defaults
// POST /api/turtle/config          -> replace the rule set
// GET  /api/turtle/session-log     -> the append-only history
//
// The POST is ALL OR NOTHING, matching the rule already used for detection
// config: a partially applied rule set is worse than a rejected one, because the
// session would run a combination no one chose and the log would attribute the
// result to it.
//
// CHANGES ARE REFUSED WHILE A SESSION IS RUNNING. Not a technical limitation --
// the engine could accept them -- but a measurement one: a session whose rules
// changed mid-run produces a PnL that belongs to no single strategy, and the
// whole point of the durable log is to attribute results to rules.
func (s *Server) handleTurtleConfig(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, http.StatusOK, map[string]any{
			"config":   s.deps.Turtle.BookConfig(),
			"defaults": turtle.BookDefaults(),
			"state":    s.deps.Turtle.Session().State,
			"note":     "editable only while the session is idle or stopped; a mid-session rule change would make the PnL belong to no single strategy",
		})
	case http.MethodPost:
		if s.deps.Turtle.Session().State == turtle.StateRunning {
			writeJSON(w, http.StatusConflict, map[string]string{
				"error": "a session is running; stop it before changing the rules, so the PnL can be attributed to the rules that produced it",
			})
			return
		}
		var incoming turtle.BookConfig
		dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&incoming); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"error": "invalid config: " + err.Error(),
			})
			return
		}
		if problems := turtle.ValidateBookConfig(incoming); len(problems) > 0 {
			writeJSON(w, http.StatusBadRequest, map[string]any{
				"error": "config rejected", "problems": problems,
			})
			return
		}
		s.deps.Turtle.SetBookConfig(incoming)
		writeJSON(w, http.StatusOK, map[string]any{"config": s.deps.Turtle.BookConfig()})
	default:
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "use GET or POST"})
	}
}

// handleTurtleSessionLog serves the append-only history, newest first, with the
// malformed-line count alongside it -- a truncated tail is a real condition and a
// reader that hides it makes the history look complete when it is not.
func (s *Server) handleTurtleSessionLog(w http.ResponseWriter, r *http.Request) {
	path := s.deps.TurtleLogPath
	if path == "" {
		writeJSON(w, http.StatusOK, map[string]any{"events": []any{}, "note": "no session log configured"})
		return
	}
	limit := queryInt(r, "limit", 200)
	events, malformed, err := turtle.ReadSessionLog(path, limit)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"events": []any{}, "error": err.Error(),
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"events": events, "count": len(events), "malformed": malformed,
		"stats": s.deps.Turtle.StoreStats(),
	})
}
