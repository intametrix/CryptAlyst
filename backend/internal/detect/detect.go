// Package detect defines the pluggable detection-model layer.
//
// Every detection strategy -- the 1-second flow-onset engine, the 5/15/60-minute
// pump/dump screen, the Turtle breakout survey, and anything added later --
// implements the same Detector interface and is registered in a Registry. The
// scanner then knows nothing about any particular model: it feeds events in and
// collects Signals out, and each Signal carries the name of the model that
// produced it so the UI can render a mixed feed without special-casing.
//
// Two shapes of detector are supported, because forcing one into the other
// would be a lie:
//
//   - Streaming detectors (flow onset) ingest a firehose of Ticker and
//     AggTrade events and evaluate continuously.
//   - Batch detectors (the screen, the Turtle survey) own their own cadence and
//     fetch history over REST. Observe is a no-op for them; they expose their
//     most recent result through Evaluate and do the real work on a timer.
//
// The distinction is made explicit by Batch() rather than hidden, so the UI can
// label a batch detector's output with the age of its snapshot instead of
// pretending it is live.
package detect

import (
	"encoding/json"
	"sort"
	"sync"
	"time"
)

// EventType discriminates an Event's payload.
type EventType string

// Event types.
const (
	EventTicker   EventType = "ticker"
	EventAggTrade EventType = "aggtrade"
	EventBar      EventType = "bar"
)

// Event is one input to a detector.
//
// Raw carries the exchange payload verbatim and detectors parse what they need.
// That is deliberate: parsing centrally would mean every detector pays for
// every field, and the flow engine already parses the ticker payload on its hot
// path, so a decoded struct here would be a second parse of the same bytes at
// several hundred messages per second. Symbol and TSMS are best-effort
// convenience fields a producer may fill when it has them for free; a detector
// must not rely on them being populated.
type Event struct {
	Type   EventType
	Raw    json.RawMessage
	Symbol string
	TSMS   int64
}

// Signal is one detection output from any model.
//
// It is deliberately self-describing: Detector names the model, Kind names the
// pattern, and Metrics carries the model-specific numbers. A consumer must be
// able to render this without knowing which model produced it, which is what
// makes the registry worth having.
type Signal struct {
	// ID is a deterministic identity for this detection: detector + symbol +
	// kind + the inception time of the thing detected. The Registry emits an ID
	// at most once, which is what stops a batch detector from repeating the
	// same breakout on every tick.
	ID string `json:"id"`
	Detector   string             `json:"detector"`
	Kind       string             `json:"kind"`
	Symbol     string             `json:"symbol"`
	Direction  int                `json:"direction"`
	Severity   string             `json:"severity"`
	Score      float64            `json:"score"`
	Price      float64            `json:"price"`
	TSMS       int64              `json:"ts_ms"`
	// Summary is a one-line, human-readable rendering so a consumer can display
	// a mixed feed from several models with zero special-casing.
	Summary    string             `json:"summary"`
	Reasons    []string           `json:"reasons"`
	Components map[string]float64 `json:"components,omitempty"`
	Metrics    map[string]any     `json:"metrics,omitempty"`
	// Batch marks a signal derived from a periodic snapshot rather than live
	// observation, and SnapshotMS says how old that snapshot is.
	Batch      bool  `json:"batch"`
	SnapshotMS int64 `json:"snapshot_ms,omitempty"`
}

// Descriptor describes a registered detector for the API and the UI.
type Descriptor struct {
	Name        string         `json:"name"`
	Title       string         `json:"title"`
	Description string         `json:"description"`
	Batch       bool           `json:"batch"`
	Enabled     bool           `json:"enabled"`
	Params      map[string]any `json:"params"`
	SignalCount int64          `json:"signal_count"`
	LastSignalMS int64         `json:"last_signal_ms"`
	LastEvalMS  int64          `json:"last_eval_ms"`
}

// Detector is a pluggable detection model.
type Detector interface {
	// Name is a stable, machine-readable id (for example "flow-onset").
	Name() string
	// Title is a human label for the UI.
	Title() string
	// Description explains, in one sentence, what the model detects.
	Description() string
	// Batch reports whether this model works from periodic snapshots rather
	// than live events.
	Batch() bool
	// Observe ingests one event. It must not block: it runs on the stream's
	// read path.
	Observe(ev Event)
	// Evaluate returns any signals the model currently has. `now` is epoch ms.
	Evaluate(now int64) []Signal
	// Params returns the model's tunable parameters, for display.
	Params() map[string]any
}

// EscalationRequest asks the transport layer to subscribe a symbol to a
// higher-resolution stream for a while. Keeping this as an optional interface
// means a detector can request trade-level detail without knowing that
// websockets exist.
type EscalationRequest struct {
	Symbol string
	Stream string // for example "aggTrade"
	TTL    time.Duration
}

// Escalating is implemented by detectors that want temporary extra data.
type Escalating interface {
	// Escalations returns pending requests and clears them.
	Escalations() []EscalationRequest
}

// Registry holds the detectors and fans events and evaluation across them.
type Registry struct {
	mu        sync.RWMutex
	order     []string
	detectors map[string]Detector
	enabled   map[string]bool
	stats     map[string]*detectorStats
	// seen maps a Signal ID to when it was first emitted. A batch detector
	// republishes its whole snapshot on every tick, so without this the feed
	// would repeat the same breakout once per second.
	seen map[string]int64
}

type detectorStats struct {
	signals      int64
	lastSignalMS int64
	lastEvalMS   int64
}

// NewRegistry creates an empty registry.
func NewRegistry() *Registry {
	return &Registry{
		detectors: make(map[string]Detector),
		enabled:   make(map[string]bool),
		stats:     make(map[string]*detectorStats),
		seen:      make(map[string]int64),
	}
}

// Register adds a detector, enabled by default. Re-registering a name replaces
// the implementation and preserves the enabled flag, which is what a hot reload
// would want.
func (r *Registry) Register(d Detector) {
	r.mu.Lock()
	defer r.mu.Unlock()
	name := d.Name()
	if _, exists := r.detectors[name]; !exists {
		r.order = append(r.order, name)
		r.enabled[name] = true
		r.stats[name] = &detectorStats{}
	}
	r.detectors[name] = d
}

// SetEnabled turns a detector on or off.
//
// Disabling clears VOLATILE state but deliberately preserves cooldowns. The
// obvious-looking alternative -- wiping cooldowns on disable -- is backwards:
// a cooldown only ever suppresses, so keeping it is harmless, whereas clearing
// it makes every previously-suppressed symbol fire the instant the detector
// comes back. Note also that persisted cooldowns mean a process restart is not
// itself an alert storm; detectors that hold them in memory should re-prime
// from cold on start instead.
func (r *Registry) SetEnabled(name string, on bool) bool {
	r.mu.Lock()
	d, ok := r.detectors[name]
	if !ok {
		r.mu.Unlock()
		return false
	}
	r.enabled[name] = on
	if !on {
		if resetter, ok := d.(interface{ Reset() }); ok {
			resetter.Reset()
		}
	}
	r.mu.Unlock()
	return true
}

// IsEnabled reports whether a detector is on.
func (r *Registry) IsEnabled(name string) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.enabled[name]
}

// Observe fans an event to every enabled detector.
//
// DeepCopy semantics matter here: the payload pointers are shared, so a
// detector must copy anything it retains rather than holding the pointer. The
// flow engine copies into its own ring buffer, which satisfies this.
func (r *Registry) Observe(ev Event) {
	r.mu.RLock()
	targets := make([]Detector, 0, len(r.detectors))
	for name, d := range r.detectors {
		if r.enabled[name] {
			targets = append(targets, d)
		}
	}
	r.mu.RUnlock()
	for _, d := range targets {
		d.Observe(ev)
	}
}

// Evaluate collects signals from every enabled detector, newest-model-first by
// score, and refreshes the per-detector stats.
func (r *Registry) Evaluate(now int64) []Signal {
	r.mu.RLock()
	type named struct {
		name string
		d    Detector
	}
	targets := make([]named, 0, len(r.detectors))
	for name, d := range r.detectors {
		if r.enabled[name] {
			targets = append(targets, named{name, d})
		}
	}
	r.mu.RUnlock()

	out := make([]Signal, 0, 16)
	for _, t := range targets {
		signals := t.d.Evaluate(now)
		if len(signals) == 0 {
			continue
		}
		// Emit-once: drop signals whose identity has already been published
		// inside the dedup window. Signals without an ID are passed through,
		// since a streaming detector with its own cooldown legitimately emits
		// a fresh ID per alert.
		signals = r.filterSeen(signals, now)
		r.mu.Lock()
		st := r.stats[t.name]
		if st == nil {
			st = &detectorStats{}
			r.stats[t.name] = st
		}
		st.lastEvalMS = now
		for i := range signals {
			if signals[i].Detector == "" {
				signals[i].Detector = t.name
			}
			st.signals++
			if signals[i].TSMS > st.lastSignalMS {
				st.lastSignalMS = signals[i].TSMS
			}
		}
		r.mu.Unlock()
		out = append(out, signals...)
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Score > out[j].Score })
	return out
}

// seenTTL bounds the dedup table. Longer than any plausible cooldown, short
// enough that the map cannot grow without limit on a long-running process.
const seenTTL = 12 * time.Hour

func (r *Registry) filterSeen(signals []Signal, now int64) []Signal {
	out := signals[:0]
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, sig := range signals {
		if sig.ID == "" {
			out = append(out, sig)
			continue
		}
		if first, ok := r.seen[sig.ID]; ok && now-first < seenTTL.Milliseconds() {
			continue
		}
		r.seen[sig.ID] = now
		out = append(out, sig)
	}
	// Opportunistic eviction so a long-running process does not leak.
	if len(r.seen) > 20000 {
		cutoff := now - seenTTL.Milliseconds()
		for id, first := range r.seen {
			if first < cutoff {
				delete(r.seen, id)
			}
		}
	}
	return out
}

// Escalations drains escalation requests from every enabled detector that
// implements Escalating.
func (r *Registry) Escalations() []EscalationRequest {
	r.mu.RLock()
	sources := make([]Escalating, 0, 4)
	for name, d := range r.detectors {
		if !r.enabled[name] {
			continue
		}
		if e, ok := d.(Escalating); ok {
			sources = append(sources, e)
		}
	}
	r.mu.RUnlock()

	var out []EscalationRequest
	for _, source := range sources {
		out = append(out, source.Escalations()...)
	}
	return out
}

// Describe lists every registered detector for the API.
func (r *Registry) Describe() []Descriptor {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]Descriptor, 0, len(r.order))
	for _, name := range r.order {
		d := r.detectors[name]
		if d == nil {
			continue
		}
		st := r.stats[name]
		desc := Descriptor{
			Name:        name,
			Title:       d.Title(),
			Description: d.Description(),
			Batch:       d.Batch(),
			Enabled:     r.enabled[name],
			Params:      d.Params(),
		}
		if st != nil {
			desc.SignalCount = st.signals
			desc.LastSignalMS = st.lastSignalMS
			desc.LastEvalMS = st.lastEvalMS
		}
		out = append(out, desc)
	}
	return out
}

// Names returns the registered detector names in registration order.
func (r *Registry) Names() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]string, len(r.order))
	copy(out, r.order)
	return out
}

// Reset clears every detector's accumulated state. Used when the universe is
// rebuilt, so a detector cannot carry baselines for symbols that are no longer
// tracked.
func (r *Registry) Reset() {
	r.mu.RLock()
	targets := make([]Detector, 0, len(r.detectors))
	for _, d := range r.detectors {
		targets = append(targets, d)
	}
	r.mu.RUnlock()
	for _, d := range targets {
		if resetter, ok := d.(interface{ Reset() }); ok {
			resetter.Reset()
		}
	}
}
