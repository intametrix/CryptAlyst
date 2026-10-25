// Package graph is the signal-flow graph foundation for strategy design.
//
// A strategy is modelled as a network of small, self-describing nodes: each
// node declares typed input and output ports, and a Signal flows along a wire
// from one output port to a matching input port. The graph is the strategy,
// and the same nodes compose into any number of strategies.
//
// This package defines the lowest layer only: the Signal envelope, the typed
// Port contract, the Node interface, and a Registry. The runtime (lazy
// evaluation, cycle detection, link equations) is built on top of it in later
// phases, and the existing detect models are given Node adapters so they
// become first-class graph sources without changing their internals.
package graph

import "encoding/json"

// SignalType is the "wire gauge" of a signal: what kind of data it carries.
//
// Types are deliberate, not free-form strings, so the graph can type-check a
// link at design time (a PRICE output cannot feed a DIRECTION input) and the
// UI can label a port with a colour-coded chip.
type SignalType string

// Signal types.
const (
	// TypeDirection is a -1 / 0 / +1 directional signal.
	TypeDirection SignalType = "DIRECTION"
	// TypeScore is a 0..100 strength signal.
	TypeScore SignalType = "SCORE"
	// TypePrice is a last/mark price.
	TypePrice SignalType = "PRICE"
	// TypeVolume is a rolling or bar volume.
	TypeVolume SignalType = "VOLUME"
	// TypeFlow is a structured taker buy/sell flow payload.
	TypeFlow SignalType = "FLOW"
	// TypeTA is a technical-analysis indicator value (RSI, EMA, ...).
	TypeTA SignalType = "TA"
	// TypeProbability is a calibrated 0..1 probability.
	TypeProbability SignalType = "PROBABILITY"
	// TypeText is a free-form reason/note.
	TypeText SignalType = "TEXT"
	// TypeVerdict is the strategy output: BULL or BEAR.
	TypeVerdict SignalType = "VERDICT"
)

// Signal is one typed value flowing along a wire.
//
// It is deliberately a small, self-describing envelope rather than an
// interface{}, so a consumer can always reconstruct where a value came from
// and why. Basis carries the human-readable explanation and is threaded
// through a chain so a verdict can be traced back to the raw market event.
type Signal struct {
	Type   SignalType     `json:"type"`
	Symbol string         `json:"symbol"`
	Value  float64        `json:"value"`
	TSMS   int64          `json:"ts"`
	Basis  string         `json:"basis,omitempty"`
	Meta   map[string]any `json:"meta,omitempty"`
}

// New returns a Signal with the mandatory fields set.
func New(typ SignalType, symbol string, value float64, ts int64) Signal {
	return Signal{Type: typ, Symbol: symbol, Value: value, TSMS: ts}
}

// WithBasis returns a copy of s carrying the given human-readable reason.
func (s Signal) WithBasis(b string) Signal {
	s.Basis = b
	return s
}

// WithMeta returns a copy of s with the extra metadata attached. A nil map is
// treated as empty so callers never need to guard before setting a key.
func (s Signal) WithMeta(m map[string]any) Signal {
	s.Meta = m
	return s
}

// ScoreValue returns the signal's value as a 0..100 score. Non-score signals
// return 0, so a link equation over `score` is well-defined regardless of the
// source signal's type.
func (s Signal) ScoreValue() float64 {
	if s.Type == TypeScore {
		return s.Value
	}
	return 0
}

// Direction returns the canonical +1 / -1 / 0 for a DIRECTION signal, reading
// from Value. Non-directional signals return 0.
func (s Signal) Direction() int {
	if s.Type != TypeDirection {
		return 0
	}
	if s.Value > 0 {
		return 1
	}
	if s.Value < 0 {
		return -1
	}
	return 0
}

// String implements fmt.Stringer for log/UI convenience.
func (s Signal) String() string {
	b, err := json.Marshal(s)
	if err != nil {
		return "<signal>"
	}
	return string(b)
}
