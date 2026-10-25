package gpuagent

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"pumpdump/backend/internal/envelope"
	"pumpdump/backend/internal/graph"
)

// SweepRequest is the transport body: a job plus its inline history. The
// history is bundled here for the small/synthetic case; a large history uses a
// HistoryRef the agent resolves itself.
type SweepRequest struct {
	Job      envelope.SweepJob `json:"job"`
	Contexts []graph.Context   `json:"contexts"`
	Closes   []float64         `json:"closes"`
}

// Client posts sweep jobs to a GPU agent over HTTP.
type Client struct {
	BaseURL string
	HTTP    *http.Client
}

// NewClient returns a client for the given agent base URL.
func NewClient(baseURL string) *Client {
	return &Client{BaseURL: baseURL, HTTP: &http.Client{Timeout: 60 * time.Second}}
}

// Sweep posts the job and returns the result.
func (c *Client) Sweep(job envelope.SweepJob, contexts []graph.Context, closes []float64) (envelope.SweepResult, error) {
	body, err := json.Marshal(SweepRequest{Job: job, Contexts: contexts, Closes: closes})
	if err != nil {
		return envelope.SweepResult{}, err
	}
	resp, err := c.HTTP.Post(c.BaseURL+"/sweep", "application/json", bytes.NewReader(body))
	if err != nil {
		return envelope.SweepResult{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return envelope.SweepResult{}, fmt.Errorf("agent: %s", resp.Status)
	}
	var out envelope.SweepResult
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return envelope.SweepResult{}, err
	}
	return out, nil
}

// HandleSweep serves a /sweep request: decode, run, respond. Both the agent and
// a test server use it, so the client and server agree on the wire format.
func HandleSweep(w http.ResponseWriter, r *http.Request) {
	var req SweepRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	res, err := Run(req.Job, req.Contexts, req.Closes)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, res)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
