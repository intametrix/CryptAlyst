package api

import (
	"bytes"
	"encoding/json"
	"net"
	"net/http"
	"sync"
	"time"
)

// AgentInfo is one enrolled CryptALAS agent as reported by its /gpu endpoint.
// NetIface is one network interface reported by the agent.
type NetIface struct {
	Name string `json:"name"`
	IP   string `json:"ip"`
	MAC  string `json:"mac"`
	Up   bool   `json:"up"`
}

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
	// Friendly name, actual IP as the platform sees it, and machine resources.
	Name     string `json:"name"`
	IP       string `json:"ip"`
	MemTotal uint64 `json:"mem_total"` // system memory in bytes (0 = unknown)
	GPUName  string `json:"gpu_name"`  // first GPU model ("" = none)
	GPUVRAM  string `json:"gpu_vram"`  // first GPU memory ("" = none)
	GPUCount int    `json:"gpu_count"` // number of GPUs
	// Live utilisation, capabilities, and running tasks.
	MemUsed      uint64     `json:"mem_used"`
	CPUUtil      float64    `json:"cpu_util"`
	GPUUtil      float64    `json:"gpu_util"`
	GPUMemUsed   uint64     `json:"gpu_mem_used"`
	Capabilities []string   `json:"capabilities"`
	Interfaces   []NetIface `json:"interfaces"`
	Privileges   []string   `json:"privileges"`
	NetRxBps     float64    `json:"net_rx_bps"`
	NetTxBps     float64    `json:"net_tx_bps"`
	DiskTotal    uint64     `json:"disk_total"`
	DiskFree     uint64     `json:"disk_free"`
	OllamaModels []string   `json:"ollama_models"`
	JobsQueued   int        `json:"jobs_queued"`
	JobsRunning  int        `json:"jobs_running"`
	JobsDone     int        `json:"jobs_done"`
	JobsFailed   int        `json:"jobs_failed"`
	FirstSeen    int64      `json:"first_seen_ms"`
	LastSeen     int64      `json:"last_seen_ms"`
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
	if info.IP == "" {
		info.IP = remoteIP(r)
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

// remoteIP extracts the source IP from a request's RemoteAddr ("IP:port").
func remoteIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

// PickLeastLoaded returns the enrolled agent with the fewest queued+running
// jobs, preferring GPU-capable agents, or ok=false when none are enrolled.
func (r *AgentRegistry) PickLeastLoaded() (AgentInfo, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	var best *AgentInfo
	bestScore := int(^uint(0) >> 1)
	for _, a := range r.agents {
		score := a.JobsQueued + a.JobsRunning
		if !a.GPUPresent {
			score += 1000 // strongly prefer GPU agents
		}
		if score < bestScore {
			bestScore = score
			best = a
		}
	}
	if best == nil {
		return AgentInfo{}, false
	}
	return *best, true
}

// handleCompute routes a compute job to a specific agent, or to the
// least-loaded enrolled agent when none is named.
func (s *Server) handleCompute(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Agent   string         `json:"agent"`
		Type    string         `json:"type"`
		Model   string         `json:"model"`
		Payload map[string]any `json:"payload"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if body.Type == "" {
		http.Error(w, "type is required", http.StatusBadRequest)
		return
	}
	var target string
	if body.Agent != "" {
		target = body.Agent
	} else if s.deps.Agents != nil {
		if a, ok := s.deps.Agents.PickLeastLoaded(); ok {
			target = a.Host
		}
	}
	if target == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"error": "no agents enrolled"})
		return
	}
	jobBody, _ := json.Marshal(map[string]any{"model": body.Model, "type": body.Type, "payload": body.Payload})
	resp, err := http.Post(target+"/jobs", "application/json", bytes.NewReader(jobBody))
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error(), "agent": target})
		return
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	if out == nil {
		out = map[string]any{}
	}
	out["agent"] = target
	writeJSON(w, resp.StatusCode, out)
}
