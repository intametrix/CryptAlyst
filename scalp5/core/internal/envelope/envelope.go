// Package envelope is the fixed shape every message in CryptAlyst travels in.
//
// Thinly adapted from the ATLAS design: data + meta + references, a version
// policy that accepts the current revision and the one before it, and a decode
// that refuses a top-level member the contract does not define while ignoring
// unknown meta keys (meta is extensible, payload is not).
package envelope

import (
	"encoding/json"
	"fmt"
	"time"
)

const (
	CurrentVersion = 1
	MinVersion     = 1
)

// Plane separates market data from everything derived from it. Market data is
// delivered first and never waits on the compute plane.
type Plane string

const (
	PlaneMarket  Plane = "market"
	PlaneIntel   Plane = "intel"
	PlaneModel   Plane = "model"
	PlaneControl Plane = "control"
)

// Hop is one stage an envelope passed through: an interceptor that checked it,
// an operator that produced it, or a transformer that changed its shape. The
// pipeline is part of the record, not a log line beside it.
type Hop struct {
	Kind    string `json:"kind"`    // interceptor | operator | transformer
	Name    string `json:"name"`    // the rule or capability
	Outcome string `json:"outcome"` // pass | refuse | modify
	Detail  string `json:"detail,omitempty"`
	MS      int64  `json:"ms,omitempty"`
}

// Ref points at another envelope without copying it.
type Ref struct {
	Topic      string `json:"topic"`
	Key        string `json:"key"`
	Generation uint64 `json:"generation,omitempty"`
	CID        string `json:"cid,omitempty"`
	// Derived marks a reference the recorder inferred from the window rather than
	// one the producer declared. Consumers must treat the two differently.
	Derived bool  `json:"derived,omitempty"`
	AgeMS   int64 `json:"age_ms,omitempty"`
}

// Meta describes provenance and freshness. Extensible by design.
type Meta struct {
	Version    int               `json:"version"`
	Plane      Plane             `json:"plane,omitempty"`
	Topic      string            `json:"topic,omitempty"`
	Key        string            `json:"key,omitempty"`
	Producer   string            `json:"producer,omitempty"`
	Generation uint64            `json:"generation,omitempty"`
	Sequence   uint64            `json:"seq,omitempty"`
	Count      int               `json:"count,omitempty"`
	Computing  bool              `json:"computing,omitempty"`
	ComputedAt *time.Time        `json:"computed_at,omitempty"`
	ReceivedAt *time.Time        `json:"received_at,omitempty"`
	ComputeMS  int64             `json:"compute_ms,omitempty"`
	CID        string            `json:"cid,omitempty"`
	ParentCID  string            `json:"parent_cid,omitempty"`
	Refs       []Ref             `json:"refs,omitempty"`
	Hops       []Hop             `json:"hops,omitempty"`
	Extra      map[string]string `json:"extra,omitempty"`
}

// Envelope is the universal carrier. T is the payload and is never a storage type.
type Envelope[T any] struct {
	Data T    `json:"data"`
	Meta Meta `json:"meta"`
}

func New[T any](data T, meta Meta) Envelope[T] {
	meta.Stamp()
	return Envelope[T]{Data: data, Meta: meta}
}

func (m *Meta) Stamp() {
	if m.Version == 0 {
		m.Version = CurrentVersion
	}
	if m.ReceivedAt == nil {
		now := time.Now().UTC()
		m.ReceivedAt = &now
	}
}

// AcceptVersion applies the N-1 read policy.
func AcceptVersion(v int) error {
	if v == 0 || (v >= MinVersion && v <= CurrentVersion) {
		return nil
	}
	if v > CurrentVersion {
		return fmt.Errorf("envelope: version %d is newer than this build reads (%d)", v, CurrentVersion)
	}
	return fmt.Errorf("envelope: version %d is older than this build reads (%d)", v, MinVersion)
}

// Decode validates the outer contract then the version policy.
func Decode[T any](raw []byte) (Envelope[T], error) {
	var top map[string]json.RawMessage
	if err := json.Unmarshal(raw, &top); err != nil {
		return Envelope[T]{}, fmt.Errorf("envelope: %w", err)
	}
	for key := range top {
		if key != "data" && key != "meta" {
			return Envelope[T]{}, fmt.Errorf("envelope: unknown member %q", key)
		}
	}
	var env Envelope[T]
	if data, ok := top["data"]; ok {
		if err := json.Unmarshal(data, &env.Data); err != nil {
			return Envelope[T]{}, fmt.Errorf("envelope: data: %w", err)
		}
	}
	if meta, ok := top["meta"]; ok {
		if err := json.Unmarshal(meta, &env.Meta); err != nil {
			return Envelope[T]{}, fmt.Errorf("envelope: meta: %w", err)
		}
	}
	if err := AcceptVersion(env.Meta.Version); err != nil {
		return Envelope[T]{}, err
	}
	return env, nil
}

// Transformer changes an envelope in flight: decorate, redact, reshape.
type Transformer[T any] func(Envelope[T]) Envelope[T]

// Pipe applies transformers left to right.
func Pipe[T any](env Envelope[T], ts ...Transformer[T]) Envelope[T] {
	for _, t := range ts {
		if t != nil {
			env = t(env)
		}
	}
	return env
}

// WithHop appends one pipeline stage, which is how the journey through
// interceptors, operators and transformers becomes visible on the envelope.
func (e Envelope[T]) WithHop(h Hop) Envelope[T] {
	e.Meta.Hops = append(e.Meta.Hops, h)
	return e
}

// WithRef appends a reference, which is how provenance accumulates.
func (e Envelope[T]) WithRef(r Ref) Envelope[T] {
	e.Meta.Refs = append(e.Meta.Refs, r)
	return e
}

// AgeMS is the freshness of the payload, which is what an engine refuses on.
func (e Envelope[T]) AgeMS(now time.Time) int64 {
	if e.Meta.ComputedAt == nil {
		if e.Meta.ReceivedAt == nil {
			return -1
		}
		return now.Sub(*e.Meta.ReceivedAt).Milliseconds()
	}
	return now.Sub(*e.Meta.ComputedAt).Milliseconds()
}
