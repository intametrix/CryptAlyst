package lake

import (
	"strings"
	"sync"

	"cryptalyst/core/internal/store"
)

// Placement is the lake's routing policy: given one record, which additional
// stores should hold it. The journal always does — that never varies — so the
// question is only which *second* home a record type earns.
//
// The decision is made here, in the lake, and nowhere else. The recorder hands
// records to the lake and is not allowed to know where they land; that keeps one
// place to change when data shapes or store health change, and keeps the hot path
// free of per-record probing.
type Placement struct {
	Sinks []Sink
	// byPrefix is built once at start-up: topic prefix -> sink indexes.
	byPrefix map[string][]int
}

// Sink is one destination beside the journal.
type Sink interface {
	Name() string
	// Takes reports whether this sink wants records of this topic.
	Takes(topic string) bool
	Write(rec store.Envelope) error
}

var mu sync.Mutex // sink writes are serialised per sink; stores are not required to be concurrent-safe

// PolicyWithTrades is the shipping policy: fills earn a relational row and a trade
// row; decisions earn a relational row and a document; intel earns a document.
func PolicyWithTrades() map[string][]string {
	p := DefaultPolicy()
	p["model.fill"] = []string{"sql", "trades"}
	return p
}

// DefaultPolicy is the target layout:
//
//	model.fill      -> SQLite (trades)
//	model.decision  -> SQLite (flat row) and the document plane (full envelope)
//	intel.*         -> document plane
//	model.pnl/rank  -> SQLite (books/flat)
//	market.*, ticks, alert.*, system.*, risk.*, exec.*, review.* -> journal only
//
// Nothing else goes to a second store; the journal is the record for every type.
func DefaultPolicy() map[string][]string {
	return map[string][]string{
		"model.fill":     {"sql"},
		"model.decision": {"sql", "docs"},
		"model.pnl":      {"sql"},
		"model.rank":     {"sql"},
		"intel.signal":   {"docs"},
		"intel.flow":     {"docs"},
		"intel.ta":       {"docs"},
		"intel.forecast": {"docs"},
	}
}

// NewPlacement binds a policy to the available sinks. A policy entry naming a sink
// that is not configured is ignored, not fatal: the journal still receives the record.
func NewPlacement(policy map[string][]string, sinks ...Sink) *Placement {
	byName := make(map[string]int, len(sinks))
	for i, s := range sinks {
		byName[s.Name()] = i
	}
	byPrefix := map[string][]int{}
	for topic, names := range policy {
		for _, n := range names {
			if i, ok := byName[n]; ok {
				byPrefix[topic] = append(byPrefix[topic], i)
			}
		}
	}
	return &Placement{Sinks: sinks, byPrefix: byPrefix}
}

// Route returns the sinks for a topic. A prefix match ("intel.") is supported so a
// new intel topic does not need a policy edit.
func (p *Placement) Route(topic string) []Sink {
	var out []Sink
	if idx, ok := p.byPrefix[topic]; ok {
		for _, i := range idx {
			out = append(out, p.Sinks[i])
		}
		return out
	}
	// longest prefix with a trailing dot wins
	best := ""
	for prefix := range p.byPrefix {
		if strings.HasSuffix(prefix, ".") && strings.HasPrefix(topic, prefix) && len(prefix) > len(best) {
			best = prefix
		}
	}
	if best != "" {
		for _, i := range p.byPrefix[best] {
			out = append(out, p.Sinks[i])
		}
	}
	return out
}

// Place writes one record to its sinks. It is called after the journal has the
// record: the journal is the authority, so a sink failure is counted, never fatal.
func (p *Placement) Place(rec store.Envelope) error {
	sinks := p.Route(rec.Topic)
	if len(sinks) == 0 {
		return nil
	}
	mu.Lock()
	defer mu.Unlock()
	var firstErr error
	for _, s := range sinks {
		if err := s.Write(rec); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}
