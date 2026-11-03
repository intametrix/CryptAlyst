package api

import (
	"context"
	"sync"
	"time"
)

// The live engine: continuous, parallel GPU optimisation owned by the server.
//
// Driving the search from the browser tied it to a page's lifetime — tabs sleep,
// remounts cancel work, and the GPU idled whenever nobody was looking. Running it
// here means the GPU is always calculating, and the UI becomes a viewer of state
// that is always current.

// liveState is one symbol's latest optimisation.
type liveState struct {
	Symbol    string           `json:"symbol"`
	Best      map[string]any   `json:"best"`
	Rows      []map[string]any `json:"rows"`
	Series    []map[string]any `json:"series"`
	Future    map[string]any   `json:"future"`
	Tests     int              `json:"tests"`
	Cycles    int              `json:"cycles"`
	UpdatedMS int64            `json:"updated_ms"`
	Seconds   float64          `json:"seconds"`
	History   []float64        `json:"history"`
	// Phase is the model being optimised right now.
	Phase      string             `json:"phase"`
	PhaseIndex int                `json:"phase_index"`
	PhaseTotal int                `json:"phase_total"`
	PhaseTests int                `json:"phase_tests"`
	PhaseBest  float64            `json:"phase_best"`
	PerModel   map[string]float64 `json:"per_model"`
	Problem    string             `json:"problem"`
	// Trace is the visible record of the sweep: each lookback tested and what it
	// scored. Without it the optimisation is a number that appears from nowhere.
	Trace      []tracePoint `json:"trace"`
	NowTesting int          `json:"now_testing"`
}

type tracePoint struct {
	Lookback int     `json:"lookback"`
	R2       float64 `json:"r2"`
	MAPE     float64 `json:"mape"`
}

type liveEngine struct {
	mu       sync.Mutex
	states   map[string]*liveState
	symbols  []string
	workers  map[string]bool
	interval string
	running  int
	started  bool
}

var engine = &liveEngine{
	states:   map[string]*liveState{},
	symbols:  []string{"BTCUSDT", "ETHUSDT", "SOLUSDT", "RIFUSDT"},
	interval: "4h",
	workers:  map[string]bool{},
}

func (e *liveEngine) snapshot() map[string]any {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]*liveState, 0, len(e.states))
	for _, s := range e.symbols {
		if st, ok := e.states[s]; ok {
			out = append(out, st)
		}
	}
	return map[string]any{
		"ok": true, "interval": e.interval, "symbols": e.symbols,
		"parallel_workers": e.running, "states": out,
	}
}

func (e *liveEngine) record(symbol, interval string, res map[string]any, secs float64) {
	rowsRaw, _ := res["results"].([]any)
	rows := make([]map[string]any, 0, 40)
	for i, r := range rowsRaw {
		if i >= 40 {
			break
		}
		if m, ok := r.(map[string]any); ok {
			rows = append(rows, m)
		}
	}
	seriesRaw, _ := res["series"].([]any)
	series := make([]map[string]any, 0, 4)
	for _, s := range seriesRaw {
		if m, ok := s.(map[string]any); ok {
			series = append(series, m)
		}
	}
	var best map[string]any
	if len(rows) > 0 {
		best = rows[0]
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	st, ok := e.states[symbol]
	if !ok {
		st = &liveState{Symbol: symbol, History: []float64{}}
		e.states[symbol] = st
	}
	// Keep the best ever seen, not just the latest phase: a later model may be
	// worse, and the panel should still show the winner.
	// Always publish the latest sweep so the chart moves every cycle; keep the
	// best-ever configuration separately so the header never regresses.
	st.Rows = rows
	st.Series = series
	st.UpdatedMS = time.Now().UnixMilli()
	st.Seconds = secs
	if best != nil {
		if r2, ok := best["r2"].(float64); ok {
			prev := 0.0
			if st.Best != nil {
				prev, _ = st.Best["r2"].(float64)
			}
			if st.Best == nil || r2 > prev {
				st.Best = best
			}
		}
	}
	st.Cycles++
	st.PhaseTests = len(rows)
	st.Tests += len(rows)
	st.UpdatedMS = time.Now().UnixMilli()
	st.Seconds = secs
	if st.PerModel == nil {
		st.PerModel = map[string]float64{}
	}
	if method, ok := best["method"].(string); ok {
		if r2, ok2 := best["r2"].(float64); ok2 {
			if cur, seen := st.PerModel[method]; !seen || r2 > cur {
				st.PerModel[method] = r2
			}
		}
	}
	if r2, ok := best["r2"].(float64); ok {
		st.PhaseBest = r2
		st.History = append(st.History, r2)
		if len(st.History) > 120 {
			st.History = st.History[len(st.History)-120:]
		}
	}
}

// StartEngine begins continuous optimisation. Exported so the server binary can
// launch it once at boot.
func StartEngine(s *Server) { engine.Start(s) }

// Start launches one worker per symbol, in parallel, forever.
func (e *liveEngine) Start(s *Server) {
	e.mu.Lock()
	if e.started {
		e.mu.Unlock()
		return
	}
	e.started = true
	e.running = len(e.symbols)
	e.mu.Unlock()

	e.mu.Lock()
	symbols := append([]string(nil), e.symbols...)
	e.mu.Unlock()
	for _, sym := range symbols {
		e.startWorker(s, sym)
	}
}

func (e *liveEngine) startWorker(s *Server, symbol string) {
	e.mu.Lock()
	if e.workers == nil {
		e.workers = map[string]bool{}
	}
	if e.workers[symbol] {
		e.mu.Unlock()
		return
	}
	e.workers[symbol] = true
	e.mu.Unlock()

	go func() {
		// One family at a time, and within a family one parameter step at a time,
		// so every calculation is visible as it lands.
		for {
			for i, family := range modelFamilies {
				e.setState(symbol, family, i)
				for _, chunk := range chunkLookbacks() {
					ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
					started := time.Now()
					payload, perr := s.sweepPayloadFor(ctx, symbol, e.interval, 6, 540, chunk, true, []string{family})
					if perr == nil {
						payload["grid"] = phaseCells
						if res, serr := s.sweepOnAgent(ctx, payload); serr == nil {
							e.record(symbol, e.interval, res, time.Since(started).Seconds())
							e.pushTrace(symbol, chunk, res)
						} else {
							e.note(symbol, serr.Error())
							time.Sleep(1200 * time.Millisecond)
						}
					}
					cancel()
				}
			}
		}
	}()
}

// AddSymbol starts optimising a symbol that was not in the original set.
func (e *liveEngine) AddSymbol(s *Server, symbol string) {
	e.mu.Lock()
	for _, existing := range e.symbols {
		if existing == symbol {
			e.mu.Unlock()
			return
		}
	}
	e.symbols = append(e.symbols, symbol)
	e.running = len(e.symbols)
	alreadyUp := e.started
	e.mu.Unlock()

	if _, ok := e.states[symbol]; !ok {
		e.mu.Lock()
		e.states[symbol] = &liveState{Symbol: symbol, History: []float64{}, PerModel: map[string]float64{}}
		e.mu.Unlock()
	}
	if alreadyUp {
		e.startWorker(s, symbol)
	}
}

// modelFamilies is the order the optimiser works through, one phase each.
var modelFamilies = []string{"vwap", "vwdrift", "vwtrend", "hybrid"}

// phaseCells is the window/horizon grid for one phase. It is deliberately sent as
// a single job: the runner vectorises every combination inside one GPU pass, which
// is where the real parallelism is, and the agent only runs four jobs at once.
var phaseCells = []map[string]any{
	{"bars": 240, "horizon": 6}, {"bars": 300, "horizon": 6}, {"bars": 400, "horizon": 6},
	{"bars": 500, "horizon": 6}, {"bars": 540, "horizon": 6}, {"bars": 600, "horizon": 6},
	{"bars": 240, "horizon": 12}, {"bars": 400, "horizon": 12}, {"bars": 540, "horizon": 12},
	{"bars": 600, "horizon": 12},
}

// phaseLookbacks gives 25 lookbacks; with the 10-cell grid that is 250 tests per
// phase, per symbol.
func phaseLookbacks() []int {
	out := make([]int, 0, 25)
	for lb := 2; lb <= 50; lb++ {
		out = append(out, lb)
	}
	return out
}

func (e *liveEngine) setState(symbol, family string, index int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	st, ok := e.states[symbol]
	if !ok {
		st = &liveState{Symbol: symbol, History: []float64{}, PerModel: map[string]float64{}}
		e.states[symbol] = st
	}
	st.Phase = family
	st.Trace = nil
	st.PhaseIndex = index + 1
	st.PhaseTotal = len(modelFamilies)
	st.PhaseBest = st.PerModel[family]
}

// note records a problem without destroying the last good result.
func (e *liveEngine) note(symbol, msg string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if st, ok := e.states[symbol]; ok {
		st.Problem = msg
	}
}

// chunkLookbacks splits the ladder so each step is small enough to be seen.
func chunkLookbacks() [][]int {
	out := [][]int{}
	batch := []int{}
	for lb := 2; lb <= 50; lb++ {
		batch = append(batch, lb)
		if len(batch) == 4 {
			out = append(out, batch)
			batch = []int{}
		}
	}
	if len(batch) > 0 {
		out = append(out, batch)
	}
	return out
}

// pushTrace records the best score from one parameter step.
func (e *liveEngine) pushTrace(symbol string, chunk []int, res map[string]any) {
	best := -1e9
	mape := 0.0
	if rows, ok := res["results"].([]any); ok {
		for _, r := range rows {
			m, ok := r.(map[string]any)
			if !ok {
				continue
			}
			r2, _ := m["r2"].(float64)
			if r2 > best {
				best = r2
				mape, _ = m["mape_pct"].(float64)
			}
		}
	}
	if best <= -1e9 {
		return
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	st, ok := e.states[symbol]
	if !ok {
		return
	}
	if len(chunk) > 0 {
		st.NowTesting = chunk[len(chunk)-1]
	}
	st.Trace = append(st.Trace, tracePoint{Lookback: chunk[0], R2: best, MAPE: mape})
	if len(st.Trace) > 60 {
		st.Trace = st.Trace[len(st.Trace)-60:]
	}
}
