// Package operator is the capability registry: it answers "who can do this?"
// without any caller knowing which process, port or language does the work.
//
// An operator advertises a name, the topics it consumes, the topics it produces
// and a deadline. The orchestrator matches a need to a capability; the registry
// is the only place that mapping lives.
package operator

import (
	"sort"
	"sync"
	"time"
)

// Operator is one advertised capability.
type Operator struct {
	Name     string        `json:"name"`
	Language string        `json:"language"`
	Consumes []string      `json:"consumes"`
	Produces []string      `json:"produces"`
	Deadline time.Duration `json:"deadline_ns"`
	Notes    string        `json:"notes,omitempty"`

	// runtime observations
	Invocations int64     `json:"invocations"`
	Failures    int64     `json:"failures"`
	LastSeen    time.Time `json:"last_seen"`
}

// Registry holds the advertised capabilities.
type Registry struct {
	mu   sync.RWMutex
	ops  map[string]*Operator
	byNeed map[string][]string
}

func New() *Registry {
	return &Registry{ops: map[string]*Operator{}, byNeed: map[string][]string{}}
}

// Register adds or replaces a capability, and indexes it by the topics it produces.
func (r *Registry) Register(op Operator) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.ops[op.Name] = &op
	for _, topic := range op.Produces {
		list := r.byNeed[topic]
		already := false
		for _, n := range list {
			if n == op.Name {
				already = true
				break
			}
		}
		if !already {
			r.byNeed[topic] = append(list, op.Name)
		}
	}
}

// WhoProduces answers "which operator can satisfy this need".
func (r *Registry) WhoProduces(topic string) []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := append([]string(nil), r.byNeed[topic]...)
	sort.Strings(out)
	return out
}

// Observe records that an operator produced a message, which is how the
// registry stays honest: a capability that never produces is stale.
func (r *Registry) Observe(name string, failed bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if op, ok := r.ops[name]; ok {
		op.Invocations++
		if failed {
			op.Failures++
		}
		op.LastSeen = time.Now().UTC()
	}
}

// All returns every capability, sorted by name.
func (r *Registry) All() []Operator {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]Operator, 0, len(r.ops))
	for _, op := range r.ops {
		out = append(out, *op)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// Stale reports capabilities that have not produced within twice their deadline.
func (r *Registry) Stale() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	var out []string
	now := time.Now()
	for name, op := range r.ops {
		if op.LastSeen.IsZero() {
			continue
		}
		if now.Sub(op.LastSeen) > 2*op.Deadline {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

// Default is the platform's declared capabilities, mirroring the running fleet.
func Default() *Registry {
	r := New()
	r.Register(Operator{Name: "market_data", Language: "go", Produces: []string{"ticks", "market.price", "market.depth", "market.candle", "market.mini24h"}, Deadline: 2 * time.Second, Notes: "the only process that talks to the exchange"})
	r.Register(Operator{Name: "signals", Language: "python", Consumes: []string{"ticks", "market.candle"}, Produces: []string{"intel.signal", "intel.flow"}, Deadline: 30 * time.Second})
	r.Register(Operator{Name: "tad", Language: "go", Consumes: []string{"market.candle"}, Produces: []string{"intel.ta"}, Deadline: 30 * time.Second})
	r.Register(Operator{Name: "forecast", Language: "go", Consumes: []string{"intel.ta", "intel.flow", "intel.signal"}, Produces: []string{"intel.forecast"}, Deadline: 30 * time.Second})
	r.Register(Operator{Name: "signalbus", Language: "go", Produces: []string{"alert.pumpdump"}, Deadline: 10 * time.Second})
	r.Register(Operator{Name: "healthpub", Language: "go", Produces: []string{"system.health"}, Deadline: 15 * time.Second})
	r.Register(Operator{Name: "risk", Language: "python", Consumes: []string{"intel.forecast", "intel.flow"}, Produces: []string{"risk.state"}, Deadline: 10 * time.Second})
	r.Register(Operator{Name: "books", Language: "python", Consumes: []string{"intel.signal", "intel.ta", "intel.forecast"}, Produces: []string{"model.decision", "model.fill", "model.pnl", "model.rank"}, Deadline: 60 * time.Second})
	r.Register(Operator{Name: "recorder", Language: "go", Produces: []string{}, Deadline: 5 * time.Second, Notes: "consumer only: the persistence plane"})
	return r
}
