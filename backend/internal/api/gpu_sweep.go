package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

// GPU sweep: hand the whole parameter grid to the agent at once.
//
// The CPU path makes one request per candidate. A vectorised runner on the GPU
// scores every lookback in a single pass, which is what makes a large sweep feel
// instant rather than patient.

var (
	sweepMu   sync.Mutex
	sweepJobs = map[string]map[string]any{}
)

// handleGPUSweep runs a sweep, or with ?async=1 starts one and returns an id.
//
// A sweep in flight holds one connection for seconds. In a browser that stalls
// behind the page's other polling — measured at 33s against 3.5s from curl — so
// the UI starts the work and polls a cheap status endpoint instead of waiting on
// one long request. In async mode nothing is fetched before the id is returned.
func (s *Server) handleGPUSweep(w http.ResponseWriter, r *http.Request) {
	symbol := paramSymbol(r)
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := paramInterval(r, "1h")
	horizon := queryInt(r, "horizon", 12)
	bars := queryInt(r, "bars", 900)
	lookbacks := parseLookbacks(r)

	if r.URL.Query().Get("async") == "1" {
		id := fmt.Sprintf("sw-%d", time.Now().UnixNano())
		sweepMu.Lock()
		sweepJobs[id] = map[string]any{"done": false}
		sweepMu.Unlock()
		sym, iv := symbol, interval
		go func() {
			bg, cancel := context.WithTimeout(context.Background(), 240*time.Second)
			defer cancel()
			out := map[string]any{"done": true}
			payload, perr := s.sweepPayload(bg, sym, iv, horizon, bars, lookbacks, gridMode(r))
			if perr != nil {
				out["ok"] = false
				out["error"] = perr.Error()
			} else if res, err := s.sweepOnAgent(bg, payload); err != nil {
				out["ok"] = false
				out["error"] = err.Error()
			} else {
				for k, v := range res {
					out[k] = v
				}
				out["ok"] = res["ok"]
				out["symbol"] = sym
				out["interval"] = iv
			}
			sweepMu.Lock()
			sweepJobs[id] = out
			sweepMu.Unlock()
		}()
		ok(w, map[string]any{"id": id})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 240*time.Second)
	defer cancel()
	payload, err := s.sweepPayload(ctx, symbol, interval, horizon, bars, lookbacks, gridMode(r))
	if err != nil {
		fail(w, err.Error())
		return
	}
	res, err := s.sweepOnAgent(ctx, payload)
	if err != nil {
		fail(w, err.Error())
		return
	}
	res["symbol"] = symbol
	res["interval"] = interval
	writeJSON(w, http.StatusOK, res)
}

// parseLookbacks reads the ladder, defaulting to the full set.
func parseLookbacks(r *http.Request) []int {
	out := []int{2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 30, 40, 50}
	raw := strings.TrimSpace(r.URL.Query().Get("lookbacks"))
	if raw == "" {
		return out
	}
	parsed := []int{}
	for _, part := range strings.Split(raw, ",") {
		n := 0
		if _, err := fmt.Sscanf(strings.TrimSpace(part), "%d", &n); err == nil && n >= 2 && n <= 400 {
			parsed = append(parsed, n)
		}
	}
	if len(parsed) > 0 {
		return parsed
	}
	return out
}

// barsCache keeps the fetched series briefly: three workers sweep the same symbol
// every few seconds, and re-downloading a thousand candles each time was the
// largest cost in a cycle that otherwise takes a second on the GPU.
type barsCacheEntry struct {
	closes, volumes []float64
	times           []int64
	at              time.Time
}

var (
	barsMu    sync.Mutex
	barsCache = map[string]barsCacheEntry{}
)

const barsTTL = 15 * time.Second

// sweepPayload gathers the bars the runner needs.
// gridMode turns one request into a whole search: every window and horizon
// combination is scored inside a single agent job, which is what removes the
// per-call startup cost.
func gridMode(r *http.Request) bool {
	return r.URL.Query().Get("grid") == "1"
}

func (s *Server) sweepPayload(ctx context.Context, symbol, interval string, horizon, bars int, lookbacks []int, grid bool) (map[string]any, error) {
	return s.sweepPayloadFor(ctx, symbol, interval, horizon, bars, lookbacks, grid, nil)
}

// sweepPayloadFor restricts the sweep to one model family when methods is set.
func (s *Server) sweepPayloadFor(ctx context.Context, symbol, interval string, horizon, bars int, lookbacks []int, grid bool, methods []string) (map[string]any, error) {
	if s.deps.Market == nil {
		return nil, fmt.Errorf("market client unavailable")
	}
	key := symbol + "|" + interval
	barsMu.Lock()
	if e, ok := barsCache[key]; ok && time.Since(e.at) < barsTTL {
		barsMu.Unlock()
		return map[string]any{
			"closes": e.closes, "volumes": e.volumes, "times": e.times,
			"horizon": horizon, "bars": bars, "lookbacks": lookbacks,
			"grid": gridCells(grid), "methods": methods,
		}, nil
	}
	barsMu.Unlock()

	kb, err := s.barsFor(ctx, symbol, interval, 1000)
	if err != nil {
		return nil, err
	}
	if len(kb) < 200 {
		return nil, fmt.Errorf("not enough bars")
	}
	closes := make([]float64, len(kb))
	volumes := make([]float64, len(kb))
	times := make([]int64, len(kb))
	for i, b := range kb {
		closes[i] = b.Close
		volumes[i] = b.Volume
		times[i] = b.OpenTime
	}
	barsMu.Lock()
	barsCache[key] = barsCacheEntry{closes: closes, volumes: volumes, times: times, at: time.Now()}
	barsMu.Unlock()
	payload := map[string]any{
		"closes": closes, "volumes": volumes, "times": times,
		"horizon": horizon, "bars": bars, "lookbacks": lookbacks,
	}
	if grid {
		payload["grid"] = gridCells(true)
	}
	payload["methods"] = methods
	return payload, nil
}

// sweepOnAgent submits one backtest_sweep job and waits for the result. A single
// job means a single GPU process: the runner scores every lookback in one pass.
func (s *Server) sweepOnAgent(ctx context.Context, payload map[string]any) (map[string]any, error) {
	cands := s.liveAgents(agentFreshMS)
	if len(cands) == 0 {
		return nil, fmt.Errorf("no live agents")
	}
	job := map[string]any{"type": "backtest_sweep", "model": "sweep", "device": "gpu", "payload": payload}
	body, _ := json.Marshal(job)

	// A dead host must fail fast: a dial timeout on one stale registry entry was
	// eating the entire sweep budget before the live agent was ever tried.
	client := &http.Client{Timeout: 8 * time.Second}
	var lastErr error
	for _, base := range cands {
		resp, err := client.Post(base+"/jobs", "application/json", bytes.NewReader(body))
		if err != nil {
			lastErr = err
			continue
		}
		var created map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&created)
		resp.Body.Close()
		id, _ := created["id"].(string)
		if id == "" {
			lastErr = fmt.Errorf("agent %s did not return a job id", base)
			continue
		}
		for {
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(400 * time.Millisecond):
			}
			jr, err := client.Get(base + "/jobs/" + id)
			if err != nil {
				lastErr = err
				break
			}
			var j map[string]any
			_ = json.NewDecoder(jr.Body).Decode(&j)
			jr.Body.Close()
			status, _ := j["status"].(string)
			if status == "done" {
				if res, ok := j["result"].(map[string]any); ok {
					return res, nil
				}
				return nil, fmt.Errorf("agent returned no result")
			}
			if status == "failed" {
				if res, ok := j["result"].(map[string]any); ok {
					if e, _ := res["error"].(string); e != "" {
						return nil, fmt.Errorf("%s", e)
					}
				}
				return nil, fmt.Errorf("sweep failed on %s", base)
			}
		}
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("no live agents")
	}
	return nil, lastErr
}

// handleGPUSweepStatus reports an asynchronous sweep, cheaply.
func (s *Server) handleGPUSweepStatus(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.URL.Query().Get("id"))
	sweepMu.Lock()
	res, ok := sweepJobs[id]
	sweepMu.Unlock()
	if !ok {
		fail(w, "unknown sweep id")
		return
	}
	writeJSON(w, http.StatusOK, res)
}

// gridCells is the search grid: six windows by three horizons.
func gridCells(enabled bool) []map[string]any {
	if !enabled {
		return nil
	}
	cells := make([]map[string]any, 0, 24)
	for _, w := range []int{240, 300, 400, 500, 540, 600} {
		for _, h := range []int{6, 12, 18} {
			cells = append(cells, map[string]any{"bars": w, "horizon": h})
		}
	}
	return cells
}
