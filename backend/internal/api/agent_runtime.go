package api

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"time"
)

// Remote runtime management.
//
// A compute box can be on the wrong Python, or have a CPU-only build of torch,
// and that is invisible from the platform unless the agent is asked. These two
// endpoints make the question answerable and the fix actionable:
//
//	GET  /api/agent/python   — what each interpreter on the box can do
//	POST /api/agent/install  — install packages into the interpreter the
//	                           forecast runner actually uses
//
// The install endpoint runs pip. It is deliberately explicit about the command
// it ran and returns the tail of the output, because "it did not work" is not a
// usable answer. There is no authentication on the agent, so this is only safe
// on a trusted network — stated here rather than discovered later.

// handleAgentPython reports the interpreter/runtime situation on an agent.
func (s *Server) handleAgentPython(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "GET only", http.StatusMethodNotAllowed)
		return
	}
	target := strings.TrimRight(r.URL.Query().Get("agent"), "/")
	if target == "" {
		if c := s.liveAgents(agentFreshMS); len(c) > 0 {
			target = c[0]
		}
	}
	if target == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "no live agents"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 90*time.Second)
	defer cancel()

	// Ask the agent to run its own probe through the forecast runner, which
	// already knows every interpreter candidate worth checking.
	body := map[string]any{
		"model": "probe", "type": "forecast", "device": "gpu",
		"payload": map[string]any{"probe": true, "series": []any{}, "horizon": 1},
	}
	raw, _ := json.Marshal(body)
	resp, err := http.Post(target+"/jobs", "application/json", bytes.NewReader(raw))
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	var created map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&created)
	resp.Body.Close()
	jobID, _ := created["id"].(string)

	deadline := time.Now().Add(80 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
		case <-time.After(700 * time.Millisecond):
		}
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, target+"/jobs/"+jobID, nil)
		rr, err := http.DefaultClient.Do(req)
		if err != nil {
			break
		}
		var st map[string]any
		_ = json.NewDecoder(rr.Body).Decode(&st)
		rr.Body.Close()
		status, _ := st["status"].(string)
		if status != "done" && status != "failed" {
			continue
		}
		result, _ := st["result"].(map[string]any)
		result["agent"] = target
		writeJSON(w, http.StatusOK, result)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "probe did not finish in time", "agent": target})
}

// handleAgentInstall installs python packages on an agent.
func (s *Server) handleAgentInstall(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Agent    string   `json:"agent"`
		Packages []string `json:"packages"`
		IndexURL string   `json:"index_url"`
		Upgrade  bool     `json:"upgrade"`
		Force    bool     `json:"force"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if len(body.Packages) == 0 {
		http.Error(w, "packages required", http.StatusBadRequest)
		return
	}
	// Only package names, flags are not accepted from the caller: this endpoint
	// must not become an arbitrary command runner.
	for _, p := range body.Packages {
		if p == "" || strings.HasPrefix(p, "-") || strings.ContainsAny(p, " \t\n;&|`$()") {
			http.Error(w, "invalid package name: "+p, http.StatusBadRequest)
			return
		}
	}
	if body.IndexURL != "" && !strings.HasPrefix(body.IndexURL, "https://") {
		http.Error(w, "index_url must be https", http.StatusBadRequest)
		return
	}
	target := strings.TrimRight(body.Agent, "/")
	if target == "" {
		if c := s.liveAgents(agentFreshMS); len(c) > 0 {
			target = c[0]
		}
	}
	if target == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "no live agents"})
		return
	}

	args := []string{"install"}
	if body.Force {
		// pip considers "torch==2.6.0" satisfied by "2.6.0+cpu": the local build
		// suffix is ignored, so a plain install silently keeps the CPU wheel.
		// A force-reinstall is the only way to actually replace it.
		args = append(args, "--force-reinstall")
	}
	if body.Upgrade {
		args = append(args, "--upgrade")
	}
	if body.IndexURL != "" {
		args = append(args, "--index-url", body.IndexURL)
	}
	args = append(args, body.Packages...)

	// Run it as a job so the agent applies its own capacity rules and the work
	// is visible in its job list like everything else.
	job := map[string]any{
		// No device: installing packages is not a compute job, and labelling it
		// "cpu" reads as if the agent had fallen back off the GPU.
		"model": "pip", "type": "install", "device": "",
		"payload": map[string]any{"pip_args": args},
	}
	raw, _ := json.Marshal(job)
	resp, err := http.Post(target+"/jobs", "application/json", bytes.NewReader(raw))
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	var created map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&created)
	resp.Body.Close()
	jobID, _ := created["id"].(string)
	if jobID == "" {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": "agent rejected the job", "agent": target})
		return
	}

	// pip can take minutes on a CUDA wheel, so wait longer than a forecast.
	deadline := time.Now().Add(10 * time.Minute)
	for time.Now().Before(deadline) {
		time.Sleep(2 * time.Second)
		req, err := http.NewRequest(http.MethodGet, target+"/jobs/"+jobID, nil)
		if err != nil {
			break
		}
		rr, err := http.DefaultClient.Do(req)
		if err != nil {
			break
		}
		var st map[string]any
		_ = json.NewDecoder(rr.Body).Decode(&st)
		rr.Body.Close()
		status, _ := st["status"].(string)
		if status != "done" && status != "failed" {
			continue
		}
		result, _ := st["result"].(map[string]any)
		result["agent"] = target
		result["job"] = jobID
		writeJSON(w, http.StatusOK, result)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": false, "error": "install still running after 10 minutes", "agent": target, "job": jobID,
	})
}

// handleClientError accepts a render failure from the dashboard and logs it, so
// a blank page becomes a readable stack trace in the server log instead of a
// mystery.
func (s *Server) handleClientError(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Where     string `json:"where"`
		Message   string `json:"message"`
		Stack     string `json:"stack"`
		Component string `json:"component_stack"`
		Href      string `json:"href"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}
	log.Printf("CLIENT ERROR in %q: %s\n  href: %s\n  stack: %s\n  components: %s",
		body.Where, body.Message, body.Href,
		strings.ReplaceAll(body.Stack, "\n", "\n    "),
		strings.ReplaceAll(body.Component, "\n", "\n    "))
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}
