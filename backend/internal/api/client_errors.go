package api

import (
	"net/http"
	"sync"
	"time"
)

// A small, visible record of render failures.
//
// The ErrorBoundary already posts each failure to the server and it lands in the
// log, which nobody reads while using the dashboard. These are the same reports,
// kept in memory and exposed so the UI can show that something broke instead of
// leaving a grey panel with no explanation.

type clientError struct {
	Where     string `json:"where"`
	Message   string `json:"message"`
	Href      string `json:"href"`
	AtMS      int64  `json:"at_ms"`
	Component string `json:"component,omitempty"`
}

var (
	clientErrMu sync.Mutex
	clientErrs  []clientError
)

const maxClientErrors = 50

// recordClientError stores a report, newest first, bounded.
func recordClientError(e clientError) {
	clientErrMu.Lock()
	defer clientErrMu.Unlock()
	e.AtMS = time.Now().UnixMilli()
	clientErrs = append([]clientError{e}, clientErrs...)
	if len(clientErrs) > maxClientErrors {
		clientErrs = clientErrs[:maxClientErrors]
	}
}

// handleClientErrors lists them for the dashboard badge.
func (s *Server) handleClientErrors(w http.ResponseWriter, r *http.Request) {
	clientErrMu.Lock()
	list := append([]clientError(nil), clientErrs...)
	clientErrMu.Unlock()
	ok(w, map[string]any{"errors": list, "count": len(list)})
}

// handleClientErrorsClear empties the list once the reader has seen it.
func (s *Server) handleClientErrorsClear(w http.ResponseWriter, r *http.Request) {
	clientErrMu.Lock()
	clientErrs = nil
	clientErrMu.Unlock()
	ok(w, nil)
}
