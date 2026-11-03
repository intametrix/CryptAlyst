package mocks

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
)

// Agent is a fake CryptALAS box: it answers the agent protocol so the sweep and
// verify paths can be driven without a GPU.
type Agent struct {
	Server *httptest.Server

	mu sync.Mutex
	// Jobs records what the platform asked for.
	Jobs []map[string]any
	// Result is the payload returned for a sweep job. When nil the agent answers
	// with an empty result list.
	Result map[string]any
	// Status forces an HTTP status on /jobs; 0 means 200.
	Status int
	// Seen records every path requested.
	Seen []string
}

// NewAgent starts a fake agent and returns it; call Close when done.
func NewAgent() *Agent {
	a := &Agent{}
	a.Server = httptest.NewServer(http.HandlerFunc(a.handle))
	return a
}

// Close stops the fake agent.
func (a *Agent) Close() { a.Server.Close() }

// URL is the host the platform should call.
func (a *Agent) URL() string { return a.Server.URL }

// RequestCount returns how many sweep jobs were posted.
func (a *Agent) RequestCount() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return len(a.Jobs)
}

// Paths returns every path the platform requested.
func (a *Agent) Paths() []string {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make([]string, len(a.Seen))
	copy(out, a.Seen)
	return out
}

func (a *Agent) handle(w http.ResponseWriter, r *http.Request) {
	a.mu.Lock()
	a.Seen = append(a.Seen, r.URL.Path)
	status := a.Status
	a.mu.Unlock()

	if status != 0 && status != http.StatusOK {
		w.WriteHeader(status)
		_, _ = w.Write([]byte(`{"ok":false,"error":"mock agent refused"}`))
		return
	}

	switch r.URL.Path {
	case "/gpu":
		writeJSON(w, map[string]any{
			"device": "cuda", "arch": "amd64", "num_cpu": 16,
			"gpu": "mock GPU", "gpu_mem_mib": 11264, "computing": "",
			"version": "mock-1.0", "max_jobs": 4,
		})
	case "/jobs", "/sweep":
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		a.mu.Lock()
		a.Jobs = append(a.Jobs, body)
		result := a.Result
		a.mu.Unlock()
		if result == nil {
			result = map[string]any{"ok": true, "results": []any{}, "series": []any{}}
		}
		writeJSON(w, result)
	default:
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"ok":false,"error":"not found"}`))
	}
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("content-type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
