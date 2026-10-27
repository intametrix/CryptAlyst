package api

import (
	"encoding/json"
	"net/http"
	"sync"
	"time"
)

// AgentInfo is one enrolled CryptALAS agent as reported by its /gpu endpoint.
type AgentInfo struct {
	ID         string `json:"id"`          // stable id; defaults to Host when unset
	Host       string `json:"host"`        // base URL the platform reaches the agent on
	Device     string `json:"device"`      // auto | cpu | gpu | cuda | metal
	Arch       string `json:"arch"`        // GOARCH
	NumCPU     int    `json:"num_cpu"`     // NumCPU
	GoVersion  string `json:"go_version"`  // runtime.Version()
	GitSHA     string `json:"git_sha"`     // the commit the agent runs
	GPUPresent bool   `json:"gpu_present"` // true when device is gpu/cuda/metal
	FirstSeen  int64  `json:"first_seen_ms"`
	LastSeen   int64  `json:"last_seen_ms"`
}

// AgentRegistry is the in-memory directory of enrolled agents. It is keyed by
// the agent's id (falling back to its host), so a re-enroll refreshes rather
// than duplicates.
type AgentRegistry struct {
	mu     sync.RWMutex
	agents map[string]*AgentInfo
}

// NewAgentRegistry returns an empty registry.
func NewAgentRegistry() *AgentRegistry {
	return &AgentRegistry{agents: map[string]*AgentInfo{}}
}

// Upsert records or refreshes an agent and returns the stored copy.
func (r *AgentRegistry) Upsert(info AgentInfo) AgentInfo {
	r.mu.Lock()
	defer r.mu.Unlock()
	now := time.Now().UnixMilli()
	key := info.ID
	if key == "" {
		key = info.Host
	}
	info.ID = key
	if cur, ok := r.agents[key]; ok {
		info.FirstSeen = cur.FirstSeen // preserve the original enrolment time
	} else {
		info.FirstSeen = now
	}
	info.LastSeen = now
	r.agents[key] = &info
	return info
}

// Touch updates the heartbeat for an agent id, returning false if unknown.
func (r *AgentRegistry) Touch(id string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	cur, ok := r.agents[id]
	if !ok {
		return false
	}
	cur.LastSeen = time.Now().UnixMilli()
	return true
}

// List returns a snapshot, most recently seen first.
func (r *AgentRegistry) List() []AgentInfo {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]AgentInfo, 0, len(r.agents))
	for _, a := range r.agents {
		out = append(out, *a)
	}
	// insertion sort by LastSeen desc (the list is small)
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j].LastSeen > out[j-1].LastSeen; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// handleAgents lists every enrolled agent.
func (s *Server) handleAgents(w http.ResponseWriter, r *http.Request) {
	if s.deps.Agents == nil {
		writeJSON(w, http.StatusOK, map[string]any{"agents": []AgentInfo{}})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"agents": s.deps.Agents.List()})
}

// handleAgentsRegister enrolls (or refreshes) one agent.
func (s *Server) handleAgentsRegister(w http.ResponseWriter, r *http.Request) {
	if s.deps.Agents == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "agent registry disabled"})
		return
	}
	var info AgentInfo
	if err := json.NewDecoder(r.Body).Decode(&info); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if info.Host == "" {
		http.Error(w, "host is required", http.StatusBadRequest)
		return
	}
	writeJSON(w, http.StatusOK, s.deps.Agents.Upsert(info))
}

// handleAgentsHeartbeat refreshes an agent's last-seen timestamp.
func (s *Server) handleAgentsHeartbeat(w http.ResponseWriter, r *http.Request) {
	if s.deps.Agents == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "agent registry disabled"})
		return
	}
	var body struct {
		ID string `json:"id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if body.ID == "" {
		http.Error(w, "id is required", http.StatusBadRequest)
		return
	}
	if !s.deps.Agents.Touch(body.ID) {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "unknown agent"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}
