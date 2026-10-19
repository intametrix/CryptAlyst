// Package models runs detection models CONTINUOUSLY and ASYNCHRONOUSLY.
//
// The human's instruction: "Add more models. They should be continuously running
// as services or goroutines so async."
//
// WHY A RUNNER RATHER THAN MORE CALL SITES. The previous arrangement had models
// invoked from inside the scan loop, which made three things true that should not
// be:
//
//  1. A SLOW MODEL SLOWED EVERYTHING. One model taking 200ms delayed the flow
//     measurement for every symbol, because they shared a goroutine with the
//     thing that produces the data.
//  2. A PANICKING MODEL KILLED THE PIPELINE. An index out of range in one model
//     took down the loop that feeds all of them.
//  3. A MODEL COULD ONLY RUN WHEN SOMETHING ELSE RAN. This is the bug that
//     emptied the taker buffer: aggTrade ingestion lived inside one model, so when
//     that model was not scheduled, no trades were recorded at all and two other
//     features silently read zero.
//
// Each model now owns a goroutine and a ticker. They cannot block each other, a
// panic is contained to its own goroutine and reported rather than fatal, and
// every model runs whether or not any other one does.
package models

import (
	"context"
	"log"
	"sync"
	"sync/atomic"
	"time"
)

// Snapshot is the market view handed to every model on each tick. It is built
// ONCE per tick and shared read-only, so adding a model costs one evaluation and
// not one additional copy of the whole market.
type Snapshot struct {
	AtMS int64
	// Rows is every tracked symbol's current flow state. Models must treat it as
	// read-only; the runner reuses the slice between models within a tick.
	Rows []Row
}

// Row is the subset of flow state a model may read.
//
// A narrow struct rather than the engine's own Candidate, deliberately: a model
// that can reach into everything eventually depends on everything, and then it
// cannot be tested without a live market.
type Row struct {
	Symbol         string
	Price          float64
	QuoteVolume24h float64
	ChangePct24h   float64
	High24h        float64
	Low24h         float64

	Sigma10s      float64
	SigmaMultiple float64
	BurstMultiple float64
	BurstZ        float64
	TakerBuyRatio float64
	TakerQuote    float64
	RangePosition float64
	Change10s     float64
	Change60s     float64
	Precursor     float64
	TrendAgree    float64
	TrendSign     int
}

// Signal is one model's finding. Models do not decide anything; they report, and
// the engine decides. That separation is what makes a model replaceable.
type Signal struct {
	Model     string  `json:"model"`
	Symbol    string  `json:"symbol"`
	Direction string  `json:"direction"` // PUMP, DUMP or NONE
	Score     float64 `json:"score"`
	// Reasons names what fired, so a reader can tell a three-factor signal from a
	// single-factor one.
	Reasons []string `json:"reasons"`
	AtMS    int64    `json:"at_ms"`
	// Detail carries model-specific numbers for the UI and the log.
	Detail map[string]float64 `json:"detail,omitempty"`
}

// Model is one detector.
//
// Evaluate must be safe for concurrent use with itself only in the sense that the
// runner calls it from exactly one goroutine per model; it must NOT mutate the
// snapshot it is given.
type Model interface {
	// Name is stable and is used as the signal's origin, so it must not change
	// when behaviour changes -- a rename would silently split a model's history.
	Name() string
	// Interval is how often this model wants to run. Different models want
	// different cadences: a structure model on 1s data does not need 5Hz.
	Interval() time.Duration
	// Evaluate reads the snapshot and returns signals. Returning none is normal.
	Evaluate(ctx context.Context, snap Snapshot) []Signal
}

// Source supplies the market snapshot.
type Source interface {
	SnapshotForModels(atMS int64) []Row
}

// Runner owns the models and their goroutines.
type Runner struct {
	source Source
	models []Model

	mu      sync.RWMutex
	latest  map[string][]Signal // newest signals per model
	all     []Signal            // bounded ring of recent signals, newest first
	maxKeep int

	// Per-model health, so a model that has silently stopped evaluating is
	// VISIBLE. A stopped model and a model finding nothing look identical from
	// the outside, which is the same failure shape as the empty taker buffer.
	health map[string]*modelHealth

	running atomic.Bool
	wg      sync.WaitGroup
}

type modelHealth struct {
	Ticks      int64  `json:"ticks"`
	Signals    int64  `json:"signals"`
	Errors     int64  `json:"errors"`
	Panics     int64  `json:"panics"`
	LastTickMS int64  `json:"last_tick_ms"`
	LastErr    string `json:"last_error,omitempty"`
	SlowTicks  int64  `json:"slow_ticks"`
}

// NewRunner builds a runner. Models may be added later; Add is safe while running.
func NewRunner(source Source) *Runner {
	return &Runner{
		source:  source,
		latest:  map[string][]Signal{},
		health:  map[string]*modelHealth{},
		maxKeep: 500,
	}
}

// Add registers a model and starts its goroutine if the runner is running.
func (r *Runner) Add(m Model) {
	r.mu.Lock()
	r.models = append(r.models, m)
	r.health[m.Name()] = &modelHealth{}
	start := r.running.Load()
	r.mu.Unlock()
	if start {
		r.startModel(m)
	}
}

// Start begins every registered model's goroutine.
func (r *Runner) Start(ctx context.Context) {
	if !r.running.CompareAndSwap(false, true) {
		return
	}
	r.mu.RLock()
	ms := append([]Model(nil), r.models...)
	r.mu.RUnlock()
	for _, m := range ms {
		r.startModel(m)
	}
	log.Printf("models: %d model(s) running asynchronously", len(ms))
}

func (r *Runner) startModel(m Model) {
	interval := m.Interval()
	if interval <= 0 {
		interval = time.Second
	}
	r.wg.Add(1)
	go func() {
		defer r.wg.Done()
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for range ticker.C {
			r.tick(context.Background(), m)
		}
	}()
}

// tick runs one model once, with its panic and its errors contained.
//
// The recover is not defensive habit: a panic in a model used to take down the
// goroutine that feeds every other model, so one bad index silently stopped the
// whole detection layer. A contained panic is a health counter and a log line.
func (r *Runner) tick(ctx context.Context, m Model) {
	name := m.Name()
	started := time.Now()
	defer func() {
		if rec := recover(); rec != nil {
			r.bumpPanic(name, rec)
		}
	}()

	var rows []Row
	if r.source != nil {
		rows = r.source.SnapshotForModels(time.Now().UnixMilli())
	}
	snap := Snapshot{AtMS: time.Now().UnixMilli(), Rows: rows}

	sigs := m.Evaluate(ctx, snap)
	elapsed := time.Since(started)

	r.mu.Lock()
	h := r.health[name]
	if h == nil {
		h = &modelHealth{}
		r.health[name] = h
	}
	h.Ticks++
	h.LastTickMS = time.Now().UnixMilli()
	if elapsed > 5*m.Interval() {
		h.SlowTicks++
	}
	if len(sigs) > 0 {
		for i := range sigs {
			sigs[i].Model = name
			if sigs[i].AtMS == 0 {
				sigs[i].AtMS = snap.AtMS
			}
		}
		h.Signals += int64(len(sigs))
		r.latest[name] = sigs
		r.all = append(sigs, r.all...)
		if len(r.all) > r.maxKeep {
			r.all = r.all[:r.maxKeep]
		}
	} else {
		r.latest[name] = nil
	}
	r.mu.Unlock()
}

func (r *Runner) bumpPanic(name string, rec any) {
	r.mu.Lock()
	defer r.mu.Unlock()
	h := r.health[name]
	if h == nil {
		h = &modelHealth{}
		r.health[name] = h
	}
	h.Panics++
	h.LastErr = "panic"
	log.Printf("models: PANIC in %s contained: %v", name, rec)
}

// Signals returns recent signals across all models, newest first.
func (r *Runner) Signals(limit int) []Signal {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if limit <= 0 || limit > len(r.all) {
		limit = len(r.all)
	}
	return append([]Signal(nil), r.all[:limit]...)
}

// Health reports every model's counters and its interval, so a model that has
// stopped evaluating is distinguishable from one finding nothing.
func (r *Runner) Health() []map[string]any {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]map[string]any, 0, len(r.health))
	for _, m := range r.models {
		h := r.health[m.Name()]
		if h == nil {
			continue
		}
		sinceTick := int64(-1)
		if h.LastTickMS > 0 {
			sinceTick = time.Now().UnixMilli() - h.LastTickMS
		}
		out = append(out, map[string]any{
			"model": m.Name(), "interval_ms": m.Interval().Milliseconds(),
			"ticks": h.Ticks, "signals": h.Signals, "errors": h.Errors,
			"panics": h.Panics, "slow_ticks": h.SlowTicks,
			"ms_since_last_tick": sinceTick, "last_error": h.LastErr,
			"alive": sinceTick >= 0 && sinceTick < 10*m.Interval().Milliseconds(),
		})
	}
	return out
}

// Wait blocks until every goroutine has stopped, for a clean shutdown.
func (r *Runner) Wait() { r.wg.Wait() }
